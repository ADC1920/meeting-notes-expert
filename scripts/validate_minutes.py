#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_minutes.py — 结构化总结结构校验与源文核对（meeting-notes-expert 配套）

按会议版章节名校验，仅适用会议场景的会议纪要；讲座、访谈、播客等通用总结不强制。
做四件事：
  1) 结构：必需节齐备（会议结论/关键决策/待办事项/会议内容），按 minutes-schema.json 校验；
  2) 版式：待办表六列、优先级枚举合法、基本信息区不得是表格；
  3) 文风：正文不得出现「待确认/存疑/转写亦作」等禁用标注，决策不得写成「讨论了…」；
  4) 溯源（给了 --source 才做）：成稿中的数字必须出现在源材料；待办责任人必须出现在源材料。

用法：
    python validate_minutes.py 纪要.md [--source 会议原文.txt] [--strict] [--json 报告.json]
    python validate_minutes.py 纪要.docx [--source ...]
    python validate_minutes.py --selftest

退出码：0 通过；1 有 FAIL（--strict 时 WARN 也计 FAIL）；2 用法/读取错误。
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = TOOL_DIR.parent / "references" / "minutes-schema.json"
SECTIONS_PATH = TOOL_DIR.parent / "references" / "sections.json"


def _load_sections():
    """章节白名单从 references/sections.json 读，脚本内不再硬编码。

    v1.15.1 及以前，必填节判据在本文件里存了七份常量（REQUIRED / CONDITIONAL /
    GENERIC_REQUIRED / RETRO_REQUIRED / STANDUP_REQUIRED / LAYERED_REQUIRED /
    FORMAL_EXTRA），SKILL.md 的散文里还有一份。两处各写一套的后果实测过——
    同一份复盘稿，校验器按 RETRO 口径报 PASS，导出器按会议口径报 failed。
    现在单一真相源在 JSON，文档与脚本都引用它。

    文件缺失时退回内置兜底：校验器是交付前最后一道闸，不能在读不到配置时
    直接崩掉或静默放行。
    """
    try:
        data = json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))
        if data.get("kinds"):
            return data
    except (OSError, ValueError, KeyError):
        pass
    return _SECTIONS_FALLBACK


_SECTIONS_FALLBACK = {
    "kinds": {
        "meeting": {"label": "会议纪要", "required": ["会议结论", "会议内容"],
                    "conditional": ["关键决策", "待办事项", "无人认领事项",
                                    "风险与顾虑", "时间线", "分歧与转折条件"],
                    "optional": ["待定与开放问题", "上期行动项回顾"],
                    "action_section": "待办事项"},
        "formal": {"label": "正式会议", "extends": "meeting", "extra": ["法定要素"],
                   "action_section": "待办事项"},
        "retro": {"label": "复盘会",
                  "required": ["本次目标与结果", "做得好的", "待改进", "下轮改进项"],
                  "action_section": "下轮改进项", "detect_min": 3},
        "standup": {"label": "站会", "required": ["进度", "计划", "阻塞"],
                    "action_section": None},
        "generic": {"label": "通用总结", "required": ["核心结论", "关键要点", "内容详情"],
                    "optional": ["受访者原话", "解读与推断", "章节时间戳", "金句",
                                 "提及资源", "术语定义", "案例与例证", "引用与参考",
                                 "行动事项", "待定与开放问题", "上期事项回顾"],
                    "action_section": "行动事项",
                    "basic_fields": ["内容主题", "来源", "时间", "主讲人或参与人",
                                     "记录人", "内容类型", "时长"]},
        "layered": {"label": "会议纪要·完整分层版",
                    "markers": ["会议决策摘要", "关键议题与讨论", "会议结论与后续安排"],
                    "detect_min": 2,
                    "required": ["会议决策摘要", "关键议题与讨论", "行动项",
                                 "会议结论与后续安排"],
                    "optional": ["项目背景", "项目背景与建设内容", "项目或议题背景",
                                 "风险评估", "待决问题与风险", "意见与分歧"],
                    "action_section": "行动项"},
    },
    "subtype_optional": ["近期进展", "挑战与困难", "职业发展", "双向反馈",
                         "冲刺目标", "故事点与容量", "依赖与阻塞", "完成定义",
                         "设计决策", "用户反馈", "待迭代问题", "上线标准",
                         "状态概览", "已获批准", "升级事项", "下次同步"],
    "basic_fields": ["会议主题", "会议时间", "会议地点", "参会人员", "记录人",
                     "主持人", "会议方式", "会议编号", "会议形式",
                     "参会方", "会议性质", "参会单位", "缺席人员", "会议主持"],
    "containers": ["会议基本信息", "内容基本信息", "基本信息"],
}

_SECTIONS = _load_sections()
_KINDS = _SECTIONS["kinds"]


def _kind_required(kind):
    """某骨架的必填节；formal 继承 meeting 再追加专属节。"""
    cfg = _KINDS[kind]
    req = list(cfg.get("required", []))
    parent = cfg.get("extends")
    if parent:
        req = _kind_required(parent) + list(cfg.get("extra", []))
    return req


def _kind_action_section(kind):
    cfg = _KINDS[kind]
    if cfg.get("action_section"):
        return cfg["action_section"]
    parent = cfg.get("extends")
    return _kind_action_section(parent) if parent else None


# 常规会议的两节必填：结论（这次谈了什么）+ 内容（按议题展开）。
# 「关键决策」「待办事项」2026-11-22 起降为条件节——讨论型会议本来就不产出决策与任务，
# 强行要求这两节会让每个空节都长出一句占位话（本次真实素材：决策节 14 字占 0%）。
# 章节计划是上限不是配额，没内容就整节不出现。历史稿仍带这两节时照常校验，不追究。
REQUIRED = _kind_required("meeting")
CONDITIONAL = list(_KINDS["meeting"].get("conditional", []))
OPTIONAL = list(_KINDS["meeting"].get("optional", []))
ALL_SECTIONS = REQUIRED + CONDITIONAL + OPTIONAL
GENERIC_REQUIRED = _kind_required("generic")
GENERIC_OPTIONAL = list(_KINDS["generic"].get("optional", []))
GENERIC_SECTIONS = GENERIC_REQUIRED + GENERIC_OPTIONAL
GENERIC_BASIC_OK = set(_KINDS["generic"].get("basic_fields", []))
FORMAL_EXTRA = list(_KINDS["formal"].get("extra", []))
RETRO_REQUIRED = _kind_required("retro")
STANDUP_REQUIRED = _kind_required("standup")
SUBTYPE_OPTIONAL = list(_SECTIONS.get("subtype_optional", []))
MEETING_SUB_SECTIONS = (FORMAL_EXTRA + RETRO_REQUIRED + STANDUP_REQUIRED
                        + SUBTYPE_OPTIONAL)
LAYERED_MARKERS = tuple(_KINDS["layered"].get("markers", []))
LAYERED_REQUIRED = _kind_required("layered")
LAYERED_OPTIONAL = list(_KINDS["layered"].get("optional", []))
FORBIDDEN = ["待确认", "存疑", "转写亦作", "不确定标注"]
# 「未提供」「材料未提及」与「待确认」同属禁用标注：材料缺就整行省略，不交代缺口。
# 「材料未记录」不在此列——它是结构性槽位（法定人数/表决票数/复盘原定目标）的合法写法。
FORBIDDEN_PHRASES = ["未提供", "材料未提及", "材料未说明"]
# 相对时间：待办完成时间必须换算成具体日期（SKILL.md「相对时间换算」）。
# 会议时间缺失时无从换算，放行不判——硬算等于编造。
RELATIVE_TIME_RE = re.compile(
    r"(?:本周|下周|这周)[一二三四五六日天]|[周星期][一二三四五六日天]前|月底前|下月初")
DATE_RE = re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]")
RISK_TIERS = {"可行", "待补", "待核", "不可行"}
# 待办表两套列序：七列（现行，含状态）与六列（v1.12.0 及以前的历史件）。
# 六列件不判缺状态，只告警提示补——历史归档件重跑校验不该集体报错。
ACTION_COLUMNS_7 = ["序号", "待办事项", "责任人/责任部门", "完成时间", "优先级", "状态", "备注"]
ACTION_COLUMNS_6 = ["序号", "待办事项", "责任人/责任部门", "完成时间", "优先级", "备注"]
# 正式会议在七列中间插一列「对应动议」——插在状态之后、备注之前，
# 这样七列件的列序在两种表里保持一致，读者不必重新适应。
ACTION_COLUMNS_FORMAL = ["序号", "待办事项", "责任人/责任部门", "完成时间", "优先级",
                         "状态", "对应动议", "备注"]
ACTION_COLUMNS = ACTION_COLUMNS_7
ACTION_COLUMN_SETS = (ACTION_COLUMNS_7, ACTION_COLUMNS_6, ACTION_COLUMNS_FORMAL)
ACTION_FIELD_MAP = {"序号": "id", "待办事项": "task", "责任人/责任部门": "owner",
                    "责任人": "owner", "完成时间": "due", "优先级": "priority",
                    "状态": "status", "备注": "note"}
PRIORITIES = {"高", "中", "低", "待定"}
# 状态五态 + 待定：跨会跟踪靠它区分「还没做」与「做完了」。
STATUSES = {"未开始", "进行中", "受阻", "完成", "取消", "待定"}
# 表述强度：软承诺入表但不升格为确定任务（SKILL.md「四类产出物归属」）。
STRENGTHS = {"确定", "软性"}
# 待办出处：备注列里以「出处：00:35 张三」或「出处：会议原文引文」形式标注。
# 用备注列承载而不新增第八列——八列在 A4 可用宽 15.92cm 下每列不足 2cm，内容频繁换行。
SOURCE_RE = re.compile(r"出处[：:]\s*([^；;|]+)")
# 表述强度写在备注列（「强度：软性」），不新增第八列——理由同出处。
STRENGTH_RE = re.compile(r"强度[：:]\s*([^\s；;|、，。]+)")
# 正式会议的待办需回指所执行的动议编号（SKILL.md「正式会议法定要素节」）。
MOTION_RE = re.compile(r"动议\s*([0-9]+|[一二三四五六七八九十]+)")
# 通用总结的行动事项表沿用同一套列（访谈/播客常无状态，缺失只提示不判错）。
GENERIC_TIMESTAMP_RE = re.compile(r"^\s*(\d{1,2}):([0-5]\d)")
# 术语定义句式：讲座里引入术语时「X 是指」「所谓 X」「X 的定义是」。
DEFINITION_RE = re.compile(r"(?:所谓\s*([^\s，。；：]{2,12})|([^\s，。；：]{2,12})\s*(?:是指|定义为|的定义是))")
BASIC_FIELDS_OK = set(_SECTIONS.get("basic_fields", []))
HEADING_RE = re.compile(r"^##(?!#)\s+(.+)$")
HEADING_STYLE_RE = re.compile(r"^(?:Heading|标题)\s*(\d+)$")
# 章节标题前的编号前缀：「一、」「（一）」「第 1 章」「1.」。
# 旧版 HEADING_RE 精确匹配章节名，带编号的标题一律认不出——而 templates.md 的
# 分层版示例自己就写着「一、会议决策摘要」，照抄模板即报未识别章节 + 缺节（实测）。
# formatting.md 明令「章节标题不写编号」（由 Word 自动编号），但用户手写带编号极常见，
# 校验器必须宽容：剥掉前缀再比对，编号本身不进任何校验。
HEADING_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*[章节部分]\s*[、.．:：]?\s*"
    r"|[（(]\s*[0-9一二三四五六七八九十百]+\s*[）)]\s*"
    r"|[0-9]{1,2}\s*[、.．]\s+"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]\s*)")


def strip_heading_number(name):
    """剥掉章节标题的编号前缀，返回纯章节名。

    「一、会议决策摘要」→「会议决策摘要」；「1.2 本次核心结论」→「本次核心结论」。
    只剥标题开头、且只在后面确实还有字时剥——避免把「一」这种真章节名剥成空串。
    """
    s = (name or "").strip()
    m = HEADING_NUM_PREFIX_RE.match(s)
    if m:
        rest = s[m.end():].strip()
        if rest:
            return rest
    # 「1.2 本次核心结论」这类多级编号：剥掉前导的 1.2 / 1.2.3
    m = re.match(r"^\d+(?:\.\d+)+\s+(\S.*)$", s)
    if m:
        return m.group(1).strip()
    return s
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s+")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*\s*[%％]?")
# 中文数字：纪要常把「百分之五」「两万亿」写成汉字，只比对阿拉伯数字会漏掉它们，
# 于是中文数字稿的「数字溯源」永远报 0 个、等于这项校验整个失效（实测踩到）。
CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUM_SEQ = re.compile(r"[零一二三四五六七八九十百千万亿两]{1,6}")


def cn_to_values(seq):
    """把一段中文数字串展开成可能的阿拉伯数字集合（保守展开，宁宽不漏）。

    只处理会议记录里真正会出现的两类：
      - 确定值：「二十」→20、「十六」→16、「三百」→300
      - 模糊相邻：「十五六」→15/16、「十三四」→13/14
    宁宽不漏的理由：本指标要抓的是「原文根本没提过这个数」，「十五六块」写成
    「15 元至 16 元」是忠实整理而非编造，误判会让人不信这项校验。
    展开也必须收窄——早期版本把「十五六」也展开出 50/55/56，等于放水。
    """
    out = set()
    if not seq:
        return out
    m = re.fullmatch(r"([一二三四五六七八九两])?十([一二三四五六七八九两])?", seq)
    if m:
        tens = CN_DIGIT[m.group(1)] if m.group(1) else 1
        out.add(tens * 10 + CN_DIGIT[m.group(2)] if m.group(2) else tens * 10)
        return out
    m = re.fullmatch(r"([一二三四五六七八九两])?([百千万亿])", seq)
    if m:
        base = CN_DIGIT[m.group(1)] if m.group(1) else 1
        out.add(base * {"百": 100, "千": 1000, "万": 10000, "亿": 100000000}[m.group(2)])
        return out
    # 复合大单位：「两万亿」= 2 万亿 = 2e12，「十几万亿」同理，「八千亿」= 8000 亿。
    # 不处理这一段，后面按位兜底会把「两万亿」解成 ['2']，原文真有这个数也匹配不上
    # （实测：稿件写「两万亿」被判成编造）。
    if "万" in seq or "亿" in seq:
        total = 0
        rest = seq
        for unit, factor in (("亿", 100000000), ("万", 10000)):
            idx = rest.find(unit)
            if idx < 0:
                continue
            # 单位前的那一截本身可以是「二十」「八百」这类，交给本函数递归解析
            head = rest[:idx] or "一"
            head_vals = cn_to_values(head)
            if len(head_vals) == 1:
                total += head_vals.pop() * factor
                rest = rest[idx + 1:]
        if total:
            out.add(total)
            return out
    if len(seq) == 1 and seq in CN_DIGIT:
        out.add(CN_DIGIT[seq])
        return out
    vals = [CN_DIGIT[c] for c in seq if c in CN_DIGIT]
    if vals:
        out.update(vals)
        if "十" in seq:
            for i, c in enumerate(seq):
                if c != "十":
                    continue
                prev = CN_DIGIT.get(seq[i - 1]) if i > 0 else 1
                prev = 1 if prev is None else prev
                out.add(prev * 10)
                if i + 1 < len(seq):
                    nxt = CN_DIGIT.get(seq[i + 1])
                    if nxt is not None:
                        out.add(prev * 10 + nxt)
                        if i + 2 < len(seq):
                            n2 = CN_DIGIT.get(seq[i + 2])
                            if n2 is not None:
                                out.add(prev * 10 + n2)
            ten_at = seq.index("十")
            for h in [CN_DIGIT[c] for c in seq[:ten_at] if c in CN_DIGIT]:
                out.add(h * 10)
        if len(vals) == 2 and "十" not in seq:
            out.add(vals[0] * 10 + vals[1])
    return out


def cn_numbers(text):
    """文本里出现的所有中文数字可能取值（字符串形式，与阿拉伯数字同口径归一）。

    切串时把数字后紧邻的大单位一并纳入：「两万多亿」里「多」隔断了数字串，
    只切到「两万」会展开成 20000、丢掉后面的「亿」，而原文「两万亿」展开是 2e12，
    两者明明同一数量级却对不上（实测踩到）。

    两侧必须用同一函数：稿件写「两万多亿美元」、源文写「两万亿」时，
    只要都经过这里展开，差集才对得上。
    """
    out = set()
    t = text or ""
    # 数字串可选跟一个「多/余/来」再接大单位，把整段当一个数处理
    pattern = r"[零一二三四五六七八九十百千万亿两]{1,8}(?:[多余来][百千万亿])?"
    for m in re.finditer(pattern, t):
        seq = m.group(0)
        core = re.sub(r"[多余来]", "", seq)
        for v in cn_to_values(core):
            out.add(str(v))
        # 去掉末尾单位后的前缀也收：与源文只写「两万」的形态对齐
        for v in cn_to_values(re.sub(r"[万亿]+$", "", core)):
            out.add(str(v))
        # 后缀子串（源文侧可能出现更短的写法）
        for i in range(1, len(core)):
            for v in cn_to_values(core[i:]):
                out.add(str(v))
    return out
OWNER_PLACEHOLDERS = {"待定", "未指定", "全体", "大家", "—", "-"}
# 责任人是部门/单位而非个人时的后缀。这类值不进人名溯源——会议原文从不出现
# 「财务部」三个字（说的是「财务那边」），按人名核对必然误报；说话人归属同理跳过。
ORG_SUFFIX_LIST = ("部", "组", "科", "司", "处", "室", "中心", "团队", "委员会")


# ---------- 解析 ----------
def parse_md(text):
    blocks, table = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and all(set(c) <= set("-: ") for c in cells):
                continue
            if table is None:
                table = []
                blocks.append(("t", table))
            table.append(cells)
            continue
        table = None
        blocks.append(("p", line))
    return blocks


def parse_docx(path):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = para.text
            # article-format 出稿后标题是样式不是字面 `##`，这里按样式还原成
            # Markdown 标题，让 split_sections 的同一套规则同时吃 md 与 docx。
            style = (para.style.name or "").strip() if para.style is not None else ""
            m = HEADING_STYLE_RE.match(style)
            if m:
                text = "#" * (int(m.group(1)) + 1) + " " + text
            elif style == "Title":
                text = "# " + text
            blocks.append(("p", text))
        elif child.tag == qn("w:tbl"):
            rows = [[c.text.strip() for c in r.cells] for r in Table(child, doc).rows]
            blocks.append(("t", rows))
    return blocks


def split_sections(blocks):
    basic, sections, basic_table = {}, {}, False
    cur = None
    known = (set(ALL_SECTIONS) | set(GENERIC_SECTIONS) | set(MEETING_SUB_SECTIONS)
             | set(LAYERED_REQUIRED) | set(LAYERED_OPTIONAL))
    # 未识别的二级标题：必须记下来，不能静默丢弃。
    # 实测过的漏检形态：整篇只有一节被换成外来名字（如把「待定与开放问题」写成「风险分析」），
    # 这段内容会落进基本信息区，而「待定与开放问题」本身是可选节，于是既没缺节告警、
    # 也看不出这一节被吞了——校验器报 PASS，只留一条莫名其妙的「非常规字段」提示。
    # 全部章节都写错时反而能被「必需节缺失」抓住，只有单节写错才是静默漏检。
    # 「会议基本信息」是基本信息区的容器标题，不是内容节，不能算未识别。
    HEADING_CONTAINERS = set(_SECTIONS.get("containers", [])) or {
        "会议基本信息", "内容基本信息", "基本信息"}
    unknown = []
    for kind, payload in blocks:
        if kind == "p":
            text = payload.strip()
            m = HEADING_RE.match(text)
            if m:
                raw_name = m.group(1).strip()
                name = strip_heading_number(raw_name)
                # 通用总结的章节名也要认成节，否则「## 核心结论」会被当成基本信息区的
                # 后续文本吞掉，类型判定永远命不中（实测：访谈总结被判成缺四个会议节）
                cur = name if name in known else None
                if cur:
                    sections.setdefault(cur, [])
                elif raw_name in HEADING_CONTAINERS or name in HEADING_CONTAINERS:
                    # 容器标题（会议基本信息）不记未识别；它带编号时同样放行
                    pass
                else:
                    unknown.append(raw_name)
                continue
        if cur is None:
            if kind == "t":
                basic_table = True
            elif not payload.lstrip().startswith("#"):
                m = re.match(r"^([^：:]{2,12})[：:]\s*(.+)$", payload.strip())
                if m:
                    basic[m.group(1).strip()] = m.group(2).strip()
        else:
            sections[cur].append((kind, payload))
    return basic, sections, basic_table, unknown


def section_text(entries):
    out = []
    for kind, payload in entries:
        if kind == "p":
            out.append(payload)
        else:
            out.extend(" | ".join(r) for r in payload)
    return "\n".join(out).strip()


def _parse_meeting_date(text, default_year=None):
    """从文本解析出日期，解析不到返回 None。

    「11 月 20 日」这类不带年份的写法用 default_year 补齐——纪要正文按惯例不写年份，
    不补的话每个日期都解析成 None，日期溯源会全盘误报（实测）。补出的日期若比
    参照日早 60 天以上，按跨年处理（会议在 12 月说「1 月 5 日」应是次年）。
    """
    if not text:
        return None
    m = re.search(r"(\d{4})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})", text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if default_year is None:
        return None
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", text)
    if not m:
        return None
    try:
        return datetime.date(default_year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _strip_dates(text):
    """剥掉日期，避免换算得到的日期数字被当成编造。只剥完整年月日与「X 月 Y 日」——
    不剥裸「X 月」：「3 个月内完成」是时长不是日期，剥掉会漏掉真编造。"""
    return DATE_RE.sub(" ", text)


def _extract_dates(text, basic):
    """收集稿中出现的日期，返回 [(原文写法, 解析出的 date 或 None)]。

    用于「日期溯源」：日期要么在源文里出现过，要么能由会议时间与相对表述换算得出。
    不带年份的写法用会议时间的年份补齐——纪要正文按惯例不写年份。
    """
    base = _parse_meeting_date((basic or {}).get("会议时间", ""))
    year = base.year if base else None
    found = []
    for m in DATE_RE.finditer(text):
        raw = m.group(0)
        found.append((raw, _parse_meeting_date(raw, default_year=year)))
    return found


def _derivable_from_basic(target, basic):
    """日期能否由会议时间合理换算得出：同月内的月末、次月初、或会议后 30 天内。

    这是宽松兜底——相对表述的精确换算由人做，校验器只挡住明显凭空编的远期日期
    （会期在 11 月却写「12 月 30 日」这种）。范围取 30 天是因为「月底前」「下月初」
    「两周内」这类表述都落在会期后一个月内。
    """
    base = _parse_meeting_date((basic or {}).get("会议时间", ""))
    if not base:
        return False
    return base <= target <= base + datetime.timedelta(days=30)


def _first_table(entries):
    """取节内第一张表，返回 (数据行, 表头)。无表时返回 ([], [])。"""
    for kind, payload in entries:
        if kind == "t" and payload:
            header = [c.strip() for c in payload[0]]
            rows = [[c.strip() for c in r] for r in payload[1:]]
            return rows, header
    return [], []


def collect_action_items(entries):
    """取待办节的第一张表，按表头名映射成字段。

    六列旧件（无状态）与七列新件都认：按表头文字映射，不按位置硬取，
    这样列序调整或加列不会静默错位。列数不对时仍返回，让列名断言报出来。
    """
    rows, header = _first_table(entries)
    if not header:
        return [], []
    keys = [ACTION_FIELD_MAP.get(h, "") for h in header]
    items = []
    for cells in rows:
        item = {k: "" for k in ("id", "task", "owner", "due", "priority",
                                "status", "note", "source", "strength")}
        for i, key in enumerate(keys):
            if key and i < len(cells):
                item[key] = cells[i]
        # 出处与表述强度写在备注列，解析后单独取出供溯源、跨会查询与强度机检使用
        m = SOURCE_RE.search(item["note"] or "")
        item["source"] = (m.group(1).strip() if m else "")
        m2 = STRENGTH_RE.search(item["note"] or "")
        item["strength"] = (m2.group(1) if m2 else "")
        items.append(item)
    return items, header


def _has_motion_column(header):
    """正式会议：备注列外允许出现「对应动议」类列，判据是表头含「动议」。"""
    return any("动议" in h for h in header)


def _schema_item(it):
    """待办行转 schema 载荷：空的可选字段不下发，避免 enum 被空串打穿。

    导出器与 schema 校验都走这一个函数。导出器曾经直接把 collect_action_items
    的原始行下发，那些行里 status/strength/source 是空字符串而不是缺键，
    schema 的 enum 一碰到 '' 就报错（实测：六类骨架全部 schema 不合规）。
    """
    out = {k: it[k] for k in ("id", "task", "owner", "due", "priority", "note")
           if it.get(k) not in (None, "")}
    for k in ("status", "strength", "source"):
        if it.get(k):
            out[k] = it[k]
    return out


def norm_anchor(s):
    """出处锚点与原文比对前的归一化：去空白与标点。

    原文是 `[00:20] 李四：…`，锚点写 `00:20 李四`——差一对括号和一个冒号。
    不归一化就永远匹配不上，出处溯源会全盘误报（实测）。
    """
    return re.sub(r"[\s\[\]()（）【】:：，。、；;|—\-]", "", s or "")


def collect_decisions(entries):
    lines = []
    for kind, payload in entries:
        if kind != "p":
            continue
        text = payload.strip()
        if text:
            lines.append(LIST_RE.sub("", text).strip())
    return [x for x in lines if x]


def split_decision_source(line):
    """把决策条目里以「出处：…」结尾的锚点拆出来，返回 (正文, 出处)。

    决策正文按 SKILL.md 不加任何出处标注；锚点只用于 JSON 导出。
    若作者在 docx 里手写了「（出处：00:35）」且希望保留可读性，这里仍拆走——
    正文不带依据是硬口径。
    """
    m = re.search(r"[（(]?\s*出处[：:]\s*([^）)]+)[）)]?\s*$", line)
    if not m:
        return line.strip(), ""
    return line[:m.start()].strip(), m.group(1).strip()


# ---------- 校验 ----------
def read_text_any(path):
    """读文本文件，按 UTF-8 → UTF-8-SIG → GB18030 → UTF-16 依次尝试。

    纯 UTF-8 解码失败就掉进 docx 分支，会让 GBK/UTF-16 的 md 报成
    「PackageNotFoundError：无法解析 md/docx」——错误信息指向 docx 损坏，
    真实原因是编码不对，排障方向被带偏（实测）。这里先把编码试遍，
    全都失败才抛 UnicodeDecodeError，由上层给出「编码不对」的明确提示。
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # 有 UTF-16 BOM 的，先按 UTF-16 解，别让 UTF-8 拿 BOM 字节去撞运气
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, min(len(raw), 1),
                             "无法按 utf-8/gb18030/utf-16 解码")


def parse_input(path):
    """按后缀选解析器，返回 blocks。

    .md/.txt 走文本解析；其余（.docx）走 docx 解析。文本解析失败时错误
    信息必须点明是编码问题，不能落到 docx 分支上（那是误导）。
    """
    if path.suffix.lower() in (".md", ".markdown", ".txt"):
        try:
            text = read_text_any(path)
        except UnicodeDecodeError:
            raise ValueError(
                f"文本编码无法识别：{path.name} 不是 UTF-8/GBK/UTF-16 任一可识别编码，"
                f"请转存为 UTF-8 后重试")
        return parse_md(text)
    try:
        return parse_docx(path)
    except Exception as e:
        raise ValueError(f"无法解析 docx：{type(e).__name__}: {e}")


def detect_kind(sections):
    """判定骨架类型，返回 kind 键（见 references/sections.json 的 kinds）。

    校验与导出共用这一份判据。v1.15.1 及以前，validate() 里按骨架选 need，
    export_structured() 里却写死 REQUIRED（会议版），于是同一份复盘稿
    校验报「PASS 必需节齐备 [复盘会]」、导出报「状态 failed」——两套判据必然漂移（实测）。
    """
    is_retro = all(k in sections for k in _kind_required("retro")[:3])
    is_standup = ("进度" in sections and "计划" in sections and "阻塞" in sections
                  and not is_retro)
    markers = _KINDS["layered"].get("markers", [])
    need_m = _KINDS["layered"].get("detect_min", 2)
    is_layered = sum(1 for k in markers if k in sections) >= need_m
    if is_retro or is_standup or is_layered:
        if is_retro:
            return "retro"
        if is_standup:
            return "standup"
        return "layered"
    if any(k in sections for k in
           ("核心结论", "关键要点", "内容详情", "受访者原话",
            "章节时间戳", "术语定义")):
        return "generic"
    if any(k in sections for k in FORMAL_EXTRA):
        return "formal"
    return "meeting"


def validate(path, source_text=None, strict=False):
    results = []

    def ok(name, detail=""):
        results.append(("PASS", name, detail))

    def bad(name, detail=""):
        results.append(("FAIL", name, detail))

    def warn(name, detail=""):
        results.append(("WARN", name, detail))

    try:
        blocks = parse_input(path)
    except ValueError as e:
        print(f"读取失败：{e}")
        # 回一条 FAIL 而不是空列表：空列表会让 summarize 判成 complete，
        # 于是读取失败被打印成「状态：complete」（实测）。退出码仍是 2。
        return 2, [("FAIL", "读取文件", str(e))]
    except OSError as e:
        print(f"读取失败：{e}")
        return 2, [("FAIL", "读取文件", f"文件无法读取：{type(e).__name__}: {e}")]

    basic, sections, basic_table, unknown_headings = split_sections(blocks)
    # 类型判定：复盘/站会/分层版有各自独有的必填节，通用总结靠专属节名识别，
    # 都不命中才按常规会议（正式会议是常规 + 法定要素节）。判据与导出器共用 detect_kind。
    kind = detect_kind(sections)
    is_retro = kind == "retro"
    is_standup = kind == "standup"
    is_layered = kind == "layered"
    is_generic = kind == "generic"
    is_formal = kind == "formal"
    if basic_table:
        bad("基本信息表形态", "基本信息区使用了表格，规范要求逐行「字段名：内容」")
    # 未识别的二级标题：这是「模板没按 references/templates.md 走」唯一的机检信号。
    # 判 WARN 不判 FAIL：附录/致谢这类标题本就允许存在，硬判 FAIL 会误伤；
    # 但必须显式列出来——静默吞掉会让「这一节整段不见」以 PASS 状态交到用户手里（实测）。
    if unknown_headings:
        warn("未识别章节", "以下二级标题不在模板章节表内，其内容未被纳入任何校验："
             + "、".join(unknown_headings[:5]))
    else:
        ok("未识别章节", "全部二级标题均在模板章节表内")
    if not basic:
        warn("基本信息", "未解析到字段行（材料可能未提供基本信息）")
    else:
        ok_fields = GENERIC_BASIC_OK if is_generic else BASIC_FIELDS_OK
        unknown = [k for k in basic if k not in ok_fields]
        (warn if unknown else ok)("基本信息字段", "非常规字段：" + "、".join(unknown) if unknown else "")

    if is_retro:
        need, kind_label = RETRO_REQUIRED, _KINDS["retro"]["label"]
    elif is_standup:
        need, kind_label = STANDUP_REQUIRED, _KINDS["standup"]["label"]
    elif is_layered:
        need, kind_label = LAYERED_REQUIRED, _KINDS["layered"]["label"]
    elif is_generic:
        need, kind_label = GENERIC_REQUIRED, _KINDS["generic"]["label"]
    else:
        need = _kind_required("formal" if is_formal else "meeting")
        kind_label = _KINDS["formal" if is_formal else "meeting"]["label"]
    missing = [s for s in need if not section_text(sections.get(s, []))]
    if missing:
        bad("必需节齐备", f"[{kind_label}] 缺：" + "、".join(missing))
    else:
        ok("必需节齐备", f"[{kind_label}] " + "/".join(need) + " 均在")

    # 禁用标注必须扫全篇：只扫四个必填节正文时，把「会议时间：待确认」写进基本信息区、
    # 或另起一个 ## 附录 放不确定表述，都能绕过（实测）
    body = "\n".join(section_text(v) for v in sections.values())
    full_text = body + "\n" + "\n".join(f"{k}：{v}" for k, v in basic.items())
    hits = [w for w in FORBIDDEN if w in full_text]
    if hits:
        bad("禁用标注", "全文出现：" + "、".join(hits))
    else:
        # 禁用短语只扫基本信息与各节正文；「材料未记录」是结构性槽位的合法写法，不在禁用表里。
        phrase_hits = [w for w in FORBIDDEN_PHRASES if w in full_text]
        if phrase_hits:
            warn("缺失写法", "出现「" + "、".join(phrase_hits) + "」：材料缺就整行省略，结构性槽位写「材料未记录」")
        else:
            ok("禁用标注", "无待确认/存疑类标注")

    decisions = collect_decisions(sections.get("关键决策", []))
    if is_layered:
        # 完整分层版把决策写进「会议决策摘要」的 1.2 节，不在独立章节里。
        # 只验这一节非空且不出现讨论句式——分节层级由模板负责，校验器不解析 1.1/1.2 编号。
        summary_entries = collect_decisions(sections.get("会议决策摘要", []))
        bad_lines = [d for d in summary_entries if re.match(r"^(讨论|研究|考虑)了", d)]
        if not summary_entries:
            bad("决策摘要", "「会议决策摘要」为空，分层版必须写明本次核心结论")
        elif bad_lines:
            bad("决策摘要句式", "以下条目是讨论句式：" + "；".join(bad_lines[:3]))
        else:
            ok("决策摘要", f"{len(summary_entries)} 条记录，无讨论句式")
    elif is_retro or is_standup:
        # 复盘与站会骨架里没有「关键决策」节：复盘通常不表决，站会本来就不做决策。
        # 这里不判缺节，但仍扫一遍待改进/进度条目里的讨论句式，防止把过程写成结论。
        scan_key = "待改进" if is_retro else "进度"
        entries = collect_decisions(sections.get(scan_key, []))
        bad_lines = [d for d in entries if re.match(r"^(讨论|研究|考虑)了", d)]
        if bad_lines:
            bad("句式", f"[{scan_key}] 以下条目是讨论句式：" + "；".join(bad_lines[:3]))
        else:
            ok("句式", f"[{scan_key}] {len(entries)} 条无讨论句式")
    elif is_generic:
        # 通用总结没有「关键决策」节，决策句式无从检查；但「以讨论句式冒充结论」
        # 的毛病在「关键要点」里同样会出现，所以仍扫一遍要点条目。
        points = collect_decisions(sections.get("关键要点", []))
        bad_lines = [d for d in points if re.match(r"^(讨论|研究|考虑)了", d)]
        if bad_lines:
            bad("要点句式", "以下条目是讨论句式而非结论：" + "；".join(bad_lines[:3]))
        else:
            ok("要点句式", f"{len(points)} 条要点无讨论句式")
    elif "关键决策" not in sections:
        # 无决策时整节删除是合法形态（2026-11-22 起）：章节计划是上限不是配额，
        # 讨论型会议本来就不产出决策，留一个空标题只是占位噪音（对照 silverstein/minutes
        # 「空列表直接省略」与 blaise「THE PLAN IS A MAXIMUM, NOT A QUOTA」）。
        # 但「未形成决议」这件事必须被读到——要求「会议结论」首段显式说明，
        # 这样漏检（真发生了决策却没写）与合理缺省仍可区分。
        conclusion = section_text(sections.get("会议结论", []))
        # 允许中间夹字：「未就任何一项形成正式决议」「未形成任何决议」「无决策」都该命中。
        if re.search(r"(未就|未形成|没有形成|未能形成)[^。；\n]{0,20}(决议|决策)|无[^。；\n]{0,6}(决议|决策)", conclusion):
            ok("关键决策", "无决策节，「会议结论」已声明未形成决议")
        else:
            warn("关键决策", "缺「关键决策」节且「会议结论」未声明未形成决议："
                             "确认确实无决策，或补上决策条目")
    elif not decisions:
        bad("关键决策", "节内无条目（未形成决策时应整节删除，或写「本次会议未形成决策」）")
    elif re.search(r"(未形成|没有形成|无)(任何)?(关键)?决策", "".join(decisions)):
        ok("关键决策", "声明未形成决策")
    else:
        bad_lines = [d for d in decisions if re.match(r"^(讨论|研究|考虑)了", d)]
        if bad_lines:
            bad("决策句式", "以下条目是讨论句式而非结论：" + "；".join(bad_lines[:3]))
        else:
            ok("决策句式", f"{len(decisions)} 条均为肯定表述")

    if is_formal:
        # 正式会议多一个法定要素节：只要求它存在且不是空壳，不逐项验票数格式
        # （各组织章程字段写法差异大，硬套格式会误判）。
        body_f = section_text(sections.get("法定要素", []))
        if "材料未记录" in body_f or len(body_f.strip()) >= 20:
            ok("法定要素", "本节已填写")
        else:
            bad("法定要素", "本节内容过短，法定要素需含法定人数/表决结果等实际记录")

    if is_layered:
        # 分层版的行动项表列名与默认待办表同源，但不要求必须有行——方案评审类常常
        # 只有「各方补材料」这一两件事，空表加说明也合法。
        items, header = collect_action_items(sections.get("行动项", []))
        if header and header not in ACTION_COLUMN_SETS:
            bad("行动项列名", "应为 " + "/".join(ACTION_COLUMNS_7) + "，实为 " + "/".join(header))
        elif header:
            ok("行动项列名", f"{len(header)} 列且顺序正确（{len(items)} 行）")
        else:
            warn("行动项", "本节未解析到表格（分层版建议用行动项表呈现行动项）")
        # 风险评估的「判断」列只允许四档：SKILL.md 写死了取值，但不校验就等于没约束，
        # 第五种写法（「存疑」「不确定」「较好」）会悄悄流进对外交付稿。
        risk_rows, risk_header = _first_table(sections.get("风险评估", []))
        if not risk_header:
            warn("风险评估", "本节未解析到表格（分层版建议用「维度/判断/依据」三列表）")
        elif "判断" not in risk_header:
            bad("风险评估表头", "缺「判断」列，实为 " + "/".join(risk_header))
        else:
            idx = risk_header.index("判断")
            wrong = [r[idx] for r in risk_rows
                     if idx < len(r) and r[idx].strip() and r[idx].strip() not in RISK_TIERS]
            if wrong:
                bad("风险评估判断", "取值不在 可行/待补/待核/不可行 内：" + "、".join(wrong[:5]))
            else:
                ok("风险评估判断", f"{len(risk_rows)} 行均取自四档")
    elif is_retro:
        # 复盘的核心是「待改进」能对上下轮改进项；两者其一为空说明骨架没走完。
        items, header = collect_action_items(sections.get("下轮改进项", []))
        if not items:
            warn("下轮改进项", "本节无表格行（无改进项时应写明「本次未形成改进项」）")
        elif header not in ACTION_COLUMN_SETS:
            bad("改进表列名", "应为 " + "/".join(ACTION_COLUMNS_7) + "，实为 " + "/".join(header))
        else:
            ok("改进表列名", f"{len(header)} 列且顺序正确（{len(items)} 行）")
    elif is_standup:
        # 站会不要求表格，但阻塞节必须存在（「没有人被卡住」本身是有效信息）
        items, header = [], None
        ok("站会阻塞", "阻塞节已写")
    elif is_generic:
        # 通用总结的行动事项用同一张表；没有行动时允许写明「未形成行动事项」。
        # 不在这里硬判必须有行动——访谈与讲座常常没有行动项。
        items, header = collect_action_items(sections.get("行动事项", []))
        if header and header not in ACTION_COLUMN_SETS:
            bad("行动表列名", "应为 " + "/".join(ACTION_COLUMNS_7) + "，实为 " + "/".join(header))
        elif header:
            ok("行动表列名", f"{len(header)} 列且顺序正确")
        else:
            ok("行动表", "本节无表格（通用总结允许无行动事项）")
    else:
        items, header = collect_action_items(sections.get("待办事项", []))
        if header:
            if header in ACTION_COLUMN_SETS:
                ok("待办表列名", f"{len(header)} 列且顺序正确")
            else:
                bad("待办表列名", "应为 " + "/".join(ACTION_COLUMNS_7) + "，实为 " + "/".join(header))
        elif "待办事项" not in sections:
            # 无待办时整节缺失是合法形态（同「关键决策」）：讨论型会议常无任何承诺动作。
            ok("待办表", "无待办节（本次会议未形成行动事项）")
        elif "未形成行动事项" in section_text(sections.get("待办事项", [])):
            ok("待办表", "声明未形成行动事项")
        else:
            bad("待办表", "待办节既无表格，也无「未形成行动事项」声明")

    if items:
        for i, it in enumerate(items, 1):
            if not it["task"]:
                bad(f"待办第{i}行", "事项为空")
            if not it["owner"]:
                bad(f"待办第{i}行", "责任人列缺失（无责任人应填「待定」）")
            if not it["due"]:
                bad(f"待办第{i}行", "完成时间列缺失（未定时应填「待定」）")
            if it["priority"] not in PRIORITIES:
                bad(f"待办第{i}行", f"优先级「{it['priority']}」不在 高/中/低/待定 内")
            # 状态列只在表里确实有状态列时才判——六列旧件没有这一列，判它等于逼历史件返工
            has_status_col = "状态" in (header or [])
            if has_status_col and it["status"] not in STATUSES:
                bad(f"待办第{i}行", f"状态「{it['status']}」不在 未开始/进行中/受阻/完成/取消/待定 内")
            if it["strength"] and it["strength"] not in STRENGTHS:
                bad(f"待办第{i}行", f"表述强度「{it['strength']}」不在 确定/软性 内")
        if not any(r[0] == "FAIL" and r[1].startswith("待办第") for r in results):
            ok("待办字段", f"{len(items)} 行责任人与时间均非空")
        if header == ACTION_COLUMNS_6:
            warn("待办表状态列", "六列旧格式，缺状态列；新稿应为七列并填状态，便于跨会跟踪未完成项")
        # 相对时间：会议时间可解析到日期时才判，解析不到就放行（无基准，换算等于编造）。
        basic_date = _parse_meeting_date(basic.get("会议时间", ""))
        rel = [it["due"] for it in items if RELATIVE_TIME_RE.search(it["due"] or "")]
        if rel and basic_date:
            bad("相对时间", "完成时间须换算为具体日期，仍在用相对表述：" + "、".join(rel[:5]))
        elif rel:
            ok("相对时间", "会议时间缺失，保留原话不换算")
        # 出处：新格式要求备注列带「出处：…」；无源材料时仅提示，有源材料时进溯源统计。
        if header in (ACTION_COLUMNS_7,):
            no_src = [str(i) for i, it in enumerate(items, 1) if not it["source"]]
            if no_src:
                warn("待办出处", "以下行备注未标出处：" + "、".join(no_src[:5])
                     + "（备注写「出处：00:35 张三」或原文引文片段）")
            else:
                ok("待办出处", f"{len(items)} 行均标了原文出处")
        # 正式会议：行动项应能回指所执行的动议编号。
        # 只在确实有待办行时才判——「本次会议未形成行动事项」的稿子没有表可加列。
        if is_formal and items:
            if _has_motion_column(header or []):
                no_motion = [str(i) for i, row in enumerate(
                    _first_table(sections.get("待办事项", []))[0], 1)
                    if not MOTION_RE.search("|".join(row))]
                if no_motion:
                    warn("动议回指", "以下行未标对应动议：" + "、".join(no_motion[:5]))
                else:
                    ok("动议回指", "行动项均回指动议")
            else:
                warn("动议回指", "正式会议待办表建议加「对应动议」列，便于按动议核销执行情况")

    if not missing and not is_generic and not (is_retro or is_standup or is_layered):
        # schema 只描述常规会议结构：通用总结、复盘、站会的章节名不同，套会议 schema
        # 必然缺节，所以按类型跳过（不是放松校验——它们仍走上面的必填节与禁用标注检查）。
        # 正式会议也走 schema：它就是常规七节 + 法定要素节，七节齐了 schema 自然通过。
        try:
            import jsonschema
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            action_section = _kind_action_section("meeting")
            payload = {
                "schema_version": 3,
                "document_kind": "meeting",
                "document_kind_label": _KINDS["meeting"]["label"],
                "meeting_type": (basic.get("会议性质") or "").strip(),
                "basic_info": basic,
                "sections": {k: section_text(v) for k, v in sections.items()},
                "action_section": action_section,
                "action_items": [_schema_item(it) for it in items] if items else [],
                "decisions": [{"text": t, "source": s} for t, s in
                              (split_decision_source(d) for d in decisions)],
                "decision_texts": decisions,
                "unclaimed_items": collect_plain_items(
                    sections.get("无人认领事项", [])),
                "risks": collect_plain_items(sections.get("风险与顾虑", [])),
                "timeline": collect_plain_items(sections.get("时间线", [])),
                "divergences": collect_plain_items(
                    sections.get("分歧与转折条件", [])),
            }
            jsonschema.validate(payload, schema)
            ok("结构 schema", "符合 minutes-schema.json")
        except ImportError:
            warn("结构 schema", "jsonschema 未安装，跳过")
        except FileNotFoundError:
            warn("结构 schema", "minutes-schema.json 缺失，跳过")
        except Exception as e:
            warn("结构 schema", str(e).splitlines()[0][:120])

    # ---- 溯源核对（可选）----
    if source_text:
        src = source_text
        # 源文里的 [00:12] 时间戳要先剥掉：不剥的话「12」会进源数字集合，
        # 纪要里编造的「12 台」正好命中、查不出来（实测）
        src_clean = re.sub(r"\[\d{1,3}:\d{2}(?:-\d{1,3}:\d{2})?\]", " ", src)
        src_nums = {re.sub(r"[,\s]", "", x).rstrip("%％") for x in NUM_RE.findall(src_clean)}
        # 受检稿同样剥时间戳：会议纪要本不该有，但通用总结（播客章节、讲座术语）
        # 按规范要保留时刻，不剥就会把 00/01/20 这类时刻数字当成编造（实测告警）。
        body_for_nums = re.sub(r"[(（]?\d{1,3}:\d{2}(?:-\d{1,3}:\d{2})?[)）]?", " ", body)
        # 行首的列表序号也不是事实数据：决策列表的「1.」「2.」会被当成编造数字（实测）。
        # 只剥行首带标点的序号，不剥行内数字——「共 3 项」这种是正文事实，要保留。
        body_for_nums = re.sub(r"(?m)^\s*\d+[.、)]\s+", "", body_for_nums)
        # 日期单独核对、不进数字集合：会议时间 11 月 18 日、会上说「周五前」，成稿写
        # 「11 月 20 日前」时 20 必然不在源文里，混进数字集合会让每份合格纪要都告警
        # （实测）。日期改由下面的「日期溯源」单独判，覆盖不丢。
        doc_dates = _extract_dates(body_for_nums, basic)
        body_for_nums = _strip_dates(body_for_nums)
        # 表格展平后序号列会挤进数字流（「序号 | 待办事项 | ... | 1 | ...」），
        # 而序号是排版产物不是事实。剔掉每行表格第一个单元格——它与表头首列
        # （序号）对齐，仅在表头首列确为「序号」时执行，避免误伤别的表。
        if "序号" in body_for_nums:
            lines = []
            in_table = False
            for ln in body_for_nums.splitlines():
                cells = [c.strip() for c in ln.split("|")]
                if len(cells) >= 3 and ln.count("|") >= 2:
                    in_table = True
                    if cells and re.fullmatch(r"\d+", cells[0]):
                        cells = [""] + cells[1:]
                    # 「对应动议」列的「动议 2」是结构性编号，不是事实数字——
                    # 和序号一样剔掉，否则每份正式会议纪要都会被判编造数字。
                    cells = [re.sub(r"^动议\s*\d+$", "", c) for c in cells]
                    lines.append(" ".join(c for c in cells if c))
                else:
                    if in_table:
                        in_table = False
                    lines.append(ln)
            body_for_nums = "\n".join(lines)
        out_nums = {re.sub(r"[,\s]", "", x).rstrip("%％") for x in NUM_RE.findall(body_for_nums)}
        # 中文数字同样要进受检集合：纪要写「百分之五」「两万亿」时若只比对阿拉伯
        # 数字，这项校验就一个数都查不到、静默失效（实测：中文稿报「0 个数字」）。
        out_cn = cn_numbers(body_for_nums)
        out_all = out_nums | out_cn
        # 源文侧同样补中文数字：「每年升百分之五」在原文里就是汉字写法
        src_cn = cn_numbers(src_clean)
        src_all = src_nums | src_cn
        # 一位数不再整体豁免：源文数字很少时它恰恰是编造的重灾区。改为要求该数字
        # 在源文里以独立 token 出现，避免「1」被「12」包含而误判为已出现。
        src_tokens = set(NUM_RE.findall(src_clean))
        # 中文数字与阿拉伯数字的量级要对齐：「两万亿」展开是 2e12，而原文写
        # 「两万亿美元」也展开成 2e12，两边一致；但阿拉伯写法「2 万亿」会被
        # NUM_RE 拆成 2 和 20000 两个 token。这里把两边的量级形式都补进源文集合，
        # 只补「能由已有值直接拼出」的组合，不做跨句推断。
        invented = sorted(n for n in out_all
                          if n not in src_all and n not in src_tokens)
        if invented:
            warn("数字溯源", "源材料未出现：" + "、".join(invented[:8]))
        else:
            ok("数字溯源", f"{len(out_all)} 个数字均可在源材料中找到")

        owners = [it["owner"] for it in items
                  if it["owner"] and it["owner"] not in OWNER_PLACEHOLDERS
                  and not it["owner"].endswith(ORG_SUFFIX_LIST)]
        # 日期溯源：日期不进数字集合（会与相对时间换算冲突），单独核。
        # 通过条件二选一——源文出现过同一日期，或能由会议时间换算得出。
        src_dates = {d for _, d in _extract_dates(src_clean, {}) if d}
        src_date_raw = {raw for raw, _ in _extract_dates(src_clean, {})}
        unknown_dates = []
        for raw, parsed in doc_dates:
            if raw in src_date_raw:
                continue
            if parsed and parsed in src_dates:
                continue
            if parsed and _derivable_from_basic(parsed, basic):
                continue
            unknown_dates.append(raw)
        if unknown_dates:
            warn("日期溯源", "源材料未出现且无法由会议时间换算：" + "、".join(unknown_dates[:5]))
        elif doc_dates:
            ok("日期溯源", f"{len(doc_dates)} 个日期均可溯源或可换算")

        unknown_owners = [o for o in owners if o not in src]
        if unknown_owners:
            warn("责任人溯源", "源材料未出现：" + "、".join(sorted(set(unknown_owners))))
        elif owners:
            ok("责任人溯源", f"{len(set(owners))} 位责任人均在源材料中出现")
        else:
            ok("责任人溯源", "无可核对的责任人（均为待定/未指定）")

        # 出处溯源：备注列标了「出处：…」的行，锚点须能在源文里找到。
        # 锚点可能写成时间戳（00:35）或引文片段，两种都按归一化后的子串匹配。
        # 源文同时准备两种形态：原样，以及把「说话人N（真名）」压成真名——
        # 锚点写「00:14 钱伟」而原文是「说话人1（钱伟）」，不压标签就永远匹配不上（实测）。
        src_flat = norm_anchor(src)
        src_flat_alias = norm_anchor(re.sub(r"说话人\s*\d+\s*[（(]\s*([^）)]{1,20})\s*[）)]",
                                            r"\1", src))
        anchored = [it for it in items if it.get("source")]
        bad_src = [it["source"] for it in anchored
                   if norm_anchor(it["source"]) not in src_flat
                   and norm_anchor(it["source"]) not in src_flat_alias]
        if bad_src:
            warn("出处溯源", "以下出处无法在源材料中找到：" + "、".join(bad_src[:5]))
        elif anchored:
            ok("出处溯源", f"{len(anchored)} 条出处均可在源材料中找到")

        # 说话人归属（语义层的第一项下沉）：出处引文能定位到某段，而该段说话人与
        # 责任人不是同一人时提示。误报来源明确——同一人跨段发言、责任人由他人代派、
        # 正式会议的动议由主持人宣读而由他人执行，所以只出 WARN 不判 FAIL。
        # 这是外部评测里占比 16% 的失效类型的最小化机检。
        seg_speakers = _segment_speakers(src)
        # 执行主体为部门/单位（「财务部」「运营组」）时归属核对无从比对，跳过
        misattr = []
        for it in anchored:
            owner = it["owner"]
            if not owner or owner in OWNER_PLACEHOLDERS:
                continue
            if owner.endswith(ORG_SUFFIX_LIST):
                continue
            # 责任人不在原文出现时归属比对无意义——「责任人溯源」已经报过这条，
            # 再叠一条归属告警只是同一问题的重复。实测场景：纪要用身份代称
            # （「主持人」）而原文是「说话人0」标签，两边天然对不上。
            if owner not in src:
                continue
            spk = _speaker_of_anchor(it["source"], seg_speakers)
            if spk and owner not in spk and spk not in owner:
                misattr.append(f"第{it['id'] or '?'}行 {owner} / {spk}")
        if misattr:
            warn("说话人归属", "责任人与出处段说话人不同（可能是代派或跨段发言）："
                 + "；".join(misattr[:3]))

    # ---- 通用总结的两条可机检项（会议版不适用）----
    if is_generic:
        _check_generic_machine_items(sections, source_text, warn, ok, bad)

    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    if strict:
        fails += warns
    return (1 if fails else 0), results


def _segment_speakers(src):
    """从会议原文抽 [(时间戳秒, 说话人)]。

    原文两种常见形态都认：管线产出的 `[00:35] 张三：…`，
    以及「说话人N（真名）」标注的 `[00:35] 说话人1（李四）：…`。
    后者取括号里的真名——纪要正文用的是真名，按标签「说话人1」比对必然错配。
    """
    out = []
    for m in re.finditer(r"\[(\d{1,3}):(\d{2})(?:-\d{1,3}:\d{2})?\]\s*([^：:\n]{1,20})[：:]", src):
        label = m.group(3).strip()
        alias = re.search(r"[（(]\s*([^）)]{1,20})\s*[）)]", label)
        if alias:
            label = alias.group(1).strip()
        else:
            label = re.sub(r"^说话人\s*\d+\s*", "", label).strip() or label
        out.append((int(m.group(1)) * 60 + int(m.group(2)), label))
    return out


def _speaker_of_anchor(anchor, seg_speakers):
    """按出处锚点找对应段的说话人。锚点是时间戳时取最近的前一段。"""
    if not seg_speakers or not anchor:
        return ""
    m = re.search(r"(\d{1,3}):(\d{2})", anchor)
    if not m:
        return ""
    t = int(m.group(1)) * 60 + int(m.group(2))
    best = ""
    for ts, spk in seg_speakers:
        if ts <= t + 1:
            best = spk
        else:
            break
    return best


def _check_generic_machine_items(sections, source_text, warn, ok, bad=None):
    """通用总结两条机检：播客章节时间戳（FAIL 级）、讲座术语定义（WARN 级）。

    分级理由：时刻是精确值，错位就是错，按 FAIL 判（SKILL.md「时间戳一律按原文
    实际时刻写，不估算」）；术语名能否溯源受讲者口语变体影响，误报率高，按 WARN 判。
    """
    body_ts = section_text(sections.get("章节时间戳", []))
    if body_ts.strip():
        rows = [ln for ln in body_ts.splitlines() if ln.strip()]
        stamps = []
        for ln in rows:
            m = re.search(r"(\d{1,3}):([0-5]\d)", ln)
            if m:
                stamps.append((int(m.group(1)) * 60 + int(m.group(2)), ln.strip()))
        if stamps and stamps[0][0] != 0:
            (bad or warn)("章节时间戳", f"首条章节不是 0:00，实为 {stamps[0][1][:20]}")
        elif stamps:
            ok("章节时间戳", f"{len(stamps)} 条章节时刻齐备")
        if stamps and source_text:
            src_ts = {int(m.group(1)) * 60 + int(m.group(2))
                      for m in re.finditer(r"\[(\d{1,3}):([0-5]\d)", source_text)}
            if src_ts:
                off = [f"{t // 60}:{t % 60:02d}" for t, ln in stamps if t not in src_ts]
                if off:
                    (bad or warn)("章节时间戳溯源",
                                  "以下时刻源材料中不存在：" + "、".join(off[:5]))
                else:
                    ok("章节时间戳溯源", f"{len(stamps)} 个时刻均能在源材料中找到")

    terms_body = section_text(sections.get("术语定义", []))
    if terms_body.strip() and source_text:
        terms = [LIST_RE.sub("", ln).strip() for ln in terms_body.splitlines() if ln.strip()]
        terms = [t for t in terms if t]
        # 术语名取条目开头到第一个分隔符（「：」「——」「·」）
        names = [re.split(r"[：:—\-·]", t, 1)[0].strip() for t in terms]
        names = [n for n in names if 1 < len(n) <= 20]
        if names:
            missing = [n for n in names if n not in source_text]
            if missing:
                warn("术语定义", "以下术语在源材料中未出现：" + "、".join(missing[:5]))
            else:
                # 定义句式是加分证据而非必要条件：讲者常说「X 就是…」等变体，
                # 只要求术语本身在原文出现，定义句式的出现与否只写进明细。
                has_def = bool(DEFINITION_RE.search(source_text))
                ok("术语定义", f"{len(names)} 个术语均可溯源"
                   + ("（原文含定义句式）" if has_def else "（原文未见标准定义句式）"))


def summarize(results):
    """三态判定：complete 全过 / degraded 无 FAIL 但有 WARN / failed 有 FAIL。

    借 silverstein/minutes 的 status 枚举思路——把「结构可用但存在非致命问题」
    从二元的 PASS/FAIL 里分出来，避免有 WARN 的稿子被当成完全健康或彻底报废。
    results 为空表示根本没读到内容，返回 error 而不是 complete——否则读取失败
    （rc=2）会打印「状态：complete」，把「没校验」说成「校验通过」（实测）。
    """
    if not results:
        return "error", []
    if len(results) == 1 and results[0][1] == "读取文件":
        return "error", []          # 解析失败，任何一项校验都没跑过
    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    if fails:
        status = "failed"
    elif warns:
        status = "degraded"
    else:
        status = "complete"
    warnings = [{"step": name, "reason": detail or "未给原因", "message": detail}
                for _, name, detail in warns]
    return status, warnings


SELFTEST_OK = """# 会议纪要：测试会议

## 会议基本信息

会议主题：测试
会议时间：2026-10-19
参会人员：张三、李四

## 会议结论

本次会议决定采用方案 A。

## 关键决策

决定采用方案 A 上线。

## 待办事项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 完成上线检查 | 张三 | 10 月 25 日 | 高 | 未开始 | 优先级未明示；出处：00:15 张三 |

## 待定与开放问题

预算口径待定，由李四跟进。

## 会议内容

### 上线方案

讨论后确定采用方案 A。
"""

SELFTEST_BAD = """# 会议纪要：坏例

## 会议结论

待确认。

## 关键决策

讨论了方案 A。

## 待办事项

| 序号 | 待办事项 | 责任人 | 完成时间 |
|---|---|---|---|
| 1 | 处理一下 |  |  |

## 会议内容

无。
"""


def selftest():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "good.md"
        good.write_text(SELFTEST_OK, encoding="utf-8")
        rc, res = validate(good)
        print(f"[{'PASS' if rc == 0 else 'FAIL'}] 正例应通过（exit={rc}）")
        ok &= rc == 0
        for level, name, detail in res:
            if level != "PASS":
                print(f"  {level} {name} {detail}")
        badp = Path(td) / "bad.md"
        badp.write_text(SELFTEST_BAD, encoding="utf-8")
        rc2, res2 = validate(badp)
        caught = [r for r in res2 if r[0] == "FAIL"]
        print(f"[{'PASS' if rc2 == 1 and len(caught) >= 4 else 'FAIL'}] 负例应被抓（exit={rc2}，{len(caught)} 项 FAIL）")
        ok &= rc2 == 1 and len(caught) >= 4
        # 数字溯源负例：成稿出现源材料没有的非日期数字，应告警。
        # 不用日期做载体——日期已单独走「日期溯源」，混用会让这条断言失去意义。
        src = "本次会议由张三汇报，产量 120 吨。"
        num_doc = SELFTEST_OK.replace("完成上线检查", "完成 999 台设备上线检查")
        nump = Path(td) / "num.md"
        nump.write_text(num_doc, encoding="utf-8")
        rc3, res3 = validate(nump, source_text=src)
        warn_hit = any(r[0] == "WARN" and r[1] == "数字溯源" for r in res3)
        print(f"[{'PASS' if warn_hit else 'FAIL'}] 编造数字应告警")
        ok &= warn_hit
        # 日期不混进数字集合：待办里的「10 月 25 日」不应被判成编造数字
        # （会期 10 月 19 日，25 日在 30 天内可换算，属合法日期）
        date_as_num = any(r[0] == "WARN" and r[1] == "数字溯源" for r in res3
                          if "25" in r[2])
        print(f"[{'PASS' if not date_as_num else 'FAIL'}] 日期不得被当成编造数字")
        ok &= not date_as_num
        # 三态判定：干净稿=complete，有 WARN=degraded，有 FAIL=failed
        st_clean, _ = summarize(validate(good)[1])
        st_warn, wl = summarize(res3)
        st_fail, _ = summarize(validate(badp)[1])
        tri_ok = st_clean == "complete" and st_warn == "degraded" and st_fail == "failed" and len(wl) > 0
        print(f"[{'PASS' if tri_ok else 'FAIL'}] 三态判定（{st_clean}/{st_warn}/{st_fail}，告警 {len(wl)} 条）")
        ok &= tri_ok
        # 状态枚举：非法状态值必须被抓，合法五态放行。
        st_bad = SELFTEST_OK.replace("| 高 | 未开始 |", "| 高 | 已完成一半 |")
        stp = Path(td) / "status.md"
        stp.write_text(st_bad, encoding="utf-8")
        rc5, res5 = validate(stp)
        st_caught = any(r[0] == "FAIL" and "状态" in r[2] for r in res5)
        print(f"[{'PASS' if st_caught else 'FAIL'}] 非法状态值应被抓")
        ok &= st_caught
        # 表述强度：备注里「强度：软性」应被解析出来，非法值要 FAIL。
        soft_doc = SELFTEST_OK.replace("优先级未明示；", "优先级未明示；强度：软性；")
        sp = Path(td) / "soft.md"
        sp.write_text(soft_doc, encoding="utf-8")
        _rc6, res6 = validate(sp)
        items6, _ = collect_action_items(
            split_sections(parse_md(soft_doc))[1].get("待办事项", []))
        strength_ok = (items6 and items6[0]["strength"] == "软性"
                       and not any(r[0] == "FAIL" and "强度" in r[2] for r in res6))
        print(f"[{'PASS' if strength_ok else 'FAIL'}] 软承诺强度应被解析为合法值")
        ok &= bool(strength_ok)
        st_bad2 = soft_doc.replace("强度：软性", "强度：也许")
        sp2 = Path(td) / "soft_bad.md"
        sp2.write_text(st_bad2, encoding="utf-8")
        _rc6b, res6b = validate(sp2)
        strength_bad = any(r[0] == "FAIL" and "强度" in r[2] for r in res6b)
        print(f"[{'PASS' if strength_bad else 'FAIL'}] 非法强度值应被抓")
        ok &= strength_bad
        # 导出结构：document_status / processing_warnings / 决策对象化三件必须在位。
        exp = export_structured(good, source_text="[00:15] 张三：上线检查我来做。")
        exp_ok = (exp.get("document_status") in ("complete", "degraded", "failed")
                  and isinstance(exp.get("processing_warnings"), list)
                  and exp["decisions"] and isinstance(exp["decisions"][0], dict)
                  and exp["decisions"][0].get("text")
                  and exp.get("decision_texts") == [d["text"] for d in exp["decisions"]])
        print(f"[{'PASS' if exp_ok else 'FAIL'}] 导出含文档状态与对象化决策")
        ok &= exp_ok
        # 六列旧件的兼容：不应因缺状态列 FAIL，只 WARN。
        six = SELFTEST_OK.replace(
            "| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | 完成上线检查 | 张三 | 10 月 25 日 | 高 | 未开始 | 优先级未明示；出处：00:15 张三 |",
            "| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | 完成上线检查 | 张三 | 10 月 25 日 | 高 | 优先级未明示 |")
        sixp = Path(td) / "six.md"
        sixp.write_text(six, encoding="utf-8")
        rc7, res7 = validate(sixp)
        six_ok = (rc7 == 0 and any(r[0] == "WARN" and r[1] == "待办表状态列" for r in res7))
        print(f"[{'PASS' if six_ok else 'FAIL'}] 六列旧件只告警不判错（exit={rc7}）")
        ok &= six_ok
        # docx 往返：article-format 出稿后标题是段落样式、正文无 `##`，
        # 这条断言防 parse_docx 退回「只认字面 ##」的旧缺陷。
        try:
            from docx import Document
        except ImportError:
            print("[SKIP] docx 往返（未安装 python-docx）")
        else:
            dx = Path(td) / "good.docx"
            doc = Document()

            def flush(rows):
                if not rows:
                    return
                t = doc.add_table(rows=len(rows), cols=len(rows[0]))
                for i, r in enumerate(rows):
                    for j, c in enumerate(r[:len(rows[0])]):
                        t.cell(i, j).text = c

            doc.add_paragraph("会议纪要：测试会议", style="Title")
            pending = []
            for line in SELFTEST_OK.splitlines():
                s = line.strip()
                if s.startswith("# "):
                    continue
                is_table = s.startswith("|")
                if not is_table:
                    flush(pending)
                    pending = []
                if is_table:
                    cells = [c.strip() for c in s.strip("|").split("|")]
                    if not all(set(c) <= set("-: ") for c in cells):
                        pending.append(cells)
                    continue
                if s.startswith("### "):
                    doc.add_paragraph(s[4:], style="Heading 2")
                elif s.startswith("## "):
                    doc.add_paragraph(s[3:].strip(), style="Heading 1")
                elif s:
                    doc.add_paragraph(s)
            flush(pending)
            doc.save(str(dx))
            rc4, res4 = validate(dx)
            docx_ok = rc4 == 0
            print(f"[{'PASS' if docx_ok else 'FAIL'}] docx 往返按样式识别标题（exit={rc4}）")
            if not docx_ok:
                for level, name, detail in res4:
                    if level != "PASS":
                        print(f"  {level} {name} {detail}")
            ok &= docx_ok

        # 禁用标注必须扫到基本信息区：只在四个必填节正文里查时，
        # 「会议时间：待确认」写进基本信息区能整篇通过（实测）
        basic_forbidden = SELFTEST_OK.replace("会议时间：2026-10-19", "会议时间：待确认")
        bf = Path(td) / "basic-forbidden.md"
        bf.write_text(basic_forbidden, encoding="utf-8")
        rc5, res5 = validate(bf)
        caught5 = any(r[0] == "FAIL" and r[1] == "禁用标注" for r in res5)
        print(f"[{'PASS' if caught5 else 'FAIL'}] 基本信息区的禁用标注应被抓（exit={rc5}）")
        ok &= caught5

        # 源文时间戳不得参与数字比对：源文里的 [00:12] 会让编造的「12 台」看起来有出处。
        # 用一个非日期的编造数字（台数）验证，避免与日期豁免混淆。
        ts_src = "[00:12] 张三：今天讨论一下。\n[01:30] 李四：好的。"
        ts_doc = SELFTEST_OK.replace("完成上线检查", "完成 12 台设备上线检查")
        tsp = Path(td) / "ts.md"
        tsp.write_text(ts_doc, encoding="utf-8")
        rc6, res6 = validate(tsp, source_text=ts_src)
        caught6 = any(r[0] == "WARN" and r[1] == "数字溯源" for r in res6)
        print(f"[{'PASS' if caught6 else 'FAIL'}] 时间戳不得为编造数字背书")
        ok &= caught6

        # 未识别的二级标题必须被点名：静默吞掉会让「这一节整段不见」以 PASS 交出。
        # 反例用「把待定与开放问题改写成风险分析」——它落在可选节位置，缺节告警不会触发，
        # 只有这条新校验能发现（实测过的漏检形态）。
        unk_doc = SELFTEST_OK.replace("## 待定与开放问题", "## 风险分析汇总")
        up = Path(td) / "unknown_heading.md"
        up.write_text(unk_doc, encoding="utf-8")
        _rc8, res8 = validate(up)
        unk_ok = any(r[0] == "WARN" and r[1] == "未识别章节" for r in res8)
        print(f"[{'PASS' if unk_ok else 'FAIL'}] 未识别章节应被点名而非静默吞掉")
        ok &= unk_ok
        # 正例：章节名全部合规时不得误报（「会议基本信息」是容器标题，不算未识别）
        _rc9, res9 = validate(Path(td) / "ok.md") if (Path(td) / "ok.md").exists() else (0, [])
        if not res9:
            okp = Path(td) / "ok.md"
            okp.write_text(SELFTEST_OK, encoding="utf-8")
            _rc9, res9 = validate(okp)
        no_false = not any(r[1] == "未识别章节" and r[0] != "PASS" for r in res9)
        print(f"[{'PASS' if no_false else 'FAIL'}] 合规章节名不得误报未识别")
        ok &= no_false

        # 读取失败必须是 error 态，不能打印 complete。
        # 夹具不能用 b"\xff\xfe..." —— 那是 UTF-16 BOM，v1.16.0 起有编码兜底后
        # 会被成功解码，反而把一个可读文件判成「读取失败」（实测踩到）。
        # 用 b"\xff\xff..."（无有效 BOM，utf-8/gb18030/utf-16 都解不动）才真的读不了。
        junk = Path(td) / "junk.md"
        junk.write_bytes(b"\xff\xff\xff\xffnot-utf8")
        rc7, res7 = validate(junk)
        st7, _ = summarize(res7)
        err_ok = rc7 == 2 and st7 == "error"
        print(f"[{'PASS' if err_ok else 'FAIL'}] 读取失败应为 error 态（exit={rc7}，status={st7}）")
        ok &= err_ok

        # ---- v1.16.0 回归锁：本轮修的四类缺陷各留一条反例 ----

        # P0-1 导出按骨架选表节名：复盘稿的表在「下轮改进项」，不在「待办事项」。
        # 旧版写死「待办事项」导致表有一行却导出 0 条。
        retro_doc = """# 复盘会纪要

## 会议基本信息

会议主题：迭代复盘
会议时间：2026-11-20

## 本次目标与结果

按计划推进。

## 做得好的

需求提前冻结。

## 待改进

联调排期过紧。

## 下轮改进项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 联调预留三天缓冲 | 张三 | 11 月 28 日 | 高 | 未开始 | 出处：00:20 张三 |

## 会议内容

复盘讨论。
"""
        rp = Path(td) / "retro.md"
        rp.write_text(retro_doc, encoding="utf-8")
        exp_retro = export_structured(rp)
        retro_ok = (exp_retro["action_section"] == "下轮改进项"
                    and len(exp_retro["action_items"]) == 1
                    and exp_retro["document_kind"] == "retro")
        print(f"[{'PASS' if retro_ok else 'FAIL'}] 复盘稿的改进项应被导出"
              f"（section={exp_retro['action_section']}，{len(exp_retro['action_items'])} 条）")
        ok &= retro_ok

        # P0-1b 分层版的表在「行动项」，且标题带编号（模板示例就是带编号的）
        layered_doc = """# 分层版纪要

## 会议基本信息

会议主题：方案评审
会议时间：2026-11-20

## 一、会议决策摘要

决定采用方案 A。

## 三、关键议题与讨论

讨论若干。

## 六、行动项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 补充成本测算 | 李四 | 11 月 25 日 | 高 | 未开始 | 出处：00:30 李四 |

## 七、会议结论与后续安排

按方案 A 推进。
"""
        lp = Path(td) / "layered.md"
        lp.write_text(layered_doc, encoding="utf-8")
        rc_l, res_l = validate(lp)
        exp_l = export_structured(lp)
        layered_ok = (exp_l["document_kind"] == "layered"
                      and len(exp_l["action_items"]) == 1
                      and exp_l["action_section"] == "行动项"
                      and rc_l == 0)
        print(f"[{'PASS' if layered_ok else 'FAIL'}] 分层版带编号标题应被认且表应导出"
              f"（kind={exp_l['document_kind']}，{len(exp_l['action_items'])} 条，exit={rc_l}）")
        ok &= layered_ok

        # P0-2 导出状态与校验结论必须一致：同一份复盘稿，两边都该是「没问题」
        st_retro, _ = summarize(validate(rp)[1])
        consistent = (st_retro == "complete"
                      and exp_retro["document_status"] == "complete")
        print(f"[{'PASS' if consistent else 'FAIL'}] 导出状态与校验结论不得互斥"
              f"（校验={st_retro}，导出={exp_retro['document_status']}）")
        ok &= consistent

        # P0-4 编码兜底：GBK 的 md 应能正常读，且明确报「编码不对」而非「不是 docx」
        gbk = Path(td) / "gbk.md"
        gbk.write_bytes(SELFTEST_OK.encode("gbk"))
        rc_g, res_g = validate(gbk)
        gbk_ok = rc_g == 0
        print(f"[{'PASS' if gbk_ok else 'FAIL'}] GBK 编码的 md 应能正常读取（exit={rc_g}）")
        ok &= gbk_ok

        # 真不可读时错误信息要点明编码，不能报「无法解析 md/docx」
        bad_enc = Path(td) / "bad-enc.md"
        bad_enc.write_bytes(b"\xff\xff\xff\xff")
        rc_e, res_e = validate(bad_enc)
        msg_e = res_e[0][2] if res_e else ""
        enc_msg_ok = (rc_e == 2 and "编码" in msg_e and "docx" not in msg_e)
        print(f"[{'PASS' if enc_msg_ok else 'FAIL'}] 编码失败不得误报为 docx 损坏（{msg_e[:40]}）")
        ok &= enc_msg_ok

        # P1 schema 覆盖四新节：导出必须带结构化数组，不是只有纯文本
        four_doc = """# 研讨纪要

## 会议基本信息

会议主题：方向研讨
会议时间：2026-11-20
会议性质：研讨交流

## 会议结论

本次未形成决议。

## 无人认领事项

- 「应该有人去问口径」——待定：指派给谁

## 风险与顾虑

- 预算超支风险，财务判断，前提是规模翻倍

## 时间线

- 11 月 30 日 提交初稿

## 分歧与转折条件

- 分歧：自研还是采购；若成本低于预期则同意自研

## 会议内容

研讨若干。
"""
        fp = Path(td) / "four.md"
        fp.write_text(four_doc, encoding="utf-8")
        exp_f = export_structured(fp)
        four_ok = (len(exp_f["unclaimed_items"]) == 1
                   and len(exp_f["risks"]) == 1
                   and len(exp_f["timeline"]) == 1
                   and len(exp_f["divergences"]) == 1
                   and exp_f["meeting_type"] == "研讨交流")
        print(f"[{'PASS' if four_ok else 'FAIL'}] 四新节应导出为结构化数组"
              f"（{len(exp_f['unclaimed_items'])}/{len(exp_f['risks'])}/"
              f"{len(exp_f['timeline'])}/{len(exp_f['divergences'])}，"
              f"性质={exp_f['meeting_type']}）")
        ok &= four_ok

        # P2 跨会滑期：同一件事两次改期，第二次应计滑期，累计 2 次须处置
        idx = Path(td) / "minutes-index.jsonl"
        m1 = """# 周会

## 会议基本信息

会议主题：周会
会议时间：2026-11-01

## 会议结论

按期推进。

## 待办事项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 修复结算页按钮 | 张三 | 11 月 10 日 | 高 | 未开始 | 出处：00:10 张三 |

## 会议内容

推进。
"""
        m2 = m1.replace("2026-11-01", "2026-11-08").replace("11 月 10 日", "11 月 17 日")
        m3 = m1.replace("2026-11-01", "2026-11-15").replace("11 月 10 日", "11 月 24 日")
        for name, txt in (("m1.md", m1), ("m2.md", m2), ("m3.md", m3)):
            (Path(td) / name).write_text(txt, encoding="utf-8")
        # 第一次入索引（无历史可比，slips=0）
        d1 = export_structured(Path(td) / "m1.md")
        append_index(idx, d1, "2026-11-01", load_index(idx))
        # 第二次：改期一次 → append 时 slips 记为 1；compare 读历史（slips=0）故不报处置
        d2 = export_structured(Path(td) / "m2.md")
        rep2 = compare_with_index(d2, load_index(idx), "2026-11-08")
        append_index(idx, d2, "2026-11-08", load_index(idx))
        # 第三次：历史快照 slips 已是 1，再改期一次 → compare 报滑期且累计达 2 须处置
        d3 = export_structured(Path(td) / "m3.md")
        rep3 = compare_with_index(d3, load_index(idx), "2026-11-15")
        append_index(idx, d3, "2026-11-15", load_index(idx))
        # 落库后重读应看到 slips=2，证明累计量真的持久化了（不是只在内存里）
        persisted = load_index(idx)
        persisted_slips = max((int(r.get("slips", 0)) for r in persisted.values()),
                              default=-1)
        slip_ok = (len(rep2["slipped"]) == 1 and len(rep2["must_act"]) == 0
                   and len(rep3["slipped"]) == 1 and len(rep3["must_act"]) == 1
                   and persisted_slips == 2)
        print(f"[{'PASS' if slip_ok else 'FAIL'}] 跨会滑期计数与两次须处置"
              f"（第2次滑{len(rep2['slipped'])}/处置{len(rep2['must_act'])}，"
              f"第3次滑{len(rep3['slipped'])}/处置{len(rep3['must_act'])}，"
              f"落库slips={persisted_slips}）")
        ok &= slip_ok

        # P1 章节白名单单一真相源：JSON 必须存在且被判据读到
        single_src = (SECTIONS_PATH.is_file()
                      and "会议结论" in _kind_required("meeting")
                      and _kind_required("formal") == _kind_required("meeting") + ["法定要素"]
                      and _kind_action_section("retro") == "下轮改进项")
        print(f"[{'PASS' if single_src else 'FAIL'}] 章节白名单由 sections.json 驱动")
        ok &= single_src
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def collect_plain_items(entries, stem_prefixes=()):
    """把一节里的列表项抽成字符串列表（用于无人认领/风险/时间线/分歧四节）。

    这四节在 v1.15.0 起是纯文本，导出时原样进 sections 就够人看，但下游要筛
    「哪些风险还开着」「谁的条件没满足」时只能正则扒文本。这里给出最小结构化：
    逐条成项，并尽量抽出首句作为标题句。不做字段级解析——四节的填法本身是
    自由散文（「什么风险、谁的判断、什么前提」），硬拆字段会逼出编造。
    """
    out = []
    for kind, payload in entries:
        if kind != "p":
            continue
        text = LIST_RE.sub("", payload.strip()).strip()
        if not text:
            continue
        for p in stem_prefixes:
            if text.startswith(p):
                text = text[len(p):].strip()
                break
        if text:
            out.append(text)
    return out


def _slip_key(item, meeting_date=None):
    """行动项的跨会稳定键：责任人 + 事项的归一化串。

    待办表没有稳定 ID（每次会重新编号），跨会认同一件事只能靠内容。
    责任人加进键里，是因为「同一件事换人做」应当算新任务而不是滑期。
    meeting_date 不参与成键——带了日期，同一件事在下一次会上就永远匹配不上，
    滑期计数会恒为 0（这是 v3 初稿的缺陷，索引一跑就暴露）。日期单独存进记录字段。
    """
    task = re.sub(r"[\s，。、；;：:（）()“”\"'《》！!？?]", "",
                  item.get("task") or "")
    owner = re.sub(r"\s", "", item.get("owner") or "")
    return f"{owner}|{task}"


def load_index(index_path):
    """读跨会索引（JSONL，一行一条行动项快照），按 key 归并出最新状态。

    索引是 append-only 的流水，每次出稿追加一批；读取时按 key 取最后一条，
    这样「滑期」只需要比较两次快照的完成时间，不需要改写历史行。
    文件不存在时返回空——第一次跑索引不该报错。
    """
    latest = {}
    p = Path(index_path)
    if not p.is_file():
        return latest
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue          # 坏行跳过，不让一行脏数据毁掉整个索引
        key = rec.get("key")
        if key:
            latest[key] = rec
    return latest


def append_index(index_path, data, meeting_date, prev=None):
    """把本次导出的行动项追加进 JSONL 索引。返回写入条数。

    prev 是 load_index 读出的历史快照（可选）。传了就把累计滑期次数一并写进
    新记录——滑期是跨会累计量，只在内存里加是没用的：下一次 load_index 取到的是
    最后一条，那条若不带 slips，计数就永远归零（v3 初稿实测踩到，
    自检「两次须处置」一直判 0）。
    """
    rows = []
    for it in data.get("action_items", []):
        key = _slip_key(it)
        rec = (prev or {}).get(key) or {}
        slips = _next_slips(rec, it)
        rows.append({
            "key": key,
            "meeting_date": meeting_date,
            "source_file": data.get("source_file", ""),
            "task": it.get("task", ""),
            "owner": it.get("owner", ""),
            "due": it.get("due", ""),
            "status": it.get("status", ""),
            "priority": it.get("priority", ""),
            "strength": it.get("strength", ""),
            "slips": slips,
        })
    if rows:
        with Path(index_path).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def _next_slips(rec, item):
    """历史快照 + 本次改期 → 本次之后的累计滑期次数。

    compare 与 append 必须共用这一个算法。分开各算一次的后果实测过：
    compare 读 append 之前的快照，只看到 1 次滑期，于是「累计 2 次须处置」
    永远不触发；而 append 写回的是 2，两边对不上。
    """
    slips = int((rec or {}).get("slips", 0))
    prev_due = (rec or {}).get("due", "")
    cur_due = item.get("due", "")
    if prev_due and cur_due and cur_due != prev_due:
        slips += 1
    return slips


def compare_with_index(data, prev, meeting_date):
    """拿本次行动项与历史索引比对，标出滑期与未闭环。

    比对口径（对应 references/extraction.md「滑期约定」）：
      - 本次仍出现且完成时间比上次晚 → 滑期一次
      - 本次没出现、上次也不是完成/取消 → 消失项（可能悄悄丢了，值得人看一眼）
      - 累计滑期 ≥2 且本次仍非完成 → 标 must_act，对应「滑两次必须改人改期或改范围」
    这条规则此前只写在文档里、纯靠人工自查（v1.15.1 自述「校验器判不了」），
    有了索引就能机检。
    """
    report = {"slipped": [], "vanished": [], "must_act": []}
    seen = set()
    for it in data.get("action_items", []):
        loose = _slip_key(it)
        rec = prev.get(loose)
        seen.add(loose)
        if not rec:
            continue
        slips = _next_slips(rec, it)
        prev_due = rec.get("due", "")
        cur_due = it.get("due", "")
        if prev_due and cur_due and cur_due != prev_due:
            report["slipped"].append(
                {"task": it.get("task", ""), "owner": it.get("owner", ""),
                 "from": prev_due, "to": cur_due, "slips": slips})
        if slips >= 2 and it.get("status", "") not in ("完成", "取消"):
            report["must_act"].append(
                {"task": it.get("task", ""), "owner": it.get("owner", ""),
                 "slips": slips})
    for loose, rec in prev.items():
        if loose in seen or rec.get("status") in ("完成", "取消"):
            continue
        report["vanished"].append(
            {"task": rec.get("task", ""), "owner": rec.get("owner", ""),
             "due": rec.get("due", "")})
    return report


def export_structured(path, source_text=None):
    """把纪要解析成机器可读结构：基本信息 / 各节文本 / 待办表 / 决策列表 / 四新节。

    纪要到这一步之前只是给人看的文档，下游（任务同步、跨会议检索、报表）
    拿不到任何字段。这里复用校验器已有的解析逻辑，不新增第二套解析器——
    两套解析器必然漂移，而校验器已经覆盖 md 与 docx 两种输入。

    表节名按骨架取（_kind_action_section），不是写死「待办事项」：
    复盘稿的表在「下轮改进项」、分层版在「行动项」、通用总结在「行动事项」。
    v1.15.1 写死「待办事项」，导致这几类稿子各有表却导出 0 条（实测）。

    文档级完整性（status / processing_warnings）随导出一起下发：纪要「残不残」
    不能只藏在校验日志里，消费方拿到 JSON 就该知道。给了 source_text 时会先跑
    一遍校验取真实三态；没给则按 detect_kind 的必填节推——缺必填节 failed、
    齐了 complete（此时不代表已校验，只代表结构可解析）。
    """
    blocks = parse_input(path)
    basic, sections, _, _unknown = split_sections(blocks)
    kind = detect_kind(sections)
    action_section = _kind_action_section(kind)
    action_items, _header = collect_action_items(sections.get(action_section) or [])
    decision_lines = collect_decisions(sections.get("关键决策") or [])
    decisions = []
    for line in decision_lines:
        text, src = split_decision_source(line)
        if text:
            decisions.append({"text": text, "source": src})

    # 状态一律由真实校验产出，不自己按必填节推。
    # 自推版本会把「结构齐但有 WARN」的稿子报成 complete，而同一文件跑
    # validate 报 degraded——导出状态与校验结论打架（实测：正式会议缺决策节）。
    # 代价是多解析一次，正确性优先；给了 source_text 时校验还会带上溯源核对。
    _rc, results = validate(path, source_text)
    status, warn_dicts = summarize(results)
    warnings_payload = warn_dicts

    # 四新节的结构化形态（v1.16.0）：保持纯文本 sections 不动，另给可筛的数组。
    unclaimed = collect_plain_items(sections.get("无人认领事项") or [])
    risks = collect_plain_items(sections.get("风险与顾虑") or [])
    timeline = collect_plain_items(sections.get("时间线") or [])
    divergences = collect_plain_items(sections.get("分歧与转折条件") or [])

    return {
        "schema_version": 3,
        "source_file": str(path),
        "document_kind": kind,
        "document_kind_label": _KINDS[kind]["label"],
        "meeting_type": (basic.get("会议性质") or "").strip(),
        "basic_info": basic,
        "sections": {k: section_text(v) for k, v in sections.items()},
        "action_items": [_schema_item(it) for it in action_items],
        "action_section": action_section,
        "decisions": decisions,
        # 向后兼容：v1 消费方读纯文本决策列表
        "decision_texts": [d["text"] for d in decisions],
        "unclaimed_items": unclaimed,
        "risks": risks,
        "timeline": timeline,
        "divergences": divergences,
        "document_status": status,
        "status": status,
        "processing_warnings": warnings_payload,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="结构化总结结构校验与源文核对（会议版章节名）")
    ap.add_argument("file", nargs="?", help="纪要 md/docx")
    ap.add_argument("--source", default=None, help="源材料文本（转写稿/笔记）")
    ap.add_argument("--strict", action="store_true", help="WARN 也判失败")
    ap.add_argument("--json", dest="json_path", default=None, help="结果落 JSON")
    ap.add_argument("--export", default=None, metavar="JSON",
                    help="导出结构化纪要（基本信息/各节/待办表/决策列表/四新节）到指定 JSON")
    ap.add_argument("--index", default=None, metavar="JSONL",
                    help="把本次行动项追加进跨会索引（JSONL）。已有的同键记录用于滑期比对")
    ap.add_argument("--compare", action="store_true",
                    help="配合 --index：与索引里的历史快照比对滑期与消失项，结果进 --export 的 cross_meeting_report")
    ap.add_argument("--selftest", action="store_true")
    ns = ap.parse_args(argv)
    if ns.selftest:
        return selftest()
    if not ns.file:
        ap.error("需要纪要文件，或 --selftest")
    path = Path(ns.file)
    if not path.is_file():
        print(f"文件不存在：{path}")
        return 2
    if ns.export or ns.index:
        export_source = (read_text_any(Path(ns.source))
                         if ns.source else None)
        data = export_structured(path, export_source)
        # 会议时间用作索引的日期锚；解析不到就留空，不臆造
        meeting_date = (data.get("basic_info", {}).get("会议时间") or "").strip()
        prev = load_index(ns.index) if (ns.index or ns.compare) else {}
        if ns.compare and ns.index:
            data["cross_meeting_report"] = compare_with_index(
                data, prev, meeting_date)
        if ns.index:
            # 先 compare 再 append：compare 读的是历史快照，append 把含累计 slips 的
            # 新快照落库，顺序反了会拿本次刚写的行跟自己比
            n = append_index(ns.index, data, meeting_date, prev)
            print(f"已写入跨会索引 {n} 条：{ns.index}")
        if ns.export:
            Path(ns.export).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
            rep = data.get("cross_meeting_report") or {}
            extra = ""
            if rep:
                extra = (f"，滑期 {len(rep.get('slipped', []))}，"
                         f"须处置 {len(rep.get('must_act', []))}，"
                         f"消失 {len(rep.get('vanished', []))}")
            print(f"结构化纪要已导出：{ns.export}"
                  f"（骨架 {data['document_kind']}，"
                  f"行动项 {len(data['action_items'])} 条，"
                  f"决策 {len(data['decisions'])} 条，"
                  f"状态 {data['document_status']}{extra}）")
    source = read_text_any(Path(ns.source)) if ns.source else None
    rc, results = validate(path, source, ns.strict)
    for level, name, detail in results:
        print(f"[{level}] {name}" + (f"  {detail}" if detail else ""))
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    status, warnings = summarize(results)
    print(f"\n结论：{'PASS' if rc == 0 else 'FAIL'}（FAIL {fails}，WARN {warns}）")
    print(f"状态：{status}")
    for w in warnings:
        print(f"  告警步骤：{w['step']} — {w['reason']}")
    if ns.json_path:
        Path(ns.json_path).write_text(json.dumps(
            {"file": str(path), "verdict": "PASS" if rc == 0 else "FAIL",
             "status": status,
             "results": [{"level": a, "name": b, "detail": c} for a, b, c in results],
             "processing_warnings": warnings},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

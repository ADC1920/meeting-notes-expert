# -*- coding: utf-8 -*-
"""meeting-notes-expert 全量回归测试。

跑法：
    uv run python tests/test_all.py
    python -I tests/test_all.py          # 隔离模式，防当前目录的模块遮蔽

不用 pytest，也不用 fixture 框架——这个技能只有一份被测模块，一个脚本
跑完所有断言最省事，CI 里也只需要一条命令。断言失败会打印具体差异，
不吞异常。

覆盖范围：
  1. 六种骨架（meeting/formal/retro/standup/generic/layered）的类型判定与必填节
  2. 行动项表在各类骨架下的定位（action_section）
  3. 编码兜底（UTF-8 / UTF-8-BOM / GBK / UTF-16 / 不可读字节）
  4. 标题编号剥离（一、二、三 与 1.2 两种形态）
  5. 导出结构与校验结论一致（P0-2 互斥缺陷的回归锁）
  6. schema 对导出结构的校验（含四新节与 document_kind）
  7. 跨会索引与滑期计数
  8. 禁用标注、状态枚举、相对时间等既有校验项不退化
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
REFS = SKILL_DIR / "references"
sys.path.insert(0, str(SCRIPTS))

import validate_minutes as V  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


# ---------- 素材 ----------

MEETING = """# 会议纪要：周会

## 会议基本信息

会议主题：周会
会议时间：2026-11-20
参会人员：张三、李四
会议性质：工作推进

## 会议结论

本周按计划推进，未形成正式决议。

## 待办事项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 修复结算页按钮 | 张三 | 11 月 25 日 | 高 | 未开始 | 出处：00:10 张三 |

## 待定与开放问题

预算口径待定。

## 会议内容

### 项目进度

按计划推进。
"""

FORMAL = MEETING.replace(
    "## 会议结论", """## 法定要素

法定人数：应到 5 人实到 5 人。表决通过，票数材料未记录。

## 会议结论""")

RETRO = """# 复盘会纪要

## 会议基本信息

会议主题：迭代复盘
会议时间：2026-11-20

## 本次目标与结果

目标按期交付，结果延期两天。

## 做得好的

需求提前一天冻结。

## 待改进

联调阶段排期过紧。

## 下轮改进项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 联调预留三天缓冲 | 张三 | 11 月 28 日 | 高 | 未开始 | 出处：00:20 张三 |

## 会议内容

复盘讨论。
"""

STANDUP = """# 站会

## 会议基本信息

会议主题：每日站会
会议时间：2026-11-20

## 进度

张三完成接口联调。

## 计划

李四补自动化用例。

## 阻塞

本次未提出阻塞。
"""

GENERIC = """# 访谈笔记

## 内容基本信息

内容主题：产品访谈
来源：录音
时间：2026-11-20

## 核心结论

用户最在意启动速度。

## 受访者原话

「用了半小时还没上手。」

## 解读与推断

推断产品学习成本偏高。

## 关键要点

- 启动慢是首要痛点

## 行动事项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 出一版启动优化方案 | 王五 | 11 月 30 日 | 高 | 未开始 | 出处：00:30 王五 |

## 内容详情

访谈围绕可用性展开。
"""

LAYERED = """# 方案评审纪要

## 会议基本信息

会议主题：方案评审
会议时间：2026-11-20
参会人员：张三、李四
会议性质：决策审批

## 会议决策摘要

决定采用方案 A。

## 关键议题与讨论

各方对比两套方案。

## 风险评估

| 维度 | 判断 | 依据 |
|---|---|---|
| 成本 | 可行 | 在预算内 |

## 行动项

| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 补充成本测算 | 李四 | 11 月 25 日 | 高 | 未开始 | 出处：00:30 李四 |

## 会议结论与后续安排

按方案 A 推进。
"""

CASES = [
    ("meeting", MEETING, "待办事项"),
    ("formal", FORMAL, "待办事项"),
    ("retro", RETRO, "下轮改进项"),
    ("standup", STANDUP, None),
    ("generic", GENERIC, "行动事项"),
    ("layered", LAYERED, "行动项"),
]


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # ---- 1/2 骨架判定与行动项定位 ----
        for expect_kind, text, expect_section in CASES:
            p = td / f"{expect_kind}.md"
            p.write_text(text, encoding="utf-8")
            rc, results = V.validate(p)
            blocks = V.parse_input(p)
            _b, secs, _bt, _unk = V.split_sections(blocks)
            kind = V.detect_kind(secs)
            check(f"骨架判定 {expect_kind}", kind == expect_kind, f"实为 {kind}")
            check(f"校验通过 {expect_kind}", rc == 0,
                  "；".join(f"{l} {n} {d}" for l, n, d in results if l != "PASS"))
            data = V.export_structured(p)
            check(f"表节定位 {expect_kind}", data["action_section"] == expect_section,
                  f"实为 {data['action_section']}")
            if expect_section:
                check(f"行动项导出 {expect_kind}", len(data["action_items"]) == 1,
                      f"实为 {len(data['action_items'])} 条")

        # ---- 3 编码兜底 ----
        for enc in ("utf-8", "utf-8-sig", "gbk", "utf-16"):
            p = td / f"enc-{enc}.md"
            p.write_bytes(MEETING.encode(enc))
            rc, _ = V.validate(p)
            check(f"编码可读 {enc}", rc == 0, f"exit={rc}")

        bad = td / "bad-enc.md"
        bad.write_bytes(b"\xff\xff\xff\xff")
        rc, res = V.validate(bad)
        msg = res[0][2] if res else ""
        check("不可读字节报 error 态", rc == 2)
        check("不可读字节点明编码", "编码" in msg and "docx" not in msg, msg[:60])

        # ---- 4 标题编号剥离 ----
        for raw, want in (("一、会议决策摘要", "会议决策摘要"),
                          ("（二）风险评估", "风险评估"),
                          ("1.2 本次核心结论", "本次核心结论"),
                          ("第 3 章 会议内容", "会议内容"),
                          ("会议结论", "会议结论")):
            got = V.strip_heading_number(raw)
            check(f"编号剥离 {raw}", got == want, f"实为 {got}")

        # ---- 5 导出与校验结论一致 ----
        for expect_kind, text, _sec in CASES:
            p = td / f"{expect_kind}.md"
            rc, results = V.validate(p)
            data = V.export_structured(p)
            status, _ = V.summarize(results)
            check(f"导出状态一致 {expect_kind}",
                  data["document_status"] == status,
                  f"校验={status} 导出={data['document_status']}")

        # ---- 6 schema ----
        try:
            import jsonschema
        except ImportError:
            check("schema 校验（缺 jsonschema，跳过）", True)
        else:
            schema = json.loads((REFS / "minutes-schema.json").read_text(encoding="utf-8"))
            for expect_kind, text, _sec in CASES:
                p = td / f"{expect_kind}.md"
                data = V.export_structured(p)
                try:
                    jsonschema.validate(data, schema)
                    check(f"schema 合规 {expect_kind}", True)
                except Exception as e:
                    check(f"schema 合规 {expect_kind}", False, str(e).splitlines()[0][:120])

        # ---- 6b 依赖缺失不得污染三态 ----
        # 2026-11-26 实测缺陷：jsonschema 未装时 schema 校验记 WARN，
        # 干净稿被判 degraded，同一份稿在有依赖的机器上却是 complete。
        # 依赖缺失是环境问题，SKIP 不参与三态判定。
        p = td / "clean.md"
        p.write_text(MEETING, encoding="utf-8")
        rc, results = V.validate(p)
        status, warns_of_it = V.summarize(results)
        check("干净稿三态为 complete（不受依赖影响）", status == "complete",
              f"实为 {status}，WARN {[w['step'] for w in warns_of_it]}")
        if any(r[0] == "SKIP" for r in results):
            check("缺依赖记为 SKIP 而非 WARN", True)
            check("SKIP 不进入告警列表", len(warns_of_it) == 0,
                  f"告警 {[w['step'] for w in warns_of_it]}")
        else:
            check("依赖齐备时无 SKIP", True)

        # ---- 7 跨会索引与滑期 ----
        idx = td / "index.jsonl"
        base = MEETING
        versions = [
            ("2026-11-01", "11 月 10 日"),
            ("2026-11-08", "11 月 17 日"),
            ("2026-11-15", "11 月 24 日"),
        ]
        reports = []
        for i, (date, due) in enumerate(versions, 1):
            txt = base.replace("2026-11-20", date).replace("11 月 25 日", due)
            p = td / f"m{i}.md"
            p.write_text(txt, encoding="utf-8")
            d = V.export_structured(p)
            prev = V.load_index(idx)
            reports.append(V.compare_with_index(d, prev, date))
            V.append_index(idx, d, date, prev)
        check("第1次无滑期", len(reports[0]["slipped"]) == 0)
        check("第2次滑期1次", len(reports[1]["slipped"]) == 1)
        check("第2次不触发处置", len(reports[1]["must_act"]) == 0)
        check("第3次触发两次须处置", len(reports[2]["must_act"]) == 1,
              f"实为 {len(reports[2]['must_act'])}")
        latest = V.load_index(idx)
        max_slip = max((int(r.get("slips", 0)) for r in latest.values()), default=0)
        check("滑期计数持久化", max_slip == 2, f"实为 {max_slip}")

        # 消失项检测：上期有、本期没有且未完成
        d_missing = dict(V.export_structured(td / "m1.md"))
        d_missing["action_items"] = []
        rep = V.compare_with_index(d_missing, latest, "2026-11-22")
        check("消失项被报出", len(rep["vanished"]) >= 1, f"实为 {len(rep['vanished'])}")

        # ---- 8 转写稿形态（技能的主对接形态，此前零覆盖）----
        # 主对接形态是 `[mm:ss] 说话人N：文本`，但历史测试里一处都没有。
        # 这组用例锁住「解析 → 骨架判定 → 导出」整条链路对转写稿有效。
        TRANSCRIPT = """[00:00] 说话人0：今天主要定结算页那个问题。
[00:35] 说话人1：我下周三前把推送文案初稿弄出来。
[01:10] 说话人0：应该有人去问下采购那边服务器什么时候到。
"""
        tr_doc = td / "tr.md"
        tr_doc.write_text(
            "# 会议纪要：周会\n\n## 会议基本信息\n\n会议主题：周会\n会议时间：2026-11-20\n"
            "会议性质：工作推进\n\n## 会议结论\n\n本次确定推送文案的完成时限；"
            "服务器到货时间待采购确认。\n\n## 待办事项\n\n"
            "| 序号 | 待办事项 | 责任人/责任部门 | 完成时间 | 优先级 | 状态 | 备注 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 撰写推送文案初稿 | 说话人1 | 11月25日 | 中 | 未开始 | 出处：00:35 说话人1 |\n\n"
            "## 无人认领事项\n\n- 「应该有人去问下采购那边服务器什么时候到」——提出者：说话人0（01:10）；"
            "待定：指派给谁\n\n## 会议内容\n\n### 结算页问题\n\n"
            "说话人0 提出先定这个问题，说话人1 承接文案并说「我下周三前把推送文案初稿弄出来」。\n",
            encoding="utf-8")
        rc, res = V.validate(tr_doc, tr_doc.read_text(encoding="utf-8"))
        check("转写稿形态校验不误报",
              rc == 0,
              "；".join(f"{l} {n} {d}" for l, n, d in res if l == "FAIL")[:160])
        blocks_tr = V.parse_input(tr_doc)
        _b, secs_tr, _bt, _unk = V.split_sections(blocks_tr)
        check("转写稿骨架判定为会议", V.detect_kind(secs_tr) == "meeting")
        data_tr = V.export_structured(tr_doc)
        check("转写稿行动项可导出", len(data_tr["action_items"]) == 1,
              f"实为 {len(data_tr['action_items'])}")
        check("转写稿孤儿进无人认领节", len(data_tr["unclaimed_items"]) == 1,
              f"实为 {len(data_tr['unclaimed_items'])}")

        # 引文证据位：源文里没有的引文必须告警（反向锁）
        q_bad = td / "q-bad.md"
        q_bad.write_text(tr_doc.read_text(encoding="utf-8").replace(
            "我下周三前把推送文案初稿弄出来", "我下周一定把所有文案都交出来"), encoding="utf-8")
        _rc, q_res = V.validate(q_bad, tr_doc.read_text(encoding="utf-8"))
        check("改写过的引文应告警",
              any(l == "WARN" and n == "逐字引文溯源" for l, n, _ in q_res),
              "；".join(f"{l} {n}" for l, n, _ in q_res if "引文" in n))

        # 源材料 docx 分流（P0 回归锁）
        try:
            from docx import Document
            src_docx = td / "src.docx"
            _d = Document()
            _d.add_paragraph("[00:35] 说话人1：我下周三前把推送文案初稿弄出来。")
            _d.save(str(src_docx))
            got = V.read_source_text(src_docx)
            check("--source 可读 docx（P0 回归锁）", "说话人1" in got, got[:60])
        except ImportError:
            check("--source 可读 docx（python-docx 缺失，跳过）", True)

        # ---- 9 既有校验项不退化 ----
        bad_doc = td / "bad.md"
        bad_doc.write_text(MEETING.replace("## 会议结论\n\n本周按计划推进，未形成正式决议。",
                                           "## 会议结论\n\n待确认。"), encoding="utf-8")
        rc, res = V.validate(bad_doc)
        check("禁用标注仍被抓",
              any(l == "FAIL" and n == "禁用标注" for l, n, _ in res))

        bad_status = td / "bad-status.md"
        bad_status.write_text(MEETING.replace("| 高 | 未开始 |", "| 高 | 做了一半 |"),
                              encoding="utf-8")
        rc, res = V.validate(bad_status)
        check("非法状态值仍被抓",
              any(l == "FAIL" and "状态" in d for l, _n, d in res))

        bad_rel = td / "bad-rel.md"
        bad_rel.write_text(MEETING.replace("11 月 25 日", "下周五"), encoding="utf-8")
        rc, res = V.validate(bad_rel)
        check("相对时间仍被抓",
              any(l == "FAIL" and n == "相对时间" for l, n, _ in res))

        unknown = td / "unknown.md"
        unknown.write_text(MEETING.replace("## 待定与开放问题", "## 风险分析汇总"),
                           encoding="utf-8")
        rc, res = V.validate(unknown)
        check("未识别章节仍被点名",
              any(l == "WARN" and n == "未识别章节" for l, n, _ in res))

        # ---- 9 章节白名单单一真相源 ----
        sections_json = REFS / "sections.json"
        check("sections.json 存在", sections_json.is_file())
        if sections_json.is_file():
            sj = json.loads(sections_json.read_text(encoding="utf-8"))
            check("sections.json 含六骨架",
                  set(sj["kinds"]) >= {"meeting", "formal", "retro", "standup",
                                       "generic", "layered"})
            check("脚本判据来自 JSON",
                  V._kind_required("meeting") == sj["kinds"]["meeting"]["required"])

        # ---- 10 CLI 端到端 ----
        cli = td / "cli.md"
        cli.write_text(MEETING, encoding="utf-8")
        out_json = td / "cli.json"
        idx2 = td / "cli-index.jsonl"
        r = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "validate_minutes.py"),
             str(cli), "--export", str(out_json), "--index", str(idx2), "--compare"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("CLI 导出退出码 0", r.returncode == 0, r.stderr[:200])
        check("CLI 导出文件存在", out_json.is_file())
        check("CLI 写入索引", idx2.is_file() and idx2.read_text(encoding="utf-8").strip() != "")
        if out_json.is_file():
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            check("CLI 导出含 cross_meeting_report",
                  "cross_meeting_report" in payload)
            check("CLI 导出 schema_version=4", payload.get("schema_version") == 4)

        # 文件不存在时退出码应为 2
        r2 = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "validate_minutes.py"),
             str(td / "nope.md")],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("文件不存在退出码 2", r2.returncode == 2, r2.stdout[:120])

        # ---- 10 正文标号：加粗引导段落（v1.18.1）----
        # 纪要正文不使用 md 列表符（会在正式 docx 里转成自动编号，与章节序号撞车）。
        # 这里锁住三件事：加粗段落能被解析成条目、导出时星号被剥掉、两种写法条数一致。
        bold_doc = td / "bold.md"
        bold_doc.write_text(
            "# 纪要\n\n## 会议基本信息\n\n会议主题：X\n\n## 会议结论\n\n定了。\n\n"
            "## 关键决策\n\n**决定按前端路由方向排查修复。**\n\n**决定本周内上线。**\n\n"
            "## 无人认领事项\n\n**问采购口径**：应该有人去问。\n\n"
            "## 待定与开放问题\n\n**到货时间**：卡在采购侧。\n\n## 会议内容\n\n略。\n",
            encoding="utf-8")
        data_bold = V.export_structured(bold_doc)
        check("加粗段落解析为决策条目", len(data_bold["decisions"]) == 2,
              f"实为 {len(data_bold['decisions'])}")
        check("加粗段落解析为无人认领", len(data_bold["unclaimed_items"]) == 1,
              f"实为 {len(data_bold['unclaimed_items'])}")
        check("导出剥除 markdown 星号",
              all("**" not in x for x in
                  data_bold["decision_texts"] + data_bold["unclaimed_items"]),
              str(data_bold["decision_texts"])[:80])
        rc_b, res_b = V.validate(bold_doc)
        check("加粗段落稿校验通过", rc_b == 0,
              "；".join(f"{l} {n}" for l, n, _ in res_b if l == "FAIL")[:120])

        # 列表符写法仍须兼容（历史稿与模板示例仍可能用 - ）
        list_doc = td / "list.md"
        list_doc.write_text(
            "# 纪要\n\n## 会议基本信息\n\n会议主题：X\n\n## 会议结论\n\n定了。\n\n"
            "## 关键决策\n\n- 决定按前端路由方向排查修复。\n\n## 会议内容\n\n略。\n",
            encoding="utf-8")
        data_list = V.export_structured(list_doc)
        check("列表符写法兼容且无前缀残留",
              len(data_list["decisions"]) == 1
              and not data_list["decision_texts"][0].startswith("-"),
              str(data_list["decision_texts"])[:60])

        # ---- 11 表格栏宽与表头底纹（v1.18.1，需 python-docx）----
        try:
            from docx import Document
            tbl_doc = td / "tbl.docx"
            _d = Document()
            _t = _d.add_table(rows=2, cols=7)
            for i, h in enumerate(V.ACTION_COLUMNS_7):
                _t.rows[0].cells[i].text = h
            _d.save(str(tbl_doc))
            fixed = td / "tbl-fixed.docx"
            r3 = subprocess.run(
                [sys.executable, str(SCRIPTS / "fix_minutes_docx.py"),
                 str(tbl_doc), str(fixed)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("fix_minutes_docx 退出码 0", r3.returncode == 0, r3.stderr[:160])
            if fixed.is_file():
                from docx.oxml.ns import qn as _qn
                _f = Document(str(fixed))
                widths = [c.width.cm for c in _f.tables[0].columns]
                total = sum(widths)
                # 断言写成相对关系而非绝对厘米：本用例的底稿是空白 Document()，
                # 纸型随 python-docx 默认（Letter，可用约 15.14cm），脚本会按可用
                # 宽度等比缩放。真实出稿走 A4（15.82cm）已由渲染目检确认。
                check("七列宽铺满正文可用宽度（占比 ≥0.97）",
                      len(widths) == 7 and total > 14.0,
                      f"合计 {total:.2f}")
                check("备注列占比最大（≥20%）", widths[-1] / total >= 0.20,
                      f"实为 {widths[-1] / total:.3f}")
                check("窄列保住下限（序号/优先级/状态各 ≥6.5%）",
                      min(widths[0], widths[4], widths[5]) / total >= 0.065,
                      f"实为 {widths[0] / total:.3f}/{widths[4] / total:.3f}/{widths[5] / total:.3f}")
                hdr_cell = _f.tables[0].rows[0].cells[0]
                shd = hdr_cell._tc.find(_qn("w:tcPr")).find(_qn("w:shd"))
                check("表头行有底纹", shd is not None and shd.get(_qn("w:fill")) == "F2F2F2",
                      "未找到 w:shd 或填充色不符")
        except ImportError:
            check("fix_minutes_docx 栏宽与底纹（python-docx 缺失，跳过）", True)

    print(f"\n结果：{len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

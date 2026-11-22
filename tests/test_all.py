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

        # ---- 8 既有校验项不退化 ----
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
            check("CLI 导出 schema_version=3", payload.get("schema_version") == 3)

        # 文件不存在时退出码应为 2
        r2 = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "validate_minutes.py"),
             str(td / "nope.md")],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("文件不存在退出码 2", r2.returncode == 2, r2.stdout[:120])

    print(f"\n结果：{len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

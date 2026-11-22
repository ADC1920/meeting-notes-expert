# meeting-notes-expert

把会议录音转写、访谈、播客、讲座等材料整理成结构化纪要的 AI 技能（Claude / ZCode / Codex 等 SKILL.md 体系通用）。

章节按会议性质与内容有无动态取舍：空节整节删除，不写占位句。

## 这是什么

一个可独立安装的技能。输入是会议原文（带时间戳与说话人的转写稿）或任何讨论记录，输出是结构化的纪要 docx。

它**不负责转写**。转写由上游管线完成，本技能只做提炼与校验。上游产物只要是「每段带 `[mm:ss] 说话人N：` 前缀的文本」或普通 md/docx 记录，都能直接进。

## 安装

把整个目录放到你的技能目录下，任选其一：

```
~/.zcode/skills/meeting-notes-expert
~/.agents/skills/meeting-notes-expert
~/.claude/skills/meeting-notes-expert
```

技能会自动被该目录体系的宿主加载。无需编译、无需安装依赖包。

## 依赖

| 依赖 | 用途 | 缺失时 |
| --- | --- | --- |
| Python 3.10+ | 跑 scripts 下的脚本 | 无法出稿校验 |
| `python-docx` | 读写 docx | `fix_minutes_docx.py` 不可用 |
| `pymupdf` | PDF 转 PNG 做渲染目检 | 跳过渲染目检 |
| `jsonschema` | 按 schema 校验导出结构 | 该项降级为 WARN |
| `article-format` 技能 | docx 排版规范（标题/字体/编号/A4） | 只能出 md，无规范 docx |

前三项与 `jsonschema` 见 `requirements.txt`（`uv pip install -r requirements.txt`）。
`article-format` 是另一个独立技能，装到 `~/.agents/skills/article-format` 即可。本技能通过 `AF_SCRIPTS` 环境变量定位它的脚本，不假定固定路径。

脚本主体只用标准库：`validate_minutes.py` 不依赖任何第三方包即可跑校验与导出，`jsonschema` 缺失时 schema 项降级为 WARN 而不是报错。

## 用法

出稿分两步：**提炼**（AI 按 SKILL.md 写 md）与**出稿**（脚本转 docx 并校验）。

```bash
# 先设路径（装在哪就指哪）
export MNX_SCRIPTS="$HOME/.zcode/skills/meeting-notes-expert/scripts"
export AF_SCRIPTS="$HOME/.agents/skills/article-format/scripts"

# 出稿 + 校验
cd <纪要工作目录>
python "$AF_SCRIPTS/md2docx.py" 纪要.md 中间.docx --profile formal-doc
python "$AF_SCRIPTS/article_style.py" 中间.docx 纪要.docx --profile formal-doc
python "$MNX_SCRIPTS/fix_minutes_docx.py" 纪要.docx 纪要-终.docx
python "$MNX_SCRIPTS/validate_minutes.py" 纪要-终.docx --source 会议原文.txt
```

`md2docx.py` 的输出只取文件名并强制落在当前工作目录，所以必须先 `cd` 到目标目录再跑。

## 结构化导出与跨会跟踪

```bash
# 导出机器可读 JSON（骨架类型、行动项、决策、四新节、文档状态）
python "$MNX_SCRIPTS/validate_minutes.py" 纪要-终.docx --export 纪要.json

# 写入跨会索引并与历史比对滑期
python "$MNX_SCRIPTS/validate_minutes.py" 纪要-终.docx \
  --index 纪要索引.jsonl --compare --export 纪要.json
```

导出结构由 `references/minutes-schema.json` 定义。`document_kind` 与 `document_status` 与校验器同源，不会出现「校验 PASS、导出 failed」。`--compare` 输出 `cross_meeting_report`：`slipped` 滑期明细、`must_act` 累计滑期两次须处置、`vanished` 上期有本期消失。

## 文件结构

```
SKILL.md                    主流程与判据（AI 读这个）
LICENSE                     MIT
requirements.txt            可选依赖清单（脚本主体只用标准库）
references/
  sections.json             章节白名单单一真相源（脚本与文档都引用）
  templates.md              各类型骨架、会议亚型、输出变体
  extraction.md             待办提取口径、孤儿任务判别、字段填法
  formatting.md             排版映射、出稿链、四层验证
  minutes-schema.json       结构化导出 schema（v3）
  example-minimal.md        最小成稿示例
scripts/
  validate_minutes.py       结构与溯源校验、结构化导出、跨会索引（含 --selftest 23 项）
  fix_minutes_docx.py       待办表版式后处理
  rasterize_pdf.py          PDF 逐页转 PNG
  docx2pdf_wps.ps1          WPS 导出 PDF（Windows）
tests/
  test_all.py               全量回归（六骨架/编码/编号/导出/schema/索引）
CHANGELOG.md                版本变更
```

## 自检

```bash
# 校验器内置断言
python "$MNX_SCRIPTS/validate_minutes.py" --selftest

# 全量回归（65 项）
python -I tests/test_all.py
```

`--selftest` 23 项覆盖必需节、待办列、状态枚举、溯源、未识别章节、编码兜底、骨架导出、跨会滑期。`tests/test_all.py` 65 项覆盖六类骨架的判定与导出、四种编码、标题编号剥离、schema 合规、CLI 端到端。

## 版本

当前 v1.16.0。变更历史见 `CHANGELOG.md`。

## 与其他项目的关系

本技能是独立项目，不依赖任何特定仓库。它可以被装进某个项目里随项目分发，也可以单独安装到用户级目录——两种方式下命令与行为一致。

它认识的上游只有一件事：转写产物长什么样（`[mm:ss] 说话人N：` 前缀）。任何生成这种格式的工具都能接上，不需要是本项目。

## 许可

MIT

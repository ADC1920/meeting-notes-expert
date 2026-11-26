# 排版与出稿链

纪要排版完全走 `article-format` 技能，本技能不复制其字体字号取值。
**规格唯一权威源**为 article-format 技能目录下的 `profiles/<profile>.yaml`（v2.1 起基线 `article-format.yaml` 只供继承，不对外暴露），改规格只改对应 yaml。

**version 1.17.0 起不再固定 `formal-doc`**：profile 由提炼者在写稿前按纪要的性质与结构判一次，判定结果写进交付说明。判据见下节「profile 自选」。v2.1 起 article-format 不提供隐式默认，**不带 `--profile` 一律报错退出（exit 2）**——所以必须判，不能不判。

## profile 自选（写稿前判一次）

判据复用本技能已有的「会议性质」四分类：性质本身就编码了「谁来读、读完要做什么」，而这两点正是版式该跟随的东西。

| 会议性质 | 读者与用途 | profile | 版式差异 |
| --- | --- | --- | --- |
| 决策审批 | 领导层复核拍板，需快速定位决议与责任 | `enterprise-doc` | 1.5 倍行距、中文层次序数（一、/（一）），条文感强 |
| 工作推进 | 执行层照着干，待办表是主体 | `enterprise-doc` | 同上，表格密度更高 |
| 汇报通报 | 单向传达，通读一遍 | `business-report` | 1.5 倍行距、数字式编号、章标题居中 |
| 研讨交流 | 交换意见，无决议，读者回看观点 | `formal-doc` | 1.3 倍行距、数字式编号，紧凑 |

**四条附加规则**（优先级高于上表）：

1. **正式会议**（有表决、具法律效力）一律 `enterprise-doc`，它的层次序数最适合逐条引用动议。
2. **复盘会与站会**用 `formal-doc`：篇幅短、无表决，公文式序数反而增重。
3. **通用总结**（访谈、播客、讲座、单人汇报）按内容定：访谈/播客这类要引原话、读者当文章读的用 `formal-doc`；讲座/课程这类要当资料回查、术语多的用 `tech-doc`（无编号标题、左对齐，便于检索）。
4. **同一批系列会议保持同 profile**：同一项目连续多次出纪要时沿用上一份的选择，避免读者在系列里切换版式。跨会索引里已有记录时以索引里的 profile 为准。

**判不准时的处置**：默认 `formal-doc`（它在 article-format 里是「其他文体」兜底），并在交付说明里写明「按通用正式文体排版」。**不要为了显得正式就选 gov-official**——那是党政机关法定文种格式，企业纪要用它属于错配。

## 路径约定（本技能独立安装，不在任何特定仓库内）

本技能可作为独立技能安装到任意位置，因此下面所有命令都通过两个环境变量定位脚本，不写死仓库相对路径。先设置一次：

```bash
# 本技能自己的 scripts 目录（装在哪就指哪）
export MNX_SCRIPTS="$HOME/.zcode/skills/meeting-notes-expert/scripts"
# article-format 技能的 scripts 目录
export AF_SCRIPTS="$HOME/.agents/skills/article-format/scripts"
```

Windows PowerShell 用 `$env:MNX_SCRIPTS = "$HOME\.zcode\skills\meeting-notes-expert\scripts"`。
如果本技能被装在项目仓库里（`<repo>/skills/meeting-notes-expert`），把 `MNX_SCRIPTS` 指到那里即可，命令本身不用改。

后文命令一律写作 `"$MNX_SCRIPTS/xxx.py"` 与 `"$AF_SCRIPTS/xxx.py"`。

## 纪要落稿映射

- 主标题（纪要标题）用 `# `，不进导航树。
- 各章节标题用 `## ` 起头，**不写编号**：中文「一、」脚本不剥离，会与 Word 自动编号叠成双重编号；写阿拉伯数字会被剥离后重排——一律不写最稳。章节序号由 Word 自动多级编号生成（1 / 1.1 / 2 …）。
- 议题小节用 `### `，再细分逐级用 `#### `。
- 正文加粗写 `**重点**`（正文、编号列表、表格单元格均渲染为真加粗）。
- 待办事项用标准 Markdown 表格（**七列**：序号、待办事项、责任人/责任部门、完成时间、优先级、状态、备注；正式会议加「对应动议」列）；正文编号列表行首写 `1. `（转 Word 自动编号、顶格）。

## 出稿链

先把判定结果记进环境变量 `PROFILE`，下面三步共用同一个值——**三处必须一致**，出稿与校验用不同 profile 会误报一堆版式 FAIL。

```
# 0) 先按「profile 自选」判一次，把结果记下来（示例：工作推进型会议）
PROFILE=enterprise-doc
# 0b) 先 cd 到纪要工作目录——md2docx.py 的输出只取文件名并强制落在当前工作目录
#    （上游防路径穿越设计），传绝对路径的目录部分会被静默丢弃，务必按此写法
# 1) md → docx 骨架
python "$AF_SCRIPTS/md2docx.py" 纪要.md 中间.docx --profile "$PROFILE"
python "$AF_SCRIPTS/article_style.py" 中间.docx 纪要.docx --profile "$PROFILE"
# 2) 待办表格版式（固定列宽、单元格缩进清零、防跨页断行、表头跨页重复）
python "$MNX_SCRIPTS/fix_minutes_docx.py" 纪要.docx 纪要-终.docx
```

**换 profile 要改源稿重跑 md2docx，不要拿成品重排**——`article_style.py` 只换样式，已固化的列表编号与表格版式不会跟着变。`fix_minutes_docx.py` 的列宽与 profile 无关（只按表格列数分配），换 profile 不用重调。

## 交付前验证（四层）

1. **结构与溯源**：`python "$MNX_SCRIPTS/validate_minutes.py" 纪要-终.docx --source 会议原文.txt`
   查必需节、待办七列与字段、状态枚举、决策句式、禁用标注、数字/日期/责任人/出处溯源、软承诺强度、说话人归属；通用总结另判章节时间戳与术语定义。有源材料时必跑，`--strict` 让告警也阻断；脚本自带 `--selftest`。
   输出带三态：`complete` 全过、`degraded` 无 FAIL 但有 WARN、`failed` 有 FAIL。改动提炼口径后跑一次 `--selftest` 确认校验逻辑本身没退化；改骨架或章节名后跑 `tests/test_all.py`（六类骨架、编码、编号、导出、索引全量回归）。六列旧件只告警不判错。

   **需要机器可读产物时加 `--export`**：`--export 纪要.json` 按 `minutes-schema.json` 导出行动项、决策与四新节结构化数组；`--index 索引.jsonl --compare` 会与历史快照比对滑期，结果写进 `cross_meeting_report`。导出的 `document_kind` 与 `document_status` 和校验器同源，不会出现「校验 PASS、导出 failed」的互斥结论。

2. **结构断言**：`python "$AF_SCRIPTS/verify_article_style.py" 纪要-终.docx --profile "$PROFILE"`（约 101-108 条，随 profile 与表格数浮动；含纸型 A4 / 页边距 / 无字面 `**` 残留）。**必须传与出稿相同的 profile**，传错会报大量版式 FAIL。

3. **XML 合法**：`officecli validate 纪要-终.docx`（要求 0 错误）。**WPS 保存过的文件会报 `uiPriority`/`themeFontLang` 元素顺序告警——这是 WPS 的写法不是文件损坏**：与编辑前备份对照，备份同报即放行；仅 document.xml 正文结构错误才算失败。

4. **真机渲染**：WPS 导出 PDF 后目检。本技能附渲染脚本——`scripts\docx2pdf_wps.ps1`（`pwsh -File ... -Src <docx 绝对路径> -Dst <pdf 绝对路径>`，**两参数必须绝对路径**，相对路径会让 WPS COM 报「RPC 服务器不可用 0x800706BA」崩溃）与 `scripts\rasterize_pdf.py`（PDF 逐页转 PNG）。重点看表格列宽无截断、表头跨页重复、无字面星号；缺陷修好后重新出稿复验。续改模式只需人名与数字一致性复扫加渲染目检，不必重跑全套结构断言。

## 续改模式（用户改稿后再调整）

用户已在交付稿上手工修改后，**以用户改稿为唯一基线就地编辑**（python-docx / OOXML run 级替换，保留段内局部加粗与版式），**禁止按原稿重新生成覆盖**。

编辑前先备份到系统临时目录（不留在交付目录）；多处文本替换时先长串后短串（防止短串先执行拆掉长串的匹配），改完复扫人名、数字与口径一致性并渲染目检。

用户就纪要细节追问时（如「这个数字他当时怎么说的」「这条是谁提的」），先回原始材料核对再回答，回答区分「材料明确说过／据上下文推断／材料未提及」三种口径；涉及人名、金额、日期、责任归属的追问必须核对原文，不凭印象作答。

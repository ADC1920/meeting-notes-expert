# 会议纪要专家（meeting-notes-expert）

一个面向 AI Agent 的**会议纪要整理技能**：把会议录音转写稿、手写笔记、讨论记录、聊天记录等原始材料，整理为完整、清晰、可直接归档执行的结构化会议纪要。

> 当前版本：v1.4.0 ｜ 适用平台：ZCode / Claude Code 及其他支持 Agent Skills（SKILL.md）规范的助手 ｜ License: [MIT](LICENSE)

---

## ✨ 功能特性

- **五段式标准结构**：会议基本信息 → 会议内容 → 核心要点 → 会议总结 → 待办事项，一次成文、格式统一。
- **结论先行**：会议总结首段用不超过三行概括「本次会议最终定了什么」，未参会的人读完一段即可掌握全局。
- **一个议题只说一次**：讨论细节在「会议内容」闭环，一句话提炼归「核心要点」，成果决策收束到「会议总结」（决策条目唯一归该节），同一事实不在多个章节重复展开。
- **决策可审计**：关键决策逐条标注状态（已定 / 待定 / 待确认）并用肯定句式表述；重大决策可展开依据链（背景 / 备选方案 / 理由）；反对与保留意见如实记录，不因决策已通过而略去；单个与会者的建议未经他人接受为共同计划的不写成决议。
- **待办可直接执行**：七列表格（序号 / 待办事项 / 责任人 / 完成时间 / 优先级 / 验收标准 / 备注）；待办动词开头、具体可执行；从口语记录提取字段有识别线索表与负向过滤（表态句不提取、建议不升格为任务、请求方向核对）；缺失信息标「待定」不推断。
- **说话人可核验**：推定身份后做一致性核验，归属存疑则如实标注；匿名标签可摘录典型发言请用户确认。
- **忠实记录，绝不脑补**：一切陈述可追溯至原始记录；材料不完整（截断 / 缺失 / 标签靠推定）显著标注来源可信度；信息密度高时保留重要信息优先于压缩比例。
- **统一文章排版**：排版遵循全局文章排版规范（黑体标题 / 宋体正文 / Word 自动编号与导航目录），附表格版式后处理脚本与三层交付验证（结构断言 / XML 校验 / 真机渲染）。
- **媒体管线联动**：与本地音视频转写管线（拆轨 → 人声分离 → 说话人分离转写）产物直接对接，识别 `会议原文-*.md` 等三类转写产物，只提炼不重复转写。

## 📋 输出模板

纪要固定包含以下五个部分（章节标题不写编号，序号由 Word 自动生成）：

| 部分 | 呈现方式 | 说明 |
| --- | --- | --- |
| 一、会议基本信息 | 逐行文字罗列（严禁表格） | 会议主题 / 时间 / 地点 / 参会人员 / 记录人等，未提供的标「待确认」 |
| 二、会议内容 | 按议题分节 | 议题名称 + 主要讨论内容 + 关键发言要点，忠实原始记录 |
| 三、核心要点 | 逐条一句话 | 关键结论与重要信息，只提炼不展开；决策类条目归「会议总结」 |
| 四、会议总结 | 结论先行 + 决策清单 | 决策标注状态与依据，重大决策附依据链，异议如实记录；待定事项与开放问题单列（待决什么 / 卡在哪 / 谁推进） |
| 五、待办事项 | 规范表格 | 含验收标准列，便于按责任人、完成时间、验收口径跟踪分摊 |

最小成稿示例（源记录与成稿对照）见 [`references/example-minimal.md`](references/example-minimal.md)。

## 🚀 安装

整目录克隆并放入你的 Agent 技能目录（`references/` 提供成稿示例，`scripts/` 提供 docx 表格版式后处理与渲染辅助）：

```bash
git clone https://github.com/ADC1920/meeting-notes-expert.git
```

| 平台 | 技能目录 |
| --- | --- |
| ZCode | `~/.zcode/skills/meeting-notes-expert/` |
| Claude Code | `~/.claude/skills/meeting-notes-expert/` |

> 只需要核心提炼能力时，单独拷贝 `SKILL.md` 也可用（排版出稿与表格后处理需要 `scripts/` 配合）。

## 💬 使用方法

技能支持指令触发与自然语言触发，安装后对 Agent 说：

```text
帮我整理一下会议纪要，材料在 meeting-notes.md
把这段会议记录提炼成正式纪要，重点提取待办事项
整理今天的周会纪要，录音在 audio/20260918周会.m4a
散会了，出个纪要
```

### 音频输入（可选）

如果提供的是**会议录音**（`.wav / .m4a / .mp3 / .flac / .aac` 等），推荐先用音视频转写管线（如 [av-to-transcript-and-minutes](https://github.com/ADC1920/av-to-transcript-and-minutes)：拆轨 → 人声分离 → 说话人分离转写）或 `funasr-transcribe` 转写技能产出文稿，再交给本技能提炼——本技能只做提炼，不重复转写。管线 `--meeting` 模式产出的 `会议原文-*.md`（带时间戳与说话人标签）是本技能的主对接形态。纯文本材料无需任何额外依赖。

## 📝 更新日志

详见 [CHANGELOG.md](CHANGELOG.md)。摘要：

- **v1.4.0（2026-10-07）**
  - 参考 [tldr-recap](https://github.com/ItayMendelson/tldr-recap) 吸收六条提炼纪律：说话人一致性核验、个人建议≠决议、措辞强度对齐、请求方向核对、来源可信度注记、用户强调指令优先；新增最小成稿示例。
- **v1.3.0（2026-10-07）**
  - 接入本地音视频-文稿工作流：识别三类转写产物（会议原文 / 转写文稿 / 裸转写），只提炼不重复转写。
- **v1.2.x（2026-10-07）**
  - v1.2.0 参考 [decision-log](https://github.com/zrosenfield/sharepoint-ai-skills)、[meeting-notes](https://github.com/alapha888/agent-skills-en)、[meeting-notes-to-action-items](https://github.com/tomique34/claude-skill-creator) 完成八处升级（结论先行 / 决策依据链与异议 / 待定与开放问题 / 识别线索表 / 自检与反模式清单等）；v1.2.1 经对抗审查修复两条规则冲突并统一「待定 / 待确认」边界。
- **v1.1.0（2026-09-22）**
  - 全程中文强制（含工具调用间过程叙述）；章节标题改自动编号；补全出稿链与三层交付验证；新增转写稿处理节；排版规格切换为全局文章排版规范并回指唯一权威源。
- **v1.0（2026-09-18）**
  - 初版发布：五段式输出模板、录音转写联动。

## 🙏 致谢

本技能 v1.1 合入的三点写作纪律（议题聚合去重、决策状态 + 依据、待办验收标准），参考了 [mohui233/meeting-minutes](https://github.com/mohui233/meeting-minutes) 的设计思路；v1.2 与 v1.4 的提炼纪律参考了 [zrosenfield/sharepoint-ai-skills](https://github.com/zrosenfield/sharepoint-ai-skills)（decision-log）、[alapha888/agent-skills-en](https://github.com/alapha888/agent-skills-en)、[tomique34/claude-skill-creator](https://github.com/tomique34/claude-skill-creator) 与 [ItayMendelson/tldr-recap](https://github.com/ItayMendelson/tldr-recap)。感谢开源社区贡献。

## 📄 许可

本项目采用 [MIT License](LICENSE) 开源：可自由使用、修改、再分发（含商用），只需保留原版权与许可声明；软件按「现状」提供，不含任何担保。

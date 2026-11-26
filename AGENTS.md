# 项目规则 · meeting-notes-expert

通用规则见全局 `~/.zcode/AGENTS.md`。

## 核心资产

| 资产 | 位置 | 说明 |
|---|---|---|
| 技能主文件 | `SKILL.md` | 提炼口径、章节取舍、质量自检、工作流程 |
| 章节白名单 | `references/sections.json` | 六类骨架的必需/条件/可选章节单一真相源，校验与导出共用 |
| 出稿链细则 | `references/formatting.md` | profile 自选判据、docx 生成、四层验证 |
| 待办提取口径 | `references/extraction.md` | 行动项 / 软承诺 / 孤儿 / 开放问题的判别 |
| 校验器 | `scripts/validate_minutes.py` | 结构校验、结构化导出、跨会索引；带 `--selftest` |
| 待办表版式 | `scripts/fix_minutes_docx.py` | 固定列宽、跨页断行、表头重复 |
| 全量回归 | `tests/test_all.py` | 65 项，六类骨架 / 编码 / 编号 / 导出 / 索引 |

## 分支与推送

**本仓一律走 PR，不直接推 main**（2026-11-26 用户指令）。

1. 从最新 `main` 建分支：`feat/` `fix/` `refactor/` + 简短描述
2. 本地提交，跑门禁（见下）
3. 推分支 → `gh pr create --base main`
4. 等 CI 或本地等价检查全绿后再合，合并后删源分支

`gh pr merge --delete-branch` 在本账号下不生效（实测两次），合并后须手工 `git push origin --delete <分支>`。

## 提交前门禁

改动技能内容后依次跑，全部 exit 0 才算可提：

```
python tests/test_all.py
python scripts/validate_minutes.py --selftest
```

改动 `references/formatting.md` 的 profile 判据或出稿链时，另需在本机 `article-format` 技能目录跑：

```
python scripts/run_profile_regression.py
```

## 版本与变更记录

`SKILL.md` front-matter 的 `version` 与 `CHANGELOG.md` 首条版本号必须一致。改口径、加章节、改出稿链都要升版本并写变更记录（动因 / 改动 / 验证）。

## 本机专属内容禁提交

真实会议原文、纪要成品、录音、`hotwords.txt` 类业务词表、含本机路径的接续记录，一律不入库。样例材料用脱敏构造的 fixture 放 `tests/`。

## 安装副本同步

本仓是独立技能仓库，实际生效的是 `~/.zcode/skills/meeting-notes-expert`。改完本仓要同步过去，用主仓的 `scripts/check_skill_sync.py`（`--fix` 可覆盖）核对两处一致；只改用户级副本会导致下次重装丢失改动。

## 文档

- 用法与触发场景：`README.md`
- 版本历史：`CHANGELOG.md`

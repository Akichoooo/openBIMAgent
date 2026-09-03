# skills/ — Agent 技能库（P0-1，agentskills.io 兼容）

每个技能一个目录：`{name}/SKILL.md` = YAML frontmatter（`name`/`description`/`when_to_use`/`tools` 可选）+ Markdown 正文。

- **渐进披露**：上下文只注入目录（name+description）；`POST /api/v1/skills/invoke` 才返回正文——上下文成本按使用付费。
- **发现源**：本目录（builtin）+ `domain_packs/*/skills/`（包级）+ `OPENBIMAGENT_SKILLS_DIR`（外部）。
- **失败关闭**：frontmatter 缺键/名称非法/正文为空即拒载（计入 `GET /api/v1/skills` 的 `rejected`），不拖垮全局。
- **自蒸馏**：运行成功交付后自动生成草稿进 `_candidates/`（**永不自动生效**），人工批准（`POST /api/v1/skills/candidates/approve` 或前端 `/skills` 面板按钮）转正为 `{name}/SKILL.md`。

测试沙箱：`OPENBIMAGENT_SKILLS_ROOT` 覆盖本根目录（含候选区），防污染仓库。

详见 `docs/architecture/AGENT_CORE_ENHANCEMENT.md` §5。

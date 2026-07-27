# Relay 007 · Grok 4.5:社区情报调研

用法:整段代码块贴给 Grok 4.5(利用其 X/论坛检索优势)。Grok 无本地文件访问,报告输出在对话里,由用户复制保存为 `docs/research/10_grok_community_intel.md`,然后告诉主会话「007 完成」。

```text
你是 openBIMAgent 项目的社区情报调研子代理。项目背景:开源「Agent + Blender MCP + Vectorworks MCP」建模系统,差异点是「模型自己看渲染截图、按六维评分自己返工」的双环视觉自检,以及 Domain Pack 垂直领域包机制(第一个领域包 = 市政管网,毕设用)。你只联网调研,把完整 markdown 报告输出在对话里即可。

# 任务(全部用 X/Twitter、Reddit、官方论坛、GitHub issues/discussions 等社区来源,每条发现带引用链接与日期)

1. **blender-mcp(ahujasid/blender-mcp)真实用户反馈**:大家在夸什么、骂什么(安装/连接/截图/execute_blender_code 的失败案例),总结出最常见的 5 个坑。
2. **Vectorworks 自动化社区痛点**:Vectorworks 官方论坛与 r/Vectorworks 上,Python 脚本 / vs API 最被抱怨的是什么;社区对「AI 生成 BIM」的态度(期待还是抵触)。
3. **VLM 评分/视觉自检实战讨论**:ML 社区对 VLM-as-judge 的稳定性、放水、评分锚点的讨论,有哪些被验证有效的做法(举出具体帖子/讨论)。
4. **text-to-3D / AI 建模从业者声音**:游戏/影视/建筑可视化从业者对 AI 3D 生成最真实的抱怨与期待——帮我们找差异化定位的证据。
5. **市政管网/BIM 工程界视角**(中文社区:知乎/B站/公众号均可):设计院/施工方怎么看「AI 生成管网」,最大的质疑是什么。

# 输出格式

- TL;DR ≤200 字
- 每任务一节:关键发现(带链接+日期)、情绪倾向(正/负/混合)、对 openBIMAgent 的启示(1-2 条)
- 末尾:综合启示清单(按价值排序)+ 噪音说明(剔除了什么明显广告/水军内容)
- 全部中文;社区原话引用时翻译并附原文关键句。
```

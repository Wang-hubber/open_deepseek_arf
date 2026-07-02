# ARF 入门教程

> 🎯 Diátaxis 桶位：**Tutorials**（手把手引导，从零到可运行示例）

按学习路径递进，每篇 5-10 分钟可走通。所有教程内嵌完整可运行代码，使用真实的 LLM provider 与 MCP 节点。

## 阅读顺序

| # | 教程 | 配套源码 | 关键概念 |
|---|------|---------|---------|
| 1 | [hello.md](hello.md) | `## 代码` 段 | 4 个内置 ModelAdapter provider + OpenAI/Anthropic 兼容协议；一次 `engine.run()` 跑通 |
| 2 | [conversation.md](conversation.md) | `## 代码` 段 | 真实 MiniMax + 本地 MCP 工具节点（`McpNode.local`） + ReAct Reason→Act→Observe 闭环 |
| 3 | [tools.md](tools.md) | `## 代码` 段 | 在 ch2 基础上 + 远程 MCP 节点（`McpNode.remote`） — 完整 Bus / AgentConfig / Engine / ModelAdapter / MCP (Local & Remote) 闭环 |

## 选择 LLM Provider

ARF 不绑定 provider — 3 选 1 即可。按推荐顺序：

### 1. DeepSeek（推荐新手起手）🐳
- 文档：<https://api-docs.deepseek.com/zh-cn/>
- 起步：充个 10 块够用很久
- 文档对小白最友好，API 响应速度也快
- 江湖人称"邪恶小鲸鱼"，入口最干净

### 2. MiniMax（TokenPlan 重度用户）
- 官网：<https://www.minimaxi.com/>
- 适合：包月订阅 + 用量较大
- 注意：API 接口文档藏得深，从首页进入需多走几步

### 3. 阿里百炼（有免费额度，慎用）
- 控制台：<https://bailian.console.aliyun.com/cn-beijing?tab=home#/home>
- 适合：纯尝鲜、不想立刻充钱
- ⚠️ **坑点**：必须从"免费模型列表"中挑选标注了免费额度的模型，否则会欠费 — 第一次调用就可能花一毛钱，即便不充钱也会欠费
- 文档入口弯弯绕绕，对初学者不友好

> 本系列教程以 **MiniMax** 为示例（用 `MINIMAX_API_KEY` 环境变量）。切换 provider 只需改 2 行 — 见各章节 `## 切换 Provider` 段。

## 教程结构

每篇教程 4 段：

1. **为什么** — 这一节解决什么问题、与上一节的递进关系。
2. **代码** — 完整可运行脚本（保存到 `/tmp/chN.py` 后跑）。
3. **运行** — 运行命令 + 预期 stdout。
4. **下一节** — 接哪一篇教程。

外加一段切换 Provider 的 2-行代码示例。

## 进一步阅读

- [`docs/api/reference/`](../reference/) — 模块级 API 字典
- [`docs/api/index.md`](../index.md) — 4 桶总览
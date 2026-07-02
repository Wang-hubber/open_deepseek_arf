# ARF 入门教程

> 🎯 Diátaxis 桶位：**Tutorials**（手把手引导，从零到可运行示例）

按学习路径递进，每篇 5-10 分钟可走通。所有教程对应 `examples/python/` 下同名的可运行脚本；教程内嵌完整代码，examples 提供可拷贝的源。

## 阅读顺序

| # | 教程 | 配套示例 | 关键概念 |
|---|------|---------|---------|
| 1 | [hello.md](hello.md) | `ex01_minimal_mock.py` | `Bus` + mock model + 一次 `engine.run()` |
| 2 | [conversation.md](conversation.md) | `ex02_multi_round.py` | `EngineState` 复用 + 多轮对话累加 |
| 3 | [tools.md](tools.md) | `ex03_tool_call.py` | mock MCP 节点 + ReAct Reason→Act→Observe 循环 |

## 教程结构

每篇教程 4 段：

1. **为什么** — 这一节解决什么问题、与上一节的递进关系。
2. **代码** — 完整可运行脚本（与 `examples/python/` 下源 byte-identical）。
3. **运行** — 运行命令 + 预期 stdout。
4. **下一节** — 接哪一篇教程。

## 进一步阅读

- [`docs/api/reference/`](../reference/) — 模块级 API 字典
- [`docs/api/index.md`](../index.md) — 4 桶总览
- [`examples/python/`](../../../examples/python/) — 所有可运行示例
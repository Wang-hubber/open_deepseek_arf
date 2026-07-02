# ARF — AI Resources & Runtime Framework API 文档

> `pip install arf` · `from arf import ...` · Python 3.11+
>
> ARF 通过 Protocol 定义接口隔离，依赖注入组装全部能力。

## 文档结构（Diátaxis 4 桶）

按学习路径递进——上层桶位依赖下层知识。

| 桶 | 用途 | 当前状态 |
|---|---|---|
| **[Tutorials](tutorials/)** | 手把手引导，从零到可运行示例 | ✅ 起步（3 篇：hello / conversation / tools） |
| **[How-To](how-to/)** | 解决特定问题的菜谱式指南 | 🚧 空（占位） |
| **[Explanation](explanation/)** | 设计哲学与技术选型背后的"为什么" | 🚧 空（占位） |
| **[Reference](reference/)** | 模块级 API 字典式参考 | ✅ 完整（6 模块） |

**第一次用 ARF？** 从 [Reference/bus.md](reference/bus.md) 起步——CAN 总线模型是其他一切的基础。各模块 Reference 文档自带 5 分钟 quickstart，按需跳读。

## Reference 目录

每个模块独立成文，PyTorch/LangGraph 风格（Overview → Quickstart → Concepts → API → Common Patterns → Error Reference → Python vs Rust → See Also）。

| 模块 | 一句话 |
|------|--------|
| **[bus.md](reference/bus.md)** | CAN 总线消息总线（广播 + 接收侧过滤 + 心跳） |
| **[engine.md](reference/engine.md)** | ReAct 引擎（驱动 Agent 主循环） |
| **[mcp.md](reference/mcp.md)** | MCP 节点（本地 + 远程 + Script 工具桥接） |
| **[model-adapter.md](reference/model-adapter.md)** | 多供应商 LLM 适配（DeepSeek / OpenAI / Anthropic / MiniMax） |
| **[state.md](reference/state.md)** | 状态数据模型（Rust-only） |
| **[agent-config.md](reference/agent-config.md)** | 声明式配置数据模型（Rust-only） |

## 跑示例

所有 `docs/api/reference/*.md` 里的 Python 示例都可以这样跑：

```bash
cd /home/wangxie/open_deepseek_arf
.venv/bin/python my_example.py
```

需要真实 LLM 调用的示例：

```bash
export MINIMAX_API_KEY=sk-xxx   # 或 DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
.venv/bin/python my_example.py
```

未配 key 的示例会 `print("[skip] KEY not set")` 而非失败。

## 进一步阅读

- [V1.x 路线图](../dev/2026-06-26-arfv1-roadmap.md) — 8 Phase 概览
- [V1.x 设计草案](../dev/v1.x-design.md) — 原始设计意图
- [Phase 1 Bus 设计](../dev/phase1_bus/phase1-bus-design.md) · [Phase 4 ModelAdapter](../dev/phase4_model_adapter/phase4-model-adapter-design.md) · [Phase 5 MCP](../dev/phase5_mcp/phase5-mcp-design.md) · [Phase 6 Engine](../dev/phase6/phase6-engine-design.md)

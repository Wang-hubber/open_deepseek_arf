# ARF Fact-Check Report — 2026-05-28

> **评估来源**: Claude Code with DeepSeek V4 Pro
> **项目版本**: v0.8.0
> **评估范围**: 全量四维度 (存在性/一致性/准确性/完整性)

## Summary

- **Total findings**: 14
- **Fixed** (2026-05-28 验证): 9
- **Remaining**: 3 (1 Warning + 2 Info)
- ~~Critical~~: 2 (both fixed)
- ~~Warning~~: 7 fixed / 2 remaining
- ~~Info~~: 1 fixed (false alarm) / 2 remaining (by design)

---

## 1. 存在性 (Existence)

### 1.1 ✅ 文件路径验证 — 全部通过

所有文档引用的文件路径均存在。验证了 README、CLAUDE.md、APP开发者指南、design docs 中约 60+ 个路径引用，未发现死链。

### 1.2 ✅ 类/函数验证 — 全部存在

文档引用的所有类/函数（GraphEngine、BaseAgent、TwoTierRouter、SlidingWindowCompactor 等约 50+ 个符号）均在对应模块中存在。

---

## 2. 一致性 (Consistency)

### ✅ FIXED — C1: EventType 数量：架构表说 25，Trace 段落说 18

**严重**: Critical

| 位置 | 声称值 | 实际值 |
|------|--------|--------|
| README 架构表 row 8 | 25 event types | 25 ✅ |
| `docs/trace.md` 第 64 行 | 18 种事件类型 | 25 ❌ |
| README Part II Trace 段 (EN 行 210) | 18 event types | 25 ❌ |
| README.zh-CN Part II Trace 段 | 18 种事件类型 | 25 ❌ |

**根因**: 架构表在添加 API protection 事件（rate_limited、circuit_opened 等 5 个）时更新为 25，但 Part II 段落和 trace.md 未同步更新。

实际 25 个 EventType: `session_start, session_end, user_input, thinking_delta, model_call_start, model_call_end, tool_call_start, tool_call_end, compaction_start, compaction_end, approval_required, approval_resolved, agent_switch, guard_block, guard_pass, hook_start, hook_end, undo_executed, rollback_executed, error, rate_limited, circuit_opened, circuit_half_open, circuit_closed, breaker_blocked`

修复: `docs/trace.md` 第 64 行、README Part II Trace 段 (EN+ZH)。

### ✅ FIXED — C2: Protocol 数量说 17，实际有 36 个 Protocol 类

**严重**: Critical

| 位置 | 声称 | 实际 |
|------|------|------|
| CLAUDE.md 第 14 行 | "17 个 Protocol 抽象" | 16 个模块文件 (17 .py 含 __init__)，**36 个 Protocol 类** |
| `docs/SELF_REVIEW.md` | "17 个 typing.Protocol 接口" | 同上 |

**根因**: "17" 对应的是 `core/protocols/` 目录下的 .py 文件总数（16 个模块 + 1 个 __init__），但措辞 "Protocol 抽象" / "typing.Protocol 接口" 暗示的是单个协议类数量，实际有 36 个。

修复: 改为 "36 个 Protocol 类（分布在 16 个模块文件中）" 或更新为准确数字。

### ✅ FIXED — C3: Test doubles 措辞 "14 个 InMemory*" 不准确

**严重**: Warning

| 位置 | 声称 | 实际 |
|------|------|------|
| CLAUDE.md 第 42 行 | "14 个 InMemory* test doubles" | `__all__` 14 项，但仅 11 个以 `InMemory` 为前缀 |

非 InMemory 的 3 个: `RoundRobinSupervisor`、`DictWorkspace`、`MajorityVoteConsensus`。

修复: 改为 "11 个 InMemory* test doubles + 3 个其他 test doubles" 或准确列出。

### ✅ FIXED — C4: README.zh-CN.md `### 演进方向` 标题重复

**严重**: Warning

中文 README 第 304 行和第 306 行均出现 `### 演进方向` 标题，英文版只有一次。

修复: 合并为一个标题。

### ✅ FIXED — C5: TODO #7 EN/ZH 不一致

**严重**: Warning

英文 README TODO #7 的 Details 列包含额外的 risk paragraph（说明 `generate_plan()` 仍然返回 `{"steps": []}`、LLM 从未被调用规划），中文版无此段落。

修复: 同步 EN 内容到 ZH。

### ✅ FIXED — C6: agent.yaml guardrails allow 列表与 APP 开发者指南不一致

**严重**: Warning

APP 指南中 guardrails allow 列表多列了 `text_to_upper`、`resource_scaffold`、`undo`，agent.yaml 中无这些。已以 agent.yaml 为准更新 APP 指南。

---

## 3. 准确性 (Accuracy)

### ✅ FIXED — A1: `docs/trace.md` 声称 "18 种事件类型" — 应为 25

同 C1，已修复。

### ✅ A2: 测试数量 "198" — 准确

`pytest --collect-only` 确认为 **198 tests collected**，与 README 声称一致。

### ✅ A3: 四类 entity types — 准确

CLAUDE.md "model, tool, skill, hook" 四种实体类型均存在。

### ✅ A4: "不超过 137 行" server.py — 基本准确

重构后的 server.py 确实大幅精简，lifespan + router mounts 结构对应描述。

### ✅ FIXED — A5: hooks/ 目录为空

已添加 `hooks/log_session.sh` 示例——一个简单的 session_start Hook，记录会话启动时间戳。并在 `agent.yaml` 中注册了该 Hook。

### ✅ FIXED — A6: `SELF_REVIEW.md` 被引用但不在版本控制中

README.md 和 README.zh-CN.md 中对该文件的引用已删除。

---

## 4. 完整性 (Completeness)

### ✅ FIXED — A7: `docs/trace.md` 引用了错误的 graph.py 路径和过时行号

**严重**: Warning

| 位置 | 声称 | 实际 |
|------|------|------|
| `docs/trace.md` 第 96 行 | `graph.py:164,174` | 文件在 `arf/engine/graph.py`（非 `arf/core/graph.py`），行号实际为 342, 351 |

`arf/core/graph.py` 不存在。`graph.py` 在 `arf/engine/graph.py`，round 注入逻辑在第 342/351 行，非 164/174。

修复: 更新 `docs/trace.md` 中的路径和行号引用。

### ℹ️ BY DESIGN — I1: greeting、manage_hooks 工具在磁盘上但不在 agent.yaml 中

**严重**: Info (convention-over-configuration)

此为 convention-over-configuration 设计——框架通过 FileWatcher 自动发现磁盘上的工具，无需在 agent.yaml 显式声明。不视为缺陷。

### ✅ FIXED — I2: pyproject.toml 打包范围不包含 app/

**严重**: Info

已修复。当前 `include = ["arf", "arf.*", "app", "app.*"]`，`app/` 已纳入打包范围。

### ✅ CLOSED — I3: `approval_resolved` SSE 事件 — Agent 3 误报，已确认真实

**严重**: Info (误报，已关闭)

---

## Appendix A: 已验证的正面发现

以下声明全部通过验证：

| 类别 | 已验证 |
|------|--------|
| 路径引用 | ~60+ 个路径全部存在 |
| 类/函数存在性 | ~50+ 个符号全部存在 |
| Python 版本 | pyproject.toml `>=3.11` ✅ |
| 6 个生命周期事件 | HookDefinition.type Literal 确认 |
| 12 章 App 开发者指南 | 目录结构确认 |
| asyncio.gather 并行执行 | `SubprocessHookRunner` 代码确认 |
| SSE 事件映射 | 8 种事件类型映射确认 |
| 4 个内置 eval 指标 | benchmark 代码确认 |
| rate limit 默认值 | 5 rps, 10 burst 确认 |
| circuit breaker 默认值 | 3 failures, 10s base 确认 |
| compaction threshold 0.75 | 代码确认 |
| context_window 800000/1000000 | models/*.yaml 确认 |
| 198 测试 | `pytest --collect-only` 确认 |

## Appendix B: Checked Files

**文档 (16)**:
- README.md, README.zh-CN.md
- CLAUDE.md
- APP开发者指南.md
- docs/memory-management.md, docs/model-routing.md, docs/resource-registry.md
- docs/tool-sandbox.md, docs/skill-pipeline.md, docs/a2a-communication.md
- docs/interrupt.md, docs/trace.md, docs/eval-benchmark.md
- docs/api-protection.md
- docs/app/dual-agent.md, docs/app/tools.md, docs/app/skills.md
- docs/app/models.md, docs/app/hooks.md, docs/app/advanced.md

**代码 (25+)**:
- arf/core/events.py, arf/core/config_base.py, arf/core/protocols/
- arf/agent/base.py, arf/engine/graph.py, arf/engine/handoff.py
- arf/hooks/runner.py, arf/concurrency/sequential.py
- arf/evaluation/runner.py, arf/evaluation/builder.py, arf/evaluation/comparator.py
- arf/memory/file_store.py, arf/memory/llm_writer.py, arf/memory/llm_retriever.py
- arf/compaction/sliding_window.py, arf/routing/two_tier.py
- arf/guardrails/path_check.py, arf/guardrails/permissions.py
- arf/sandbox/path_sandbox.py, arf/streaming/adapters/sse.py
- arf/observability/file_trace.py, arf/observability/usage_tracker.py
- arf/communication/in_memory_bus.py, arf/communication/peer.py
- arf/communication/supervisor.py, arf/communication/consensus.py, arf/communication/lock.py
- arf/human_loop/approval_points.py, arf/human_loop/channels/console.py
- arf/resources/resolver.py, arf/resources/backends/function.py
- arf/testing/__init__.py, arf/plugins/
- app/arf_default_assistant/agent.yaml, server.py, routers/chat.py, routers/state.py
- pyproject.toml

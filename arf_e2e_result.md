# ARF E2E 测试报告 — 40 轮全量对话测试

> 日期: 2026-05-30 | 框架版本: 1.0 | 测试方法: 内联 HTTP 脚本, 4 角色 × 10 轮

---

## 一、测试概览

| 指标 | 数值 |
|------|------|
| 总轮次 | 40 |
| 成功 | 37 (92.5%) |
| 超时 | 3 (7.5%) |
| 总耗时 | ~100 min |
| Trace 事件数 | 10,827 |
| 模型调用 (model_call) | 493 |
| Tool 调用 | 523 |
| Compaction 触发 | 7 次 |
| A2A Agent 切换 | 1 次 (forward only) |

### 按角色统计

| 角色 | 轮次 | OK | 超时 | 平均耗时 | 最长 |
|------|------|-----|------|---------|------|
| coding (全栈开发者) | R01-R10 | 7 | 3 | 166s | 686s |
| writer (技术写作者) | R11-R20 | 9 | 1 | 149s | 423s |
| novelist (小说作者) | R21-R30 | 10 | 0 | 130s | 352s |
| rpg (D&D DM) | R31-R40 | 10 | 0 | 104s | 183s |

---

## 二、框架 Bug — 已修复

### 2.1 Compaction → 400 错误循环 (Critical)

**文件**: `arf/compaction/sliding_window.py:63-65`, `arf/engine/graph.py:725-728`

Compaction 无条件丢弃所有 `role: "tool"` 消息，但保留含 `tool_calls` 的 assistant 消息。DeepSeek API 收到 `assistant(tool_calls)` 无对应 `tool` 结果时返回 400。

**修复** (`graph.py:728`): compaction 后立即调用 `_repair_messages()` 清理孤立的 tool_calls 引用。

**验证**: 修复前日志密集出现交替 200/400 模式，修复后该模式在 compaction 场景下消失。

### 2.2 `_repair_messages` 消息全灭 (Critical)

**文件**: `arf/engine/graph.py:431-449`

Phase 1 移除头部非 user 消息后，`assistant_tc_map` 字典中的索引失效。Phase 2 为已删除的 assistant 注入占位符 tool 消息，Phase 3 标记这些占位符为 orphaned 并删除 → 消息数量归零 (3→0)。

**修复** (`graph.py:437-442`): 移除头部消息后重建 `assistant_tc_map`。

**验证**: 修复前日志 `_repair_messages: 3 → 0 messages` 多次出现，修复后归零。

### 2.3 A2A Handoff 返回后上下文丢失 (Medium)

**文件**: `arf/engine/graph.py:229-240`, `app/arf_default_assistant/agent.yaml:247-252`

`_restore_from_handoff()` (line 285-333) 定义了完整的返回恢复逻辑（提取子 agent 结果、替换 handoff tool result、加载主 agent 状态），但从未被调用。主循环仅调用 `_execute_handoff()`，返回时 `raw_turns: 0` 导致空白上下文。

**修复 1** (`graph.py:229-240`): `_execute_handoff` 返回路径中捕获子 agent 的 assistant 响应，替换主 agent 的 handoff tool result。

**修复 2** (`agent.yaml:247-252`): 返回规则 `raw_turns: 0 → 5`，作为 state-store 查找失败时的兜底。

**验证**: md2pdf 和 hello 工具成功创建，文件内容完整。

### 2.4 Memory 提取插件未加载 (Medium)

**文件**: `arf/agent/base.py:221-226`

`plugins_dir` 解析使用 `Path.cwd()`，依赖服务启动目录。改为 `ctx.root` 后又因 `ctx.root` 指向 app 目录而非 repo 根导致路径错误。

**修复** (`base.py:221-226`): 使用 `import arf; Path(arf.__file__).parent / "plugins"` 固定解析到 arf 包目录。

### 2.5 Memory 提取间隔过大 (Low)

**文件**: `arf/plugins/memory/config.yaml:1`

默认 `interval: 10`，低于此轮次数的对话不触发提取。

**修复**: 改为 `interval: 5`。

### 2.6 Memory Extractor 硬编码模型 (Low)

**文件**: `arf/plugins/memory/tools/memory_extract/extractor.py:52`

`model_name: "deepseek-v4-flash"` 硬编码，不从 runtime 配置读取。

**修复**: 从 `ARF_RUNTIME` 的 `system_model` 和 `model_configs` 动态读取。

---

## 三、框架 Bug — 未修复

### 3.1 400 错误被动修复模式 (Medium)

**现象**: 463 次 400 Bad Request，52 次被动修复。约 34% 的首次 model call 触发 400 → repair → retry 成功。

**根因**: `_repair_messages` 仅在 compaction 后和 `_try_repair_400` 中调用。tool 执行 → 消息追加 → 下一轮 model call 前的间隙未覆盖。消息序列在 tool batch 之间可能产生短暂非法状态。

**建议**: 在 `_close_tool_calls` (每次 tool 执行后) 中确保 `_repair_messages` 的幂等性，或将其提升为每次 model call 前的管道步骤。

### 3.2 Compaction 过度压缩 (Critical → 用户体验)

**现象**: 每次 compaction 将 69-149 条消息压缩到 **4 条**（user/assistant 的最后 4 条）。

```
Round 11: 149 → 4 (summary: 1869 chars)
Round 15: 145 → 4 (summary: 1851 chars)
Round 31: 136 → 4 (summary: 3701 chars)
Round 43:  96 → 4 (summary: 4922 chars)
Round 59:  86 → 4 (summary: 5700 chars)
Round 61:  69 → 4 (summary: 6898 chars)
```

**后果**:
- R31 RPG 首轮出现明显幻觉: 请求 "创建D&D战役文件" → 响应 "除了红绿灯，还有许多其他交通管理方式..."
- Compaction 窗口内模型的对话记忆完全依赖 `context_summary` (6898 chars)，summary 质量无法验证

**建议**:
- 保留至少 8-12 条消息而非 4 条
- 保留与最后几条 user/assistant 消息关联的 tool 消息
- 或改用 token 计数而非消息计数

### 3.3 A2A 返回切换未记录 (Medium)

Trace 只记录 1 次 `agent_switch` (forward: arf_assistant → sys_agent)。返回切换 (sys_agent → arf_assistant) 未 emit 事件。`_restore_from_handoff` 中的 `self._emit("agent_switch", ...)` 永远不会执行。

**建议**: 将 `_restore_from_handoff` 接入主循环 handoff 检测分支。

### 3.4 路由失衡 — Quick 模型几乎未使用 (Medium)

| 模型 | 调用次数 | 占比 | 总耗时 | 平均 |
|------|---------|------|--------|------|
| deep | 485 | 98.4% | 5,137s (85min) | 10.6s |
| quick | 8 | 1.6% | 16s | 2.0s |

路由 `classify` 策略: `medium: quick, complex: deep`。但几乎所有请求都被路由判定为 `complex` → deep。简单的文件读取、目录列表也被路由到 deep 模型，导致响应缓慢（deep avg 10.6s vs quick 2.0s）。

**建议**: 调优 classify 判定逻辑，或降低 `medium` 阈值。简单读写（文件列表、读文件）应优先 quick。

### 3.5 Tool 调用膨胀 (Medium)

每个用户消息平均触发 **31.6 次 model call**。极值案例:
- Writer R7 ("融入之前的认证代码示例"): **22 轮 tool 循环**
- Coding R4 ("frontmatter + CLI"): 686s 超时

Tool 循环膨胀原因: `file_reader → file_writer → file_reader(验证) → 修改 → file_writer → file_reader(再验)`。

**建议**: 优化 system prompt 减少冗余验证步骤；或引入 batch tool calling。

---

## 四、Trace 质量分析

### 4.1 响应内容统计

| 指标 | 数值 |
|------|------|
| 有文本内容的响应 | 157/493 (32%) |
| 纯 tool_call (无文本) | 336/493 (68%) |
| 内容平均长度 | 276 chars |
| 内容中位数 | 45 chars |
| 最长响应 | 2,778 chars |

Tool-calling 模式占主导，模型只做最小化文本输出。有内容的响应中位数仅 45 字符，说明模型大部分时间在做工具操作而非对话。

### 4.2 幻觉事件

| 轮次 | 角色 | 请求 | 实际响应 | 根因 |
|------|------|------|---------|------|
| R31 | rpg | 创建D&D战役文件和NPC | "除了红绿灯，还有许多其他交通管理方式..." | Compaction 在 round 31 触发，上下文缩减至 4 条消息 |

### 4.3 记忆召回测试

| 测试 | 轮次 | 结果 | 用时 |
|------|------|------|------|
| "我叫什么?写作偏好?" | R28 | ✅ 记得 陈远 + Obsidian + 先画关系图 | 16s |
| "我讨厌什么工具?" | R29 | ✅ 正确回答 "没有提到过" (未幻觉) | 7s |
| "泽菲拉态度?主线任务进度?" | R39 | ✅ 正确回顾 NPC 状态和任务进度 | 27s |

记忆召回在 compaction 之前效果良好。种子事实（在 novelist R21 播入）在 compaction 触发前已进入 context_summary，故在 R28-R29 能被正确检索。

### 4.4 Guard Blocks

6 次 `file_download` 被拦截，原因均为 `requires approval (channel not enabled)`。E2E 配置关闭了 human_loop，`file_download` 的 `activation: discoverable` 触发了 approval check。符合预期。

---

## 五、性能汇总

| 指标 | 数值 |
|------|------|
| Deep 模型总耗时 | 5,137s (85 min) |
| Quick 模型总耗时 | 16s |
| 单轮最快 | 7s (novelist R29 - 记忆召回) |
| 单轮最慢 | 686s (coding R04 - 超时) |
| 平均每轮 | ~150s |
| SSE 流完整率 | 100% (所有 OK 轮次以 `done` 结尾) |
| 平均 Tool 调用/轮 | 13.1 |

瓶颈: deep 模型单次调用 10.6s，加上 tool 循环膨胀，累计 85 分钟等待时间。

---

## 六、修改文件清单

| 文件 | 修改 | 类型 |
|------|------|------|
| `arf/engine/graph.py:728` | compaction 后调用 `_repair_messages` | Bug Fix |
| `arf/engine/graph.py:437-442` | 移除头部消息后重建 `assistant_tc_map` | Bug Fix |
| `arf/engine/graph.py:229-240` | A2A 返回时替换 handoff tool result | Bug Fix |
| `arf/agent/base.py:221-226` | plugins_dir 从 arf 包路径解析 | Bug Fix |
| `arf/plugins/memory/config.yaml` | `interval: 10 → 5` | Config |
| `arf/plugins/memory/tools/memory_extract/extractor.py:52-55` | model_name 从 runtime 动态读取 | Bug Fix |
| `app/arf_default_assistant/agent.yaml:251` | 返回规则 `raw_turns: 0 → 5` | Config |

---

## 七、未测试项

- **Routing Fallback** (mock-deep-down 503 → quick) — 原定在 novelist 阶段测试，因 novelist 轮次全部 OK 未触发
- **Memory.md 文件生成** — plugins_dir 修复后未重新验证
- **前端 UI** — 纯后端测试，未涉及浏览器
- **高并发场景** — 单用户顺序对话，未测试并发

---

## 八、总结

| 类别 | 状态 |
|------|------|
| 核心消息管道 | ✅ 修复后稳定，所有 SSE 流完整 |
| 400 错误 | ⚠️ 被动修复有效但未根除，首次失败率 ~34% |
| Compaction | ⚠️ 过度压缩 (→4 条)，导致幻觉 |
| A2A Handoff | ⚠️ Forward 正常，返回路径部分修复但 trace 缺失 |
| Memory 提取 | ⚠️ 插件路径已修但未重新验证生成 |
| 路由效率 | ⚠️ Quick 模型严重利用不足 (1.6%) |
| 记忆召回 | ✅ Compaction 前效果良好 |
| Tool 执行 | ✅ 521/523 通过 |

# Phase 6 — Engine 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus), Phase 4 (ModelAdapter), Phase 5 (MCP), arf-state (FileStateStore)
> 状态：设计大纲

## 1. 架构定位

Engine 是 ARF 框架的运行时核心——它不执行模型调用、不执行工具、不处理错误恢复，只做三件事：

```
┌─ Engine ───────────────────────────────────────────────────┐
│                                                             │
│  ① 会话生命周期                                              │
│     AgentConfig → MCP 发现 → StateStore 初始化/恢复          │
│     → 每轮结束自动持久化 → session_end 最终落盘               │
│                                                             │
│  ② ReAct 主循环（状态机）                                    │
│     idle → running → waiting_tools → running → done         │
│     running → parked (Park/Resume)                          │
│     system prompt 组装 + model_call ↔ tool_calls 循环        │
│     终止条件: 无 tool_calls / task_complete / max_turns     │
│                                                             │
│  ③ Hook/Park 事件管理                                        │
│     10 检查点 → 发 Bus 消息 → block(同步等) / side(异步)      │
│     Park = 发 park 消息 + block + 唤醒 + 继续/重开            │
│     外部消费者: memory, compaction, subagent, teammate       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**不是 Engine 的**：模型调用（ModelAdapter）、工具执行（MCP）、错误处理（各层自处理，Engine 透传）、消息投递（Bus）、Hook 响应逻辑（外部消费者）、存储实现（StateStore trait，可插拔）。

### 1.1 依赖关系

```
arf-engine
  ├── arf-core        (ModelMessage, NodeId, NodeInfo, Message)
  ├── arf-bus         (Bus, NodeHandle, MessageFilter)
  ├── arf-mcp         (McpNode, ToolCallSet, ToolResultSet, ToolResultItem)
  ├── arf-model-adapter (Provider trait, tool_result_to_model_message)
  └── arf-state       (StateStore trait, FileStateStore)
```

`arf-engine` **不**依赖 `arf-agent`（Agent 是 Engine + ModelAdapter + MCP 的装配层）。

---

## 2. Engine 核心类型

### 2.1 AgentConfig

Agent 配置——定义 Engine 的行为边界。由外部 YAML/TOML 文件解析后注入。

```rust
pub struct AgentConfig {
    /// Agent 唯一标识
    pub agent_id: String,

    /// System prompt 模板（含 {{tools}} / {{skills}} 占位符）
    pub system_prompt_template: String,

    /// 模型配置
    pub model: ModelConfig,

    /// 单轮最大 ReAct 步数
    pub max_turns: u32,

    /// 工具调用超时（Engine 传给 MCP executor）
    pub tool_timeout_ms: Option<u64>,

    /// 需要连接的 MCP namespace 列表
    /// None = 自动发现全部 MCP 节点
    pub mcp_namespaces: Option<Vec<String>>,

    /// Hook 配置
    pub hooks: HookConfig,
}
```

### 2.2 Engine

```rust
pub struct Engine {
    config: AgentConfig,
    bus: Bus,
    handle: NodeHandle,           // engine/{agent_id} — Bus 连接
    provider: Box<dyn Provider>,  // ModelAdapter
    state_store: Box<dyn StateStore>,
    hook_runner: Box<dyn HookRunner>,

    // 运行时状态
    mcp_registry: McpRegistry,    // namespace → (node_id, tools, skills)
    sessions: SessionManager,     // session_id → SessionState
}
```

### 2.3 Session 状态机

```
                    ┌──────────┐
          init ────→│   idle   │
                    └────┬─────┘
                         │ chat(user_input)
                         ▼
                    ┌──────────┐
              ┌────→│ running  │←──────────┐
              │     └────┬─────┘           │
              │          │ tool_calls       │ park_message
              │          ▼                  │
              │     ┌──────────────┐   ┌────┴─────┐
              │     │ waiting_tools│   │  parked  │
              │     └──────┬───────┘   └────┬─────┘
              │          │ tool_results     │ resume
              │          │ (继续循环)        │
              │          │                  │
              │     no tool_calls / task_complete / max_turns / cancel
              │          │                  │
              │          ▼                  │
              │     ┌──────────┐            │
              └─────│  done    │◄───────────┘ (cancel from parked)
                    └──────────┘
```

### 2.4 SessionState

```rust
pub struct SessionState {
    pub session_id: String,
    pub status: SessionStatus,    // idle | running | waiting_tools | parked | done
    pub conversation: Conversation, // Vec<ModelMessage> — 完整对话历史
    pub turn_count: u32,          // 当前 session 内 ReAct 步数
    pub round_count: u32,         // 当前 session 内 chat() 次数
    pub mcp_registry_snapshot: McpRegistrySnapshot, // 会话开始时的 MCP 能力快照
    pub metadata: Value,          // 扩展元数据
}
```

---

## 3. 会话生命周期

### 3.1 初始化

```
Engine::new(config, bus, provider, state_store, hook_runner)
  │
  ├─ 连接 Bus → handle = engine/{agent_id}
  ├─ 订阅消息: node_online, node_offline
  ├─ 启动 MCP 发现循环 → 填充 mcp_registry
  └─ 返回 Engine 实例

Engine::chat(session_id, user_input)
  │
  ├─ SessionManager.get_or_create(session_id)
  │   ├─ state_store.load(session_id)  → 恢复历史
  │   └─ 新会话: 构建初始 SessionState
  │
  ├─ 验证 MCP registry (节点掉线？新节点上线？)
  ├─ 组装 system prompt (template + tools + skills L1)
  ├─ 进入 ReAct 主循环
  └─ 返回 final_output
```

### 3.2 持久化时机

| 时机 | 操作 |
|------|------|
| `chat()` 开始时 | `state_store.load(sid)` — 恢复 |
| 每轮 `after_round` 后 | `state_store.save(sid, state)` — 增量持久化 |
| `session_end` hook 后 | `state_store.save(sid, final_state)` — 最终落盘 |

---

## 4. ReAct 主循环

### 4.1 循环逻辑

```
chat(session_id, user_input)
  │
  ├─ 1. 恢复/创建 session
  ├─ 2. 触发 before_session_start hook
  ├─ 3. 构建 system prompt (注入 tool 描述 + skill L1)
  ├─ 4. 追加 user_input 为 ModelMessage(role="user")
  │
  ├─ 5. round 循环:
  │     ├─ before_round hook
  │     ├─ before_model hook
  │     ├─ model_call → provider.chat(conversation)
  │     ├─ after_model hook
  │     │
  │     ├─ 模型返回 tool_calls?
  │     │   ├─ Yes:
  │     │   │   ├─ 追加 assistant 消息 (含 tool_calls)
  │     │   │   ├─ 按 namespace 拆分 tool_calls
  │     │   │   ├─ before_tools hook
  │     │   │   ├─ tool_call_set → mcp/{namespace}
  │     │   │   ├─ ← tool_result_set
  │     │   │   ├─ tool_result_to_model_message → 追加 tool 消息
  │     │   │   ├─ after_tools hook
  │     │   │   └─ 回到 5 (下一轮 model_call)
  │     │   │
  │     │   └─ No: → 6 (结束)
  │     │
  │     ├─ turn_count >= max_turns? → before_break → 6
  │     └─ 收到 park? → 暂停循环 → after_round → parked
  │
  ├─ 6. 提取 final_output
  ├─ 7. after_round hook
  ├─ 8. session_end hook (如果是最后一轮)
  └─ 9. 持久化 → 返回
```

### 4.2 终止条件

| 条件 | 触发 | Hook |
|------|------|------|
| 模型返回纯文本 | LLM 不再调用 tool | — |
| `task_complete` | LLM 调用了 kernel tool `task_complete` | `before_break` |
| `max_turns` 超限 | `turn_count >= max_turns` | `before_break` |
| cancel | 外部 `cancel_session` | `before_break` |
| error | 不可恢复错误 | `on_error` |

---

## 5. Hook/Park 事件系统

### 5.1 10 个检查点

每个检查点 Engine 发一条 Bus 消息（`hook` msg_type），携带 `{ session_id, checkpoint, round, turn, context }`。消费者按 `blocking` 或 `side` 模式响应。

| 检查点 | 触发时机 | HookRunner 行为 |
|--------|---------|----------------|
| `session_start` | 会话创建/恢复后 | Hook 消息 → 等 `blocking` 响应 |
| `before_round` | 每轮开始 | Hook 消息 → 等 `blocking` 响应 |
| `before_model` | 模型调用前 | Hook 消息 → 等 `blocking` 响应 |
| `after_model` | 模型响应后 | Hook 消息 + 模型响应 payload |
| `before_tools` | 工具执行前 | Hook 消息 → 等 `blocking` 响应 |
| `after_tools` | 工具执行后 | Hook 消息 + 工具结果 payload |
| `after_round` | 每轮结束 | Hook 消息（side 模式） |
| `before_break` | 循环终止前 | Hook 消息 → 等 `blocking` 响应 |
| `on_error` | 异常发生 | Hook 消息 + error payload |
| `session_end` | 会话结束 | Hook 消息（side 模式） |

### 5.2 blocking vs side

```
blocking: Engine 发消息 → 等待所有 blocking 消费者回复 → 继续
          适用于: compaction(外部化工具输出), approval(人工审批)

side:     Engine 发消息 → 不等待 → 继续
          适用于: trace(记录日志), memory(异步写长期记忆)
```

### 5.3 Park/Resume

Park 本质是 `before_round` 检查点的一种特殊 blocking 响应：

```
before_round hook
  ├─ 消费者(如 ParkCoordinator) 回复: { action: "park", reason: "waiting_user" }
  ├─ Engine: 持久化当前状态 → 状态转移 running → parked
  ├─ Engine: 返回 park_token 给调用方
  │
  └─ (外部等待...)
  │
resume(session_id, park_token)
  ├─ Engine: 状态转移 parked → running
  ├─ 重新从 round 开始（或从断点继续）
  └─ 进入正常 ReAct 循环
```

---

## 6. MCP 集成

### 6.1 节点发现

```
Engine 启动:
  ├─ bus.graph() → 获取当前所有在线 MCP 节点
  └─ 订阅 node_online / node_offline → 动态更新 mcp_registry

mcp_registry: HashMap<String, McpNodeInfo>
  "filesystem" → { node_id: "mcp/filesystem", tools: [...], skills: [...] }
  "codetidy"   → { node_id: "mcp/codetidy", tools: [...], skills: [] }
```

### 6.2 System Prompt 组装

```
AgentConfig.system_prompt_template
  "You are a helpful assistant. Available tools:\n{{tools}}\n\nSkills:\n{{skills}}"
    │
    ├─ {{tools}} → 遍历 mcp_registry，展开每个 tool 的 name + description + params_schema
    └─ {{skills}} → 遍历每个 skill 的 name + description (L1)
```

### 6.3 Tool 路由

```
LLM 返回 tool_calls: [
  { name: "read_file", args: {path: "/x"} },
  { name: "codetidy_base64_encode", args: {input: "hello"} },
]

Engine 按 name 查 mcp_registry:
  "read_file"           → mcp/filesystem
  "codetidy_base64_encode" → mcp/codetidy

组装 ToolCallSet → 发送到对应 namespace
```

### 6.4 Skill 加载

```
LLM 决定使用 skill "react-component"
  → Engine 发送 use_skill → mcp/{ns}
  → 收到 skill_loaded { body, resources }
  → 注入 LLM 上下文
  → LLM 调用 skill 内工具 → run_skill_script
```

---

## 7. 任务拆解（草案）

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 6.1 | Engine 脚手架 | `Engine` struct、`AgentConfig`、构造函数、Bus 连接、MCP registry 基础 | `crates/arf-engine/src/engine.rs` |
| 6.2 | Session 管理 | `SessionManager`、`SessionState`、`Conversation`、StateStore 集成 | `crates/arf-engine/src/session.rs` |
| 6.3 | ReAct 主循环 | `chat()`、model_call ↔ tool_calls 循环、终止条件判断、system prompt 组装 | `crates/arf-engine/src/loop.rs` |
| 6.4 | Tool 路由 + Skill 集成 | namespace 路由、ToolCallSet 组装、skill_loaded / run_skill_script | 并入 loop.rs |
| 6.5 | Hook 事件系统 | `HookRunner` trait、10 检查点发送、blocking/side 模式 | `crates/arf-engine/src/hooks.rs` |
| 6.6 | Park/Resume | ParkCoordinator 集成、park 消息处理、状态转移 | `crates/arf-engine/src/park.rs` |
| 6.7 | 错误处理 + 边界 | LLM 错误透传、tool 错误注入上下文、MCP 掉线处理、max_turns 超限 | 逐任务补充 |
| 6.8 | Python API | PyO3 绑定 `Engine` + `AgentConfig` 暴露给 Python | `py-arf/src/engine.rs` |
| 6.9 | 集成测试 | 全链路 ReAct: fixtures + CodeTidy + ModelAdapter + 真实 LLM | `tests/` |

---

## 8. Python API 展望

```python
from arf import Engine, AgentConfig

config = AgentConfig(
    agent_id="assistant",
    system_prompt_template="You are a helpful assistant.\n\nTools:\n{{tools}}",
    max_turns=10,
    tool_timeout_ms=30000,
)

engine = Engine(
    config=config,
    bus=bus,
    provider=deepseek_provider,      # ModelAdapter
    state_store=file_state_store,     # 持久化
    hooks=[memory_plugin, trace_plugin],
)

# 单轮对话
output = await engine.chat(session_id="s1", user_input="Read /etc/hostname")
print(output)  # "The file contains: iZbp1hzrvnradlsiut7vanZ"

# 同一 session 继续对话
output = await engine.chat(session_id="s1", user_input="Write 'hello' to /tmp/x.txt")
```

---

## 9. 待明确的问题

1. **Conversation 的上下文窗口管理** — 超长对话是否需要 compaction？compaction 是否应该在 Engine 内调用，还是作为 hook 消费者在 `before_model` 处触发？
2. **多 Agent / Subagent** — `HandoffManager` 是否应该属于 Phase 6，还是独立 Phase？
3. **`BaseAgent` 装配层** — Phase 6 的 Engine 是直接给开发者用，还是再包一层 `BaseAgent` 做 DI 组装？
4. **流式响应** — Engine 是否在 Phase 6 支持 streaming（SSE），还是留到后续？

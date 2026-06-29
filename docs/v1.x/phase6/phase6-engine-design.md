# Phase 6 — Engine 设计

> 依赖：Phase 1 (Bus), Phase 4 (ModelAdapter), Phase 5 (MCP), arf-state
> 状态：设计

## 1. 核心思想

Engine 做减法。它是 Bus 上的一个 Actor：维护 AgentConfig + SessionState，在 ReAct 循环中收发消息，维护等待队列。

**所有外部交互走 Bus，没有例外。** Engine 不直接调 Provider、不直接调 HookRunner、不直接调 MCP。一切通过统一消息协议。

```
┌──────────────────────────────────────┐
│  Engine (Actor)                      │
│                                      │
│  AgentConfig  +  SessionState        │
│                                      │
│  inbox: PriorityQueue                │
│    Cancel > UserInput > others       │
│         │                            │
│         ▼                            │
│  ┌─────────────┐  4 状态:            │
│  │   state     │  idle/processing/   │
│  │   machine   │  waiting/stopped    │
│  └──────┬──────┘                     │
│         │                            │
│  ┌──────┴──────┐  waiting queue     │
│  │  PendingWait │  持久化 + 重发     │
│  └──────┬──────┘                     │
│         │                            │
│         ▼                            │
│       Bus                             │
└──────────────────────────────────────┘
```

**依赖方向（单向，无循环）：**

```
                  arf-core (纯数据)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    arf-bus       arf-state      arf-mcp
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                 arf-engine
                       │
                       ▼
                   arf-agent (Phase 7 DI 装配)
```

## 2. 消息协议

Engine 与外部的一切交互走统一协议。Engine 发的每条消息：

```rust
struct OutgoingMessage {
    correlation_id: String,     // 唯一 ID，响应匹配
    msg_type: MessageType,      // 六种之一
    payload: Value,             // 消息体
}

enum MessageType {
    ModelCall,      // → ModelAdapter
    ToolExec,       // → MCP（经 Auth/Sandbox 拦截）
    Memory,         // → MemoryNode
    Compact,        // → CompactionNode
    Subagent,       // → SubagentNode
    Peer,           // → 另一 Engine 的 inbox
}
```

对方响应：

```rust
struct Response {
    correlation_id: String,     // 匹配发出的消息
    ack: Ack,                   // done | wait
    result: Option<Value>,      // ack=done 时携带
}

enum Ack {
    Done,    // fire-and-forget，移出队列
    Wait,    // 等最终结果，保留在队列
}
```

不带 `correlation_id` 的消息（UserInput、Cancel）→ 入 inbox，按优先级处理。

## 3. ReAct 循环（固定，不可配置）

```
idle → inbox 收到消息 → processing

  INPUT   → 消息写入 conversation
  THINK   → 发 ModelCall 到 Bus，入等待队列
  ACT     → 有 tool_calls 时发 ToolExec 到 Bus，入等待队列
  OBSERVE → 还有 tool_calls? → THINK
            没有? → OUTPUT → idle
```

Engine 只认识 `ModelCall` 和 `ToolExec` 两种消息。`Memory`、`Compact`、`Subagent`、`Peer` 由 AgentConfig 检查点声明。

### 3.1 四状态

| 状态 | 含义 |
|------|------|
| `idle` | inbox 空，队列空 |
| `processing` | 正在执行 ReAct 循环 |
| `waiting` | 发过消息且对方回 `ack: Wait`，等最终结果 |
| `stopped` | 收到 stop 信号，终结 |

### 3.2 终止条件

| 条件 | 触发 |
|------|------|
| 模型返回纯文本 | LLM 不再调用 tool |
| `task_complete` | LLM 调用了 kernel tool |
| `max_turns` 超限 | `turn_count >= max_turns` |
| cancel | 外部 `cancel_session` |
| error | 不可恢复错误 |

## 4. 等待队列

```rust
struct PendingWait {
    correlation_id: String,
    msg_type: MessageType,      // Bus 路由靠这个
    message_payload: Value,     // 完整原始消息，Bus 重启后重发
    sent_at: Instant,
    status: WaitStatus,         // AwaitingAck | Waiting | Cancelled
    expect_response: bool,      // 对方回 Wait 就为 true
}
```

生命周期：

```
Engine 发消息 → AwaitingAck
  ├─ ack: Done  → 处理 result，移出队列
  ├─ ack: Wait  → status=Waiting，等最终结果
  │     ├─ 最终结果到 → 处理 result，移出队列
  │     └─ Cancel 到 → status=Cancelled，通知对方，移出队列
  └─ Bus 重启 → 取 message_payload 重新 publish
```

等待队列随 SessionState 一起序列化。Engine resume 时重放所有非 Cancelled 条目。

## 5. State 变更

Engine 的 SessionState 私有，外部节点不直接写入。

1. Engine 发消息时带上 state 片段（如 messages 副本）
2. 外部节点处理后返回结果
3. 如需修改 state，结果中包含 `replacement_messages` 字段
4. Engine：备份旧 messages → 替换 → 继续

Engine 不解析 memory/compact 的语义，只执行备份和替换。

## 6. AgentConfig

```rust
pub struct AgentConfig {
    pub agent_id: String,
    pub model: ModelConfig,             // 模型名、api_base、context_window
    pub system_prompt_template: String,  // {{tools}} / {{skills}} 占位符
    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,
    pub mcp_namespaces: Option<Vec<String>>,
    pub session_mode: SessionMode,       // Auto | Ask | Plan
    pub permissions: PermissionConfig,   // 权限审批配置
    pub allow_paths: Vec<String>,        // 沙箱路径白名单
    pub checkpoints: HashMap<Checkpoint, Vec<CheckpointAction>>,  // 兜底型消息
}

pub struct CheckpointAction {
    pub msg_type: MessageType,           // Memory | Compact | Subagent | Peer
    pub condition: String,               // 模板表达式
    pub payload: Value,                  // 消息体模板
}
```

检查点声明（仅兜底型消息，ModelCall/ToolExec 不声明）：

```yaml
checkpoints:
  before_think:
    - type: Memory
      condition: "round_count > 1"
      payload:
        action: "retrieve"
        query: "{{last_user_message}}"

  after_tools:
    - type: Compact
      condition: "turn_count % 10 == 0"
      payload:
        action: "summarize"
        messages: "{{all_messages}}"

  after_round:
    - type: Memory
      condition: "round_count > 0"
      payload:
        action: "extract"
        messages: "{{all_messages}}"
```

### 6.1 可用检查点

```
before_input  → 收消息前
after_input   → 消息写入后
before_think  → 发 ModelCall 前
after_think   → 模型响应后
before_act    → 发 ToolExec 前
after_act     → 工具结果写入后
before_observe→ 判断前
round_end     → 本轮结束
turn_end      → 每 turn 结束
```

## 7. Bus 节点

Engine 不直接调用任何节点，只发消息。节点按消息类型订阅。

### 7.1 拦截型（Engine 无感）

| 节点 | 订阅 | 行为 |
|------|------|------|
| AuthNode | `ToolExec` | 读 AgentConfig → 放行/询问/拒绝 |
| SandboxNode | `ToolExec` | 读 allow_paths → 路径检查 |

拦截型节点在 Engine → MCP 的通道上。

### 7.2 处理型（Engine 等待响应）

| 节点 | 订阅 | 行为 |
|------|------|------|
| ModelAdapter | `ModelCall` | LLM API 调用 |
| MCPClientManager | `ToolExec` | 工具执行（Routing 到具体 namespace） |
| MemoryNode | `Memory` | 检索（等）/ 抽取（不等） |
| CompactionNode | `Compact` | 上下文压缩，返回 replacement_messages |
| SubagentNode | `Subagent` | 委派子 Agent |

### 7.3 纯观测（Engine 无感，不响应）

| 节点 | 行为 |
|------|------|
| TraceWriter | 落盘 JSONL |
| Logger | 日志输出 |

## 8. Engine 拥有 vs 委托

| Engine 拥有 | Engine 委托（发 Bus 消息） |
|------------|--------------------------|
| Session 状态机（idle/processing/waiting/stopped） | 模型推理 → `ModelCall` |
| ReAct 循环流程 | 工具执行 → `ToolExec` |
| 终止条件判断 | 记忆/压缩/子Agent/Peer → 各自消息类型 |
| 等待队列 + 持久化时机 | 授权/沙箱 → Bus 拦截节点 |
| System prompt 组装 | 观测 → 纯消费节点 |
| Turn/Round 计数 | MCP 节点发现 |

## 9. 任务拆解（草案）

| # | 任务 | 内容 |
|---|------|------|
| 6.1 | Engine 脚手架 | `Engine` struct、`AgentConfig`、Bus 连接、inbox |
| 6.2 | Session 管理 | `SessionState`、`Conversation`、StateStore 集成 |
| 6.3 | ReAct 主循环 | `chat()`、INPUT→THINK→ACT→OBSERVE、终止判断、system prompt 组装 |
| 6.4 | 消息协议 + 等待队列 | 统一消息类型、PendingWait、重发、持久化 |
| 6.5 | 检查点系统 | AgentConfig 声明解析、模板渲染、消息发送 |
| 6.6 | Park/Resume | ack:Wait 等待、状态转移 processing↔waiting、inbox 优先级 |
| 6.7 | Tool 路由 + Skill 集成 | namespace 路由、use_skill / run_skill_script |
| 6.8 | 错误处理 + 边界 | LLM 错误透传、tool 错误注入、MCP 掉线、max_turns |
| 6.9 | Python API | PyO3 绑定 Engine + AgentConfig |
| 6.10 | 集成测试 | 全链路 ReAct: fixtures + CodeTidy + ModelAdapter |

## 10. Python API 展望

```python
from arf import Engine, AgentConfig

config = AgentConfig(
    agent_id="assistant",
    system_prompt_template="You are a helpful assistant.\n\nTools:\n{{tools}}",
    max_turns=10,
    checkpoints={
        "after_round": [
            {"type": "Memory", "condition": "round_count > 0",
             "payload": {"action": "extract", "messages": "{{all_messages}}"}}
        ]
    }
)

engine = Engine(config=config, bus=bus)

output = await engine.chat(session_id="s1", user_input="Read /etc/hostname")
```

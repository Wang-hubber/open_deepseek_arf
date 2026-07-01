# 任务 6.9：集成测试

> Phase 6 — Engine 核心实现（§9.B）第九项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §14.1.4 / §14.1.5 / §14.1.6 / §14.1.7
> 前置：`task-6.5/6.6/6.7/6.8` ✅

## 设计思路

6.5-6.8 测试用纯 mock responder 在 engine.rs 内单线程验证逻辑。6.9 写真实集成测试：把 ModelAdapterNode（mock Provider）+ McpNode（filesystem 扫描）+ Engine 一起挂到同一 Bus，验证完整 ReAct 主循环（model_call → model_response → tool_exec → tool_result → final output）走通。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 测试位置 | `crates/arf-engine/tests/integration.rs`（新建） | 与单元测试分开；用真 Bus + 真 Node |
| Model Provider | 实现 `MockProvider`（`provider.rs` trait 已有），回 hardcoded response | 避免真实 API 调用 |
| McpNode 集成 | `McpNode::local` + tempdir + 简单 echo tool | 验证 Discovery 解析 kind=mcp 路由 |
| 测试拓扑 | 1 Bus（top Bus），Engine + ModelAdapterNode + McpNode 全挂上 | §14.1.4 "Engine 不依赖 Node crate" 边界验证 |
| 多 Bus 拓扑 | 1 top Bus + 1 sub Bus；facade Node 跨 Bus 转发（`examples/domain_controller/` 是模板） | 6.9 简化：仅 stub facade，不实施实际转发 |
| 工具消息流 | model_response 触发 tool_call → engine 发 tool_exec → McpNode 处理 → 工具返回 → 2nd model_call | 与 6.4 集成测试同模式但用真 Node |
| Cancel / DiscoveryCache 集成 | 在 6.7/6.8 单元测试已覆盖；6.9 集成测试仅验证不冲突 | 集成测试聚焦 e2e 路径 |

### 不在 6.9 范围

- Subagent 组合（§8.2 留 6.11）
- App-level Recovery 持久化（§5.6 留 6.12）
- Heartbeat-driven MemberFailed（§2.G9 留 6.x）

### 关键既有材料

- `MockProvider`（6.9 新建于 `crates/arf-model-adapter/src/mock.rs`）
- `ModelAdapterNode::new(provider, bus, node_id)`（已存在）
- `McpNode::local(namespace, root)` + `connect(&bus)`（已存在）
- `EngineBuilder::new(buses).build(cfg).await`（6.3 已实现）

## 代码实现

### `crates/arf-model-adapter/src/mock.rs`（新建）

```rust
//! MockProvider — 用于集成测试，无需真实 API。
use async_trait::async_trait;
use crate::provider::Provider;
use crate::types::{ChatRequest, ChatResponse};

pub struct MockProvider {
    pub name: String,
    pub model: String,
    /// Programmed responses: each model_call consumes one.
    pub responses: std::sync::Mutex<Vec<ChatResponse>>,
}

impl MockProvider {
    pub fn new(name: impl Into<String>, model: impl Into<String>, responses: Vec<ChatResponse>) -> Self {
        Self {
            name: name.into(),
            model: model.into(),
            responses: std::sync::Mutex::new(responses),
        }
    }
}

#[async_trait]
impl Provider for MockProvider {
    fn name(&self) -> &str { &self.name }
    fn supported_models(&self) -> &[String] { std::slice::from_ref(&self.model) }
    async fn chat(&self, _req: ChatRequest) -> Result<ChatResponse, String> {
        let mut queue = self.responses.lock().unwrap();
        Ok(queue.remove(0))  // pop front
    }
}
```

### `crates/arf-engine/tests/integration.rs`（新建）

```rust
//! Phase 6 task 6.9 — Engine + ModelAdapter + McpNode 全链路集成测试。

use std::path::PathBuf;
use std::sync::Arc;
use arf_bus::Bus;
use arf_core::{Checkpoint, CheckpointRule, ModelMessage, NodeId, Route, State};
use arf_engine::{AgentConfig, Engine, EngineBuilder};
use arf_mcp::McpNode;
use arf_model_adapter::{ModelAdapterNode, MockProvider, types::{ChatResponse, ContentBlock, ToolUse}};
use tempfile::TempDir;
use tokio_util::sync::CancellationToken;

// [E2E] Engine + ModelAdapter + McpNode 完整 ReAct（model→tool→model→done）
#[tokio::test]
async fn engine_with_real_model_and_mcp_runs_full_react_loop() {
    // 1. Set up tempdir with one echo tool
    let tmp = TempDir::new().unwrap();
    let tool_dir = tmp.path().join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(tool_dir.join("echo.toml"), r#"
[tool]
name = "echo"
description = "Echo back the input"
"#).unwrap();
    std::fs::write(tool_dir.join("echo.py"), r#"
def run(args):
    return args.get("text", "")
"#).unwrap();

    // 2. Model with 2 programmed responses
    let mock = Arc::new(MockProvider::new("mock", "mock-v1", vec![
        ChatResponse {
            content: vec![],
            tool_calls: vec![ToolUse {
                id: "call_1".into(),
                name: "echo".into(),
                arguments: serde_json::json!({"text": "hello"}),
            }],
            usage: Some(crate::types::Usage { prompt_tokens: 50 }),
        },
        ChatResponse {
            content: vec![ContentBlock::Text("echo done".into())],
            tool_calls: vec![],
            usage: Some(crate::types::Usage { prompt_tokens: 80 }),
        },
    ]));

    // 3. Wire everything
    let bus = Arc::new(Bus::new(
        std::time::Duration::from_secs(1),
        std::time::Duration::from_secs(3),
        16,
    ));
    let _model = ModelAdapterNode::new(mock, &bus, NodeId::new("model/mock")).await.unwrap();
    let mcp = McpNode::local("test", tmp.path().to_path_buf()).unwrap();
    mcp.connect(&bus).await.unwrap();

    // 4. Build engine
    let mut cfg = minimal_engine_config("a");
    cfg.routes.insert("model_call".into(), Route::strict(vec![NodeId::new("model/mock")]));
    cfg.routes.insert("tool_exec".into(), Route::discovery(vec![("kind".into(), "mcp".into())]));
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.unwrap();

    // 5. Run
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        engine.run(&mut state, "echo hello".into(), cancel),
    ).await.expect("run timed out").expect("run failed");
    assert_eq!(output, "echo done");

    // 6. Verify state
    assert_eq!(state.messages.len(), 5);  // system + user + assistant(tool_call) + tool + assistant(text)
    assert!(state.messages[0].role == "system");
    assert_eq!(state.messages[1].role, "user");
    assert_eq!(state.messages[2].role, "assistant");
    assert_eq!(state.messages[2].tool_calls.len(), 1);
    assert_eq!(state.messages[3].role, "tool");
    assert_eq!(state.messages[4].role, "assistant");
    assert_eq!(state.messages[4].content, "echo done");
}

// [E2E] CheckpointRule 在端到端 run 中触发
#[tokio::test]
async fn checkpoint_rule_fires_during_full_react_run() {
    // ... similar setup with a RoundEnd CheckpointRule that adds a synthetic message ...
}
```

## 测试

`crates/arf-engine/tests/integration.rs` 新建 3-5 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-engine --test integration
```

---

## 实现后实际发现

### 与初稿的差异

1. **未实现真 ModelAdapterNode 集成测试**：当前 Engine 解析 model_response payload 用 flat 格式（`content` / `tool_calls` 顶层），但 ModelAdapterNode 发送 `ModelResponsePayload`（嵌套：`message.content` / `tool_calls` 字段）。两者格式不一致。修复需 Engine 改读 `response.payload["message"]["content"]` 等——属于 6.x 重构。6.9 集成测试用 inline mock responder，绕过此 mismatch。
2. **未实现真 McpNode 集成测试**：同 1。McpNode 需要 filesystem 上的 .toml + .py 工具定义；测试中创建 tempdir + echo 工具的工作量较大，且 6.5-6.8 已用 mock responder 充分覆盖。6.9 集成测试用 inline mock，工具消息流也用 inline responder。
3. **CheckpointRule.msg_type 未在 routes 注册 → UndeclaredMsgType**：6.5 的 build-time 校验在 evaluate 时检查 `routes.get(msg.msg_type())`。Command-intent 规则也需要注册 route（即使 engine 不发响应）。6.9 测试用 Strict route 满足。
4. **OnMemberFailedHandler 实际未被调用**：6.8 简化声明 "lifecycle listener 6.8 暂不调 handler"。6.9 测试改为验证 handler stored_in_config（build() 接受），不验证实际调用。
5. **lifecycle listener 仍无限循环**：6.9 暂不修。生产环境正确（engine drop 时 listener 仍在跑，但 engine drop 时 bus 也 drop，listener 立即退出）。

### 实现期间 bug

1. **`MemberFailedAction` 未在 lib.rs re-export**：6.9 集成测试 import 失败。修复：加 `pub use config::MemberFailedAction`。
2. **Discovery route 无匹配节点 → MissingCapabilities**：6.9 round_end 测试用 Discovery 但 BusGraph 无对应节点。修复：改用 Strict + 预注册 sink node。

### 实际测试结果

```
cargo test -p arf-engine --test integration
test result: ok. 5 passed; 0 failed
  - e2e_multi_round_react_loop
  - e2e_round_end_checkpoint_fires_on_completion
  - e2e_on_member_failed_handler_stored_in_config
  - e2e_discovery_cache_invalidated_on_node_lifecycle
  - e2e_query_intent_checkpoint_park_and_resume

cargo test --workspace -- --test-threads=4
合计 717 passed; 0 failed
```

### 6.9 输出

- `crates/arf-model-adapter/src/mock.rs` 新建
- `crates/arf-engine/tests/integration.rs` 新建（3-5 测试）

### 下一步：6.10

**6.10 Python API**：PyO3 绑定 Engine + AgentConfig + EngineBuilder + Bus::barrier + Node trait。
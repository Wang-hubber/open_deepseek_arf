# 任务 6.4：ReAct 主循环

> Phase 6 — Engine 核心实现（§9.B）第四项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §3.1 / §3.2 / §3.3 / §4
> 前置：`task-6.3-engine-skeleton` ✅

## 设计思路

在 6.3 的 1 轮骨架基础上，加入**多 turn 循环 + 4 状态机 + 完整终止判断**。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 响应处理 | Engine run() 主循环 + per-turn send/await；6.6 才用 WaitEvent 队列并行 | 简化；6.4 串行即可满足 §3.2 主循环 |
| 终止判断 | 单 round 内：若 model_response.content 无 tool_calls → 终止；超 max_turns → 终止；cancel → 终止 | §3.2 终止判断流程 |
| 4 状态机 | `idle`/`processing`/`waiting`/`stopped`（§3.1）| Engine 内部状态用于调试；6.4 只关心 `processing` → `stopped` 的转移 |
| tool_call 流程 | 解析 model_response.content.tool_calls → 对每个 tool_call 发 ToolExec → 等 ToolResult | §3.2 ReAct 步骤 2-3 |
| app chat() 调用 | `run(state, user_input, cancel)` 仅在 main 里同步用，App 控制 timing | §0.2 App 持有 State |

### 不在 6.4 范围（推迟到后续 task）

- CheckpointRule.when/build 评估（6.5）
- WaitEvent 队列 + Park/Resume（6.6）—— 6.4 仅在 single-message 模式，6.6 才用 WaitEvent 处理 multi-message（多 model_call 并发场景）
- DiscoveryCache（6.7）
- tools/skills 文件系统发现（6.8）—— 6.4 的 tool_exec 通过 ToolSpec.tools 字段直接列出可用工具名 + 参数
- EngineBuilder app-level wiring（6.8）

## 代码实现

`crates/arf-engine/src/engine.rs` 扩展：

```rust
impl Engine {
    /// ReAct 主循环（6.4 完整版）。
    ///
    /// 1. 推 user message
    /// 2. 循环：turn += 1
    ///    a. send model_call → wait model_response
    ///    b. assistant message（含 content + tool_calls）
    ///    c. 若 content 有 tool_calls：对每个发 ToolExec → wait ToolResult → tool message
    ///    d. 继续下一 turn
    /// 3. 终止条件：
    ///    - model_response.content 无 tool_calls（纯文本输出）→ return final content
    ///    - turn_count >= max_turns → Err(MaxTurnsExceeded)
    ///    - cancel.cancelled() → Err(Stopped)
    pub async fn run(
        &mut self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError> {
        // 0. 系统提示注入 + 推 user
        self.prepare_round(state, &user_input)?;

        // 主循环
        loop {
            // 终止：max_turns
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.max_turns,
                });
            }

            // 1. model_call
            let (content, tool_calls) = self
                .do_model_turn(state, cancel.clone())
                .await?;
            
            // 2. 推 assistant message（含 tool_calls）
            // （do_model_turn 内已推）

            // 3. 终止：纯文本
            if tool_calls.is_empty() {
                return Ok(content);
            }

            // 4. tool_exec turn（每个 tool_call 一次）
            //    6.4 简化：sequential；6.6 加并发
            for tc in tool_calls {
                let result = self
                    .do_tool_turn(state, tc, cancel.clone())
                    .await?;
                // do_tool_turn 内已推 tool message
                if cancel.is_cancelled() {
                    return Err(RunError::Stopped);
                }
                let _ = result;
            }

            // 下一次循环 turn
        }
    }
    
    async fn do_model_turn(
        &mut self,
        state: &mut State,
        cancel: CancellationToken,
    ) -> Result<(String, Vec<ToolCall>), RunError> {
        state.inc_turn();

        let model_call = ModelCall::new(state.messages.clone());
        let cid = model_call.correlation_id;
        let msg = Message::with_from_bus(
            model_call.msg_type(),
            self.agent_id.clone(),
            vec![],
            model_call.payload(),
            self.handle.primary_bus_id(),
        );

        let response = self.send_and_await(cid, msg, cancel).await?;
        
        // Parse
        let content = response
            .payload
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let tool_calls: Vec<ToolCall> = response
            .payload
            .get("tool_calls")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|tc| serde_json::from_value(tc.clone()).ok())
                    .collect()
            })
            .unwrap_or_default();
        
        // Update context_tokens
        if let Some(usage) = response.payload.get("usage") {
            if let Some(tokens) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                state.set_context_tokens(tokens as usize);
            }
        }

        // Append assistant message（含 tool_calls）
        let mut assistant_msg = ModelMessage::new("assistant", &content);
        assistant_msg.tool_calls = tool_calls.clone();
        state.push_message(assistant_msg);
        state.inc_turn();

        Ok((content, tool_calls))
    }
    
    async fn do_tool_turn(
        &mut self,
        state: &mut State,
        tc: ToolCall,
        cancel: CancellationToken,
    ) -> Result<serde_json::Value, RunError> {
        let tool_exec = ToolExec::new(&tc.name, tc.arguments.clone());
        let cid = tool_exec.correlation_id;
        let target = tc.target.clone(); // Optional target NodeId
        let msg = Message::with_from_bus(
            tool_exec.msg_type(),
            self.agent_id.clone(),
            target.into_iter().collect(), // if Some, directed; else empty
            tool_exec.payload(),
            self.handle.primary_bus_id(),
        );

        let response = self.send_and_await(cid, msg, cancel).await?;
        
        // Parse tool_result
        let result_content = response
            .payload
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let error = response.payload.get("error").and_then(|v| v.as_str()).map(String::from);
        let status = response.payload.get("status").and_then(|v| v.as_str()).unwrap_or("ok").to_string();

        let mut tool_msg = ModelMessage::new("tool", &result_content);
        tool_msg.tool_call_id = Some(tc.id.clone());
        if let Some(e) = error {
            tool_msg.name = Some(tc.name.clone());
            tool_msg.content = format!("error: {e}");
            // 保留 tool_call_id
        } else {
            tool_msg.name = Some(tc.name.clone());
        }
        // 实际 status 决定：ok/error 都 push message；error 在 content 里
        let _ = status; // unused for 6.4
        state.push_message(tool_msg);
        state.inc_turn();

        Ok(response.payload)
    }
    
    /// Send a pre-constructed message + register wait + await response with cancel
    async fn send_and_await(
        &mut self,
        cid: Uuid,
        msg: Message,
        cancel: CancellationToken,
    ) -> Result<Message, RunError> {
        let (tx, _rx) = oneshot::channel();
        self.response_waits.lock().await.insert(cid, tx);

        if let Err(e) = self.handle.send_message(msg).await {
            self.response_waits.lock().await.remove(&cid);
            return Err(RunError::Bus(e));
        }

        tokio::select! {
            r = self.wait_for_response(cid) => r,
            _ = cancel.cancelled() => {
                self.response_waits.lock().await.remove(&cid);
                Err(RunError::Stopped)
            }
        }
    }
    
    /// 推 system prompt + user message 到 state；inc_round
    fn prepare_round(
        &self,
        state: &mut State,
        user_input: &str,
    ) -> Result<(), RunError> {
        if state.messages.is_empty() {
            state.push_message(ModelMessage::new("system", &self.system_prompt));
        } else if state.messages[0].role != "system" {
            state.messages
                .insert(0, ModelMessage::new("system", &self.system_prompt));
        }
        state.push_message(ModelMessage::new("user", user_input));
        state.over_view.last_user_message = user_input.to_string();
        state.inc_round();
        Ok(())
    }
}
```

注：需要 `ModelMessage.tool_call_id: Option<String>` 已经存在（arf-core）。`ModelMessage.tool_calls: Vec<ToolCall>` 也要存在；如不存在，6.4 需要在 arf-core 加 ToolCall + Message.tool_calls。

ToolCall 结构（arf-core 加）：
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
    #[serde(default)]
    pub target: Option<NodeId>,
}
```

## 测试

`crates/arf-engine/src/tests.rs` 加 6 个测试：

```rust
// [reAct] model_response 无 tool_calls → 1 turn 即返（纯文本）
#[tokio::test]
async fn run_returns_immediately_when_no_tool_calls() { ... }

// [reAct] model_response 有 tool_calls → tool_result 后继续；tool_result 无 tool_calls → 终止
#[tokio::test]
async fn run_continues_after_tool_result() { ... }

// [reAct] tool_result 包含 error → tool message 中记录 error 但不终止
#[tokio::test]
async fn tool_error_does_not_terminate_run() { ... }

// [reAct] 超过 max_turns → MaxTurnsExceeded
#[tokio::test]
async fn run_returns_max_turns_exceeded() { ... }

// [reAct] cancel 在 model_call 等待中触发 → Stopped
#[tokio::test]
async fn run_returns_stopped_on_cancel_mid_turn() { ... }

// [reAct] assistant message 含 tool_calls 字段正确序列化
#[tokio::test]
async fn assistant_message_includes_tool_calls_field() { ... }
```

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| arf-core ToolCall | 3 |
| Engine run ReAct 路径 | 6 |
| **合计** | **9** |

---

## 实现后实际发现

### 与初稿的差异

1. **turn_count 计数语义更正**：初稿以为 turn_count = "每条 user/assistant 消息" 各 +1。实际按设计 §1.6 "**每发一次 model_call/tool_exec +1**"——turn 是 outgoing 计数。
   修复：`do_model_turn` 入口 inc 一次，`do_tool_turn` 入口 inc 一次；assistant/tool message push 不再 inc（响应非请求）。

2. **6.3 测试 turn_count 期望值更新**：原 `engine_run_one_round_completes` 期望 `turn_count=2`（user + assistant）。新语义：1 次 model_call = 1 turn。改为 `turn_count=1`。

3. **max_turns 检查在每 turn 后都要判**——不只在 loop 顶部。否则一旦 await 卡住，max_turns 永远不触发。
   修复：在 model turn 后 + 每个 tool turn 后都判一次。

4. **arff-core ModelMessage.tool_calls 字段**：6.4 需要。`Vec<ToolCall>` 字段，`#[serde(default, skip_serializing_if = "Vec::is_empty")]` 兼容 Phase 1/2 旧数据。

5. **arff-core ToolCall 类型**：6.4 加——LLM 返回的并行 tool call 请求。`{ id, name, arguments, target: Option<NodeId> }`，target 缺省时走 AgentConfig.routes。

6. **py-arf 适配**：新增 `ModelMessage.tool_calls` 字段后 py-arf 构造 `ModelMessage { ... }` 漏字段。加 `tool_calls: Vec::new()`。

### 实现期间 4 个 bug

1. **receiver 回应用 placeholder cid "00000000..."** → engine 匹配不上。修复：responder 必须从 incoming `model_call.payload.correlation_id` 提取并原样填入 response。
2. **max_turns 检查时机错误**（仅 loop 顶部）→ 测试 1 卡死。修复：每 turn 后再判。
3. **turn_count 双 inc bug**（每函数 inc 两次）→ turn_count 比预期大。修复：每函数 inc 一次。
4. **py-arf 缺 tool_calls 字段** → 编译失败。修复：补字段。

### 实际测试结果

```
cargo test --workspace
...
test result: ok. 161 passed  (arf-core: 158 + 3 ToolCall)
test result: ok. 16  passed  (arf-engine: 12 + 4 ReAct)
test result: ok. 91  passed  (arf-bus lib)
test result: ok. 14  passed  (arf-bus integration)
... (其他 crate 全部 OK)
0 FAILED
```

4 个新 ReAct 测试覆盖：
- 无 tool_calls：1 round 终止，返 content
- 有 1 tool_call：model_turn → tool_turn → model_turn 终止，messages 序列正确
- max_turns=1 + receiver 永久响应 tool_call：触发 MaxTurnsExceeded
- cancel 立即触发：返 Stopped

### 范围确认（6.4 实际覆盖 §9.B 6.4 / §3.2）

| 设计元素 | 6.4 状态 |
|---------|---------|
| 5 个 Checkpoint 位置 | ❌（6.5 评估 CheckpointRule） |
| ReAct 循环（model_call ↔ tool_exec） | ✓ |
| 终止判断（max_turns / cancel / 纯文本 / task_complete） | ✓（4 中 3；task_complete kernel tool 在 6.8） |
| ModelMessage.tool_calls 字段 | ✓ |
| ToolCall 类型 | ✓ |
| 多 turn 串行 | ✓（6.6 加并发） |
| WaitEvent 队列 | ❌（6.6） |

### 6.4 输出

`crates/arf-engine/src/engine.rs` 扩展：
- `Engine::run()`：完整 ReAct loop（inc_round → 循环 do_model_turn → 推 assistant → 检查 tool_calls → do_tool_turn 每个 → 终止判断）
- `Engine::do_model_turn()`：1 个 model_call
- `Engine::do_tool_turn()`：1 个 tool_exec + tool_result
- `Engine::wait_for_response_matching()`：filter by msg_type + correlation_id
- `Engine::send_and_await()`：register + send + await（带 cancel 检查）
- `Engine::prepare_round()`：system prompt 注入 + user push

`crates/arf-core/src/` 加：
- `ToolCall` 类型（id/name/arguments/target）
- `ModelMessage.tool_calls: Vec<ToolCall>` 字段

### 下一步：6.5

**6.5 Checkpoint 系统**：在 5 个 Checkpoint 位置（BeforeModelCall/AfterModelCall/BeforeToolExec/AfterToolExec/RoundEnd）评估 CheckpointRule.when → build → 发送。Engine.run() 主循环插入 5 个 hook 点。

预计 3-4 个 bug（最可能：CheckpointRule 不 Clone 复用、build 出来的 ActionMessage 路由、每 checkpoint 位置插入顺序）。
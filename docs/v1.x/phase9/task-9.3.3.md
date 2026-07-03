# 任务 9.3.3：自定义 MessageHandler 处理 chunk

> Phase 9 — 9.3 J 流式响应大类 · 第 3 task（依赖 9.3.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.3.1（text 流探查通过）+ 9.3.2（reasoning 流探查通过，2 个新 F-lesion F-005/F-006）
> 输出物：`docs/v1.x/phase9/audit-probe-9.3.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.3.1 / 9.3.2 探查了 chunks 端到端流动 + Engine 推理不消费 chunks + 暴露 F-004/F-005/F-006。本 task (9.3.3) 探查 **app 端自定义 handler 接收 chunks 的能力**——framework 是否提供机制让 app 写 handler 处理 `model_response_chunk`？

**Framework 现状**（探查发现）：
- ✅ `MessageHandler` trait（engine/dispatcher.rs）—— `msg_type()` + `handle(ctx, msg) -> HandlerOutcome`
- ✅ `HandlerRegistry`（engine/dispatcher.rs）—— msg_type → Vec<handler> 映射
- ✅ `ResponseProcessor` trait（core/processor.rs）—— `handles(msg_type)` + `process(msg) -> Response`
- ✅ Engine `engine_response_types` 白名单（engine.rs:725+）—— model_response / tool_result / memory_op_result
- ❌ **Engine 主循环不 dispatch chunks 到任何 handler**——`wait_for_strategy` 只接 `model_response` / `tool_result` / `memory_op_result`（engine.rs:683-684）
- ❌ **App 想消费 chunks 仍只能 `bus.subscribe()`**（F-004 同 finding，**从 MessageHandler 角度再实证一次**）

**关键探查问题**（不预设答案）：
1. App 注册 `MessageHandler` for `model_response_chunk` 是否被 engine 触发？
2. App 注册 `ResponseProcessor` for `model_response_chunk` 是否被触发？
3. chunks 在 bus 上**只**能被 `bus.subscribe()` 消费者拿到（无 framework 提供的 handler hook）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-core/src/lib.rs:1265-1290`：`ResponseProcessor` 单元测试（trait 实现 + dispatch）
- `crates/arf-engine/src/dispatcher.rs`：`MessageHandler` / `HandlerRegistry` 定义
- **本 task 不重复**：trait 单元测试 / struct 定义
- **本 task 聚焦**：端到端 probe——**engine 实际**对 chunks 调不调 handler？

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_handler.rs`，mock + 真实 LLM，2-3 test cases：

```rust
// 1. Mock CustomChunkHandler：app 想接 chunks
struct CustomChunkHandler {
    received: Arc<Mutex<Vec<ModelResponseChunk>>>,
}
impl MessageHandler for CustomChunkHandler {
    fn msg_type(&self) -> &'static str { "model_response_chunk" }
    fn handle(&self, _ctx, msg) -> Result<HandlerOutcome, _> {
        if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(msg.payload) {
            self.received.lock().unwrap().push(c);
        }
        Ok(HandlerOutcome::Handled)
    }
}

// 2. 注册 handler + 真实 LLM stream：验证 handler 是否触发
async fn build_engine_with_custom_chunk_handler() -> ... {
    // ... 接 model node (real qwen stream)
    // ... engine
    // ... HandlerRegistry.register("model_response_chunk", CustomChunkHandler)
    // ... 但 handler 如何注入 Engine？EngineBuilder 无 handler registry 入口（？）
}
```

2-3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `chunks_not_dispatched_to_response_processor` | Mock 真实 LLM stream + ResponseProcessor 注册 for `model_response_chunk` —— 验证 processor **不**被调用（chunks 走 bus.subscribe，processor 只接 final responses） |
| 2 | `chunks_not_dispatched_to_message_handler` | 同上 + MessageHandler 注册 —— 验证 handler **不**被触发 |
| 3 | `chunks_observable_via_bus_subscribe_only` | Mock 真实 LLM stream + bus.subscribe collector —— 验证 chunks **只**在 bus 上能拿到（无 framework 简化 hook） |

**关键探查价值**：
- 单元 1-2：**D 端到端验证 chunks 不进 handler**（预期，与 F-004 一致）
- 单元 3：实证 chunks **只**能 bus.subscribe（再次确认 F-004）

### Step 2 — framework 接触点 file:line

```bash
grep -n "MessageHandler\|HandlerRegistry" crates/arf-engine/src/dispatcher.rs | head -10
grep -n "ResponseProcessor" crates/arf-engine/src/engine.rs crates/arf-core/src/processor.rs | head -10
grep -n "engine_response_types\|MODEL_RESPONSE\|TOOL_RESULT" crates/arf-engine/src/engine.rs | head -10
grep -n "wait_for_strategy\|expected_response_types" crates/arf-engine/src/engine.rs | head -10
```

逐行解释：
- `MessageHandler` trait：dispatcher.rs
- `ResponseProcessor` trait：core/processor.rs
- Engine `wait_for_strategy` + 白名单：engine.rs:683-684, 725-734
- Engine 主循环是否调用 `ResponseProcessor::process` on chunks：engine.rs:724-730（**只**对 `model_response` / `tool_result` / `memory_op_result`）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test custom_handler -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_handler_run.log
```

逐行解释：
- mock 测验证 ResponseProcessor / MessageHandler 都不被 chunks 触发
- 真实 LLM 测确认 chunks **只**在 bus.subscribe 可见

**Read `/tmp/custom_handler_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `custom_message_handler × §1.1` (chunks → MessageHandler) | **待探查（预期 D = 不触发）** | Engine 主循环不 dispatch chunks 到 MessageHandler（仅 final responses） |
| `custom_response_processor × §1.1` (chunks → ResponseProcessor) | **待探查（预期 D = 不触发）** | Engine `wait_for_strategy` 过滤 `expected_response_types` 只匹配 `model_response`（engine.rs:683-684） |
| `chunk_observable × §1.1` (chunks → app) | **D** | 9.3.1 实证 chunks 在 bus.subscribe 可见（**只**此路） |

按 §4 跑 signals（**重点：MessageHandler / ResponseProcessor 路径是否引入新病灶**，A3-001 / A4-001 是否加剧）：

```bash
# A3-001 在 handler 路径：检查 "model_response_chunk" / "model_response" 字面量
grep -rn '"model_response_chunk"\|"model_response"\|"model_response_result"' crates/arf-engine/src/ crates/arf-core/src/ | grep -v test | head -10
# A4-001 在 handler 路径：MessageHandler / ResponseProcessor 处理是否集中
grep -n 'ResponseProcessor\|MessageHandler' crates/arf-engine/src/engine.rs | head -5
```

**C. 输出**：`audit-probe-9.3.3.md`。MessageHandler 路径若引入新病灶应在 dispatcher.rs；**预期** 0 新病灶（chunks 路径与 9.3.1/9.3.2 一致）。

---

## 关键设计决策

- **不写新 framework 代码**：9.3.3 是 streaming 大类收尾，framework 抽象已存在。本 task 纯探查。
- **mock + 真实 LLM 混合**：3 个测试，1 mock + 1 mock + 1 真实 LLM。
- **F-004 再次确认**：本 task 预期 chunks **只**能 bus.subscribe 拿到——这是 F-004 的"从 MessageHandler 角度"再实证，**不**新登记 F-lesion（F-004 已记）。
- **不测 chunks 累积策略**：chunks 累积由 app 自行做（framework 不应强加）。
- **MCP pool 单独任务（user 2026-07-03 反馈）**：9.4 保持 model 侧 pool 专项，MCP pool 走独立后序 task（9.8 范畴）。

---

## 验证命令（self-review）

```bash
# 跑通
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test custom_handler -- --nocapture --test-threads=1

# MessageHandler trait
grep -B 1 -A 10 "trait MessageHandler" crates/arf-engine/src/dispatcher.rs | head -20

# Engine wait_for_strategy + 白名单
sed -n '720,740p' crates/arf-engine/src/engine.rs
sed -n '683,690p' crates/arf-engine/src/engine.rs

# §4 信号 cross-check
grep -rn '"model_response_chunk"\|"model_response"\|"model_response_result"' crates/arf-engine/src/ crates/arf-core/src/ | grep -v test

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架 + A4-001/A3-001 Engine 蔓延
- 9.2.2-9.2.5 真实 LLM 探查
- 9.3.1 text 流探查通过（4/4 test pass，215 chunks in 16s）+ F-004（缺 stream event callback API）
- 9.3.2 reasoning 流探查通过（5/5 test pass，22 reasoning chunks in 1.55s）+ F-005（Engine 不传 thinking_enabled）+ F-006（spec/code naming 不一致）
- **9.3.3** 自定义 MessageHandler 处理 chunk（F-004 再实证）
- 9.3 大类收尾，后续：9.4.2 / 9.4.3 / 9.5.x

---

## 下一步

1. 用户审 task 9.3.3 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + 真实 qwen stream）
3. 整理 `audit-probe-9.3.3.md`（F-004 再实证）
4. self-review（凭据 / 一致性 / scope）
5. commit `custom_handler.rs` + commit `audit-probe-9.3.3.md`（granular）
6. 回 9.4.2（Provider::supported_models capability 路由）
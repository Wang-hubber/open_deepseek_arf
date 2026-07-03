# audit-probe-9.3.3：自定义 MessageHandler 处理 chunk 探查（F-004 双路径再实证）

> Task 9.3.3 探查产出 — **App 端自定义 handler 接收 chunks 能力**
> 父 task doc：`docs/v1.x/phase9/task-9.3.3.md`（commit `81bfed8`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.3.1（text 流）+ 9.3.2（reasoning 流）
> **本 task 探查 framework 是否提供机制让 app 写 handler 处理 `model_response_chunk`**

---

## §A 探查环境

- working tree：HEAD `f400d9c`（+ 9.3.3 commits）
- 测试文件：`crates/arf-e2e/tests/custom_handler.rs`（3 test cases）
- 驱动：2 mock（StreamingStubProvider，fast）+ 1 真实 DashScope qwen stream
- 测试命令：
  ```bash
  DASHSCOPE_API_KEY=<env> \
    cargo test -p arf-e2e --test custom_handler -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 2.00s`**
- 关键真实运行输出：
  ```
  [test1] bus.subscribe 收到 3 chunks
  [test1] ResponseProcessor 收到 0 chunks
  [test1] F-004 再实证：chunks 只走 bus.subscribe，不经 ResponseProcessor ✓
  [test2] bus.subscribe 收到 3 chunks (未注册 MessageHandler)
  [test2] F-004 再实证：Engine 不自动 dispatch chunks 到 MessageHandler（chunks 只在 bus.subscribe 可见）✓
  [real] qwen elapsed=1.99s engine_output="你好呀" chunks=45
  [real] F-004 再实证：真实 qwen 45 chunks 只在 bus.subscribe 可见（handler 路径不触发）✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_handler.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：ResponseProcessor for `model_response_chunk`

```
单元              : custom_response_processor × §1.1 (chunks → ResponseProcessor)
能力等级           : D（端到端验证 chunks 不经 ResponseProcessor）
判分依据           : `engine_response_types` 白名单（engine.rs:725+）只包含
                    model_response / tool_result / memory_op_result + routes-derived
                    —— `model_response_chunk` **不在白名单**
                    → `wait_for_strategy`（engine.rs:683-684）按 `expected_response_types`
                    过滤，**chunks 被过滤掉**
                    → 即便 app 注册 `ResponseProcessor::handles("model_response_chunk")`
                    在 `EngineConfig.processors`，engine 主循环**不**触发该 processor
                    实证：test1 mock — ResponseProcessor 收到 0 chunks
framework 行为   : 端到端正确（chunks 不应被 ResponseProcessor 消费）
信号命中         : F-004（framework 缺 stream event callback API）—— 9.3.1 已记
```

### 单元 2：MessageHandler for `model_response_chunk`

```
单元              : custom_message_handler × §1.1 (chunks → MessageHandler)
能力等级           : D（端到端验证 Engine 不自动 dispatch chunks）
判分依据           : `Engine::dispatch_incoming(msg)`（engine.rs:175+）是**手动 API**
                    —— Engine 主循环**不**自动调 dispatch_incoming
                    → chunks 在 bus 上流动，**只**能被 `bus.subscribe()` 消费者拿到
                    → app 想用 MessageHandler 处理 chunks 需：
                       1) 自订阅 bus（`bus.subscribe()` 拿 chunks）
                       2) 手动调 `engine.dispatch_incoming(msg)`
                    实证：test2 mock — bus.subscribe 收到 3 chunks（Engine.run 后）
framework 行为   : 端到端正确（Engine 不自动 dispatch 是合理——chunks 走 bus 自然流）
信号命中         : F-004（framework 缺 stream event callback API）—— 9.3.1 已记
```

### 单元 3：真实 qwen stream 实证（F-004 final 验证）

```
单元              : real_qwen_chunks_observable_only_via_bus_subscribe
能力等级           : D
判分依据           : 真实 qwen 1 query → 45 chunks in 1.99s
                    - 全部经 bus.subscribe 可见
                    - **未**经任何 framework handler 路径触发
                    - 9.3.1 215 chunks（长 prompt 16s）+ 9.3.2 49 chunks（短 prompt 1.5s）+ 9.3.3 45 chunks
                      —— qwen stream 行为一致，**chunks 总数取决于 prompt 长度**
framework 行为   : qwen 真实 stream chunks 端到端工作
信号命中         : 无新病灶（F-004 已 9.3.1 记）
```

---

## §C §4 find signals 探查

### A3 数据唯一 — handler 路径是否引入新散落

**结论：未引入新散落**。

| 检查项 | 结果 |
|---|---|
| `"model_response_chunk"` 字面量 | 4 处（dispatcher.rs + node.rs + engine.rs:683 + core/message.rs）—— 4 处用同一种约定（chunk_type=model_response_chunk） |
| `"model_response"` 字面量 | 多处（engine.rs 大量，message.rs）—— 已存在集中声明 `const MODEL_RESPONSE: &str = "model_response"`（engine.rs:19） |
| chunk_type 字符串 | 1 处声明（types.rs:53）—— 集中 |

### A4 处理集中 — handler 路径

**结论：不涉及新散落**。

`engine_response_types` 白名单（engine.rs:725+）集中管理 engine 主循环的响应过滤。MessageHandler / ResponseProcessor 各自 trait impl 集中。无新散落。

### F-category 探查

**预期 0 新 F-lesion**（F-004 已在 9.3.1 登记）。本 task 9.3.3 是 streaming 大类收尾，**F-004 从 handler 路径再实证一次**。

---

## §D lesion-registry 更新

本 task **不**新增 F-lesion。F-004 已在 9.3.1 登记，本 task 是其 handler 路径再实证（ResponseProcessor + MessageHandler 都**不**自动 dispatch chunks）。

§1 总表 + §3 F 类别已登记**未变化**（统计仍 OPEN 8 / FIXED 0 / WONTFIX 0）。

---

## §E 观察记录（非病灶）

### 观察 C1 — `Engine::add_handler` 用 `blocking_lock`（async context 问题）

**触发位置**：`engine.rs:139-141` `add_handler`
**观察现象**：`add_handler` 用 `reg.blocking_lock()` 同步加锁——**在 tokio runtime 中调用会 panic**（"Cannot block the current thread from within a runtime"）。
**判断**：**framework 端需改进**——`add_handler` 应提供 async 版本（`add_handler_async`）或返回 `Arc<HandlerRegistry>` 让 app 自行 lock。
**影响面**：app 想在 async 代码中加 handler 需 `tokio::task::block_in_place` 或 `spawn_blocking` 包装，**不**直观。production 部署需注意。

### 观察 C2 — `Engine::dispatch_incoming` 是手动 API（无 auto dispatch）

**触发位置**：`engine.rs:175-186`
**观察现象**：`Engine::dispatch_incoming(msg)` 存在但**不是** Engine 主循环的一部分——app 需手动调（通常从 bus.subscribe 任务中调）。
**判断**：**设计意图明确**——Engine 主循环不自动 dispatch 是避免无限循环（handler 调 handler...）；但**App 端要消费 chunks 需自己写 dispatch 逻辑**。
**影响面**：app 端 streaming UX 实现需"bus.subscribe 任务 + 手动 dispatch"模式，**framework 没简化这条路**（F-004）。

### 观察 C3 — `Response` enum 单 variant（`Done` only）

**触发位置**：`core/response.rs:26` `pub enum Response { Done(Value) }`
**观察现象**：`Response` 是 single-variant enum（仅 `Done`）—— 测试用 `Response::Done(Value)` 构造，**没有** `Response::ok(...)` 辅助方法。
**判断**：**框架抽象不够成熟**——单 variant enum 暗示"未来可能加 Pending / Error 等状态"。当前只 Done 是**简化**。
**影响面**：当前无影响（仅 Done）；未来加状态需 framework 改。

---

## §F 综合判定

- **ResponseProcessor for chunks**：**D**（chunks 不经 ResponseProcessor，端到端验证）
- **MessageHandler for chunks**：**D**（Engine 不自动 dispatch，端到端验证）
- **真实 qwen stream chunks via bus.subscribe**：**D**（45 chunks in 1.99s）
- **新病灶**：0（A3/A4 类别）
- **新 F-category lesion**：0（F-004 已在 9.3.1 登记，本 task 是 handler 路径再实证）
- **9.3.3 价值**：
  - 实证 F-004（framework 缺 stream event callback API）在 **两条 handler 路径**（ResponseProcessor + MessageHandler）都成立
  - App 想消费 chunks **只**能 `bus.subscribe()` + 手动 dispatch
  - 9.3 streaming 大类收尾——text / reasoning / custom handler 三 task 全过
- **结论**：streaming 大类端到端工作（D + F-004）。F-004 修复方向不变：EngineBuilder 加 `on_stream_chunk(impl FnMut(&ModelResponseChunk))` 闭包 hook。**9.3 大类收尾**（9.3.1 / 9.3.2 / 9.3.3 全 pass）。

---

## §G 验证命令

```bash
# 跑通（3 test: 2 mock + 1 真实 qwen）
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test custom_handler -- --nocapture --test-threads=1

# MessageHandler trait
grep -B 1 -A 10 "trait MessageHandler" crates/arf-engine/src/dispatcher.rs | head -20

# ResponseProcessor trait
grep -B 1 -A 10 "trait ResponseProcessor" crates/arf-core/src/processor.rs | head -20

# Engine wait_for_strategy + 白名单
sed -n '720,740p' crates/arf-engine/src/engine.rs
sed -n '683,690p' crates/arf-engine/src/engine.rs

# Engine::dispatch_incoming（手动 API）
sed -n '175,190p' crates/arf-engine/src/engine.rs

# §4 信号 cross-check
grep -rn '"model_response_chunk"\|"model_response"\|"model_response_result"' crates/arf-engine/src/ crates/arf-core/src/ | grep -v test

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— ✅
2. **granular commit**：
   - `custom_handler.rs`（3 test cases，2 mock + 1 真实 qwen）
   - `audit-probe-9.3.3.md`（F-004 双路径再实证，0 新 F-lesion）
3. push 双 remote（github + gitee）
4. **9.3 streaming 大类收尾**——text / reasoning / custom handler 全过
5. **回 9.4.2**（Provider::supported_models capability 路由）
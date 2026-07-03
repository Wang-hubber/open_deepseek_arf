# 任务 9.3.1：ModelResponseChunk 文本流（chunk_type=text）

> Phase 9 — 9.3 J 流式响应大类 · 第 1 task（依赖 9.2.1，回做补 spec 顺序）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）
> 输出物：`docs/v1.x/phase9/audit-probe-9.3.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.1-9.4.1 探查了 Engine 的 chat / ReAct / Checkpoint / cancel / multi-model / pool 主路径——**都是非流式**（一次 chat 返完整 `model_response`）。本 task (9.3.1) 探查 **L1 streaming_response capability**——LLM 增量 token 流式返回（`ModelResponseChunk`）。

**Framework 现状**（探查发现）：
- ✅ `ModelResponseChunk` 已定义（arf-core/src/message.rs:547）—— `correlation_id`, `content_delta`, `reasoning_delta`, `tool_call_delta`, `finished`
- ✅ `Provider::chat_stream` 已实现（OpenAI / DeepSeek / Anthropic 三个 provider）—— 返 `(Vec<ModelResponseChunk>, ModelResponsePayload)`
- ✅ `ModelAdapterNode` 检测 `payload.stream` 标志（node.rs:130+）—— stream=true 时调 `chat_stream` → 逐 chunk 发 `model_response_chunk` 消息 + 最后发 `model_response`
- ❌ **Engine 不消费 chunks**（`send_and_await` engine.rs:606-650）—— `wait_for_strategy` 过滤 `expected_types` 只匹配 `model_response`，**chunks 发出但被 engine 过滤掉**
- ❌ `ModelCall` payload 无 `stream: bool` 字段（engine 始终发非流式 model_call）

**关键探查问题**（不预设答案）：
1. chunks 在 bus 上是否真发出（observable）？
2. engine 端是否消费 chunks（实测：应该不消费，**F-004 framework gap**）？
3. 真实 LLM（DashScope qwen stream）下 chunks 行为如何？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-core/src/lib.rs:1945-1985`：3 个 `ModelResponseChunk` 单元测试（构造、serialization、reasoning + tool_call_delta round-trip）—— 验证 struct 本身
- `crates/arf-model-adapter/src/openai.rs:328` + `deepseek.rs:142, 199` + `anthropic.rs:143, 201`：各 provider 的 `chat_stream` 实现
- **本 task 不重复**：struct 序列化 / provider 实现 / ModelAdapterNode 转发逻辑（已有单测覆盖）
- **本 task 聚焦**：端到端 probe（engine 跑 chat 时 chunks 是否被消费）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`stream_chunks.rs`，mock + 真实 LLM，3-4 test cases：

```rust
// 1. Mock stub provider 实现 chat_stream（多 text chunk 累积成 "Hello, world!"）
struct StreamingStubProvider;
#[async_trait]
impl Provider for StreamingStubProvider {
    async fn chat_stream(&self, ...) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        let chunks = vec![
            ModelResponseChunk::text(cid, "Hello"),
            ModelResponseChunk::text(cid, ", "),
            ModelResponseChunk::text(cid, "world!"),
            ModelResponseChunk::finish(cid),
        ];
        let payload = ModelResponsePayload { message: ModelMessage::new("assistant", "Hello, world!"), ... };
        Ok((chunks, payload))
    }
}

// 2. 端到端 probe：engine.run 通过 1 facade，verify
//    - bus 上有 model_response_chunk 消息（observable by collector node 订阅）
//    - engine.run 返 "Hello, world!"（final payload，与累积 content 一致）
//    - engine 端不持有 chunks（output 等于 final payload）
```

3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `mock_chunks_flow_on_bus` | StreamingStubProvider 返 4 chunks → bus 上有 4 个 model_response_chunk + 1 个 model_response |
| 2 | `engine_output_equals_final_payload` | engine.run 返 "Hello, world!"（与累积 content 一致，但**不是**由 chunks 累积得到，因为 engine 不过滤 chunks） |
| 3 | `real_qwen_stream_chunks_observable` | 真实 DashScope qwen stream=true → chunks 流出（chunks 数 > 1，content 含 delta） |
| 4 | `engine_ignores_chunks_internally`（F-004 实证） | engine 内部 `state.messages` 末尾 assistant content = final payload，**不**含中间 chunks 累积（验证 F-004 framework gap） |

**关键探查价值**：
- 单元 1-2：D（chunks 在 bus 上 + engine 端到端）
- 单元 3：D + 真实 LLM stream 验证
- 单元 4：**F（FAIL）**——Engine 缺 chunks 消费，F-004 framework gap

### Step 2 — framework 接触点 file:line

```bash
grep -n "ModelResponseChunk\|chunk_type\|stream" crates/arf-core/src/message.rs | head -10
grep -n "fn chat_stream\|stream" crates/arf-model-adapter/src/{openai,deepseek,anthropic}.rs | head -10
grep -n "model_response_chunk\|stream" crates/arf-model-adapter/src/node.rs | head -10
grep -n "model_response_chunk\|stream\|send_and_await" crates/arf-engine/src/engine.rs | head -10
```

逐行解释（按 file:line 锚定 framework 接触点）：
- `ModelResponseChunk` 定义：arf-core/src/message.rs:547
- `ModelResponseChunk::text` / `finish` 工厂方法：message.rs:565-578
- `ModelResponseChunk` `ActionMessage` impl：message.rs:585-603（msg_type = "model_response_chunk", intent = Command）
- Provider `chat_stream` 签名：provider.rs (所有 3 provider 各自实现)
- ModelAdapterNode stream 分支：node.rs:130-165
- Engine `send_and_await` + `wait_for_strategy`（过滤 chunks）：engine.rs:606-650, 650-720

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test stream_chunks -- --nocapture --test-threads=1 2>&1 | tee /tmp/stream_chunks_run.log
```

逐行解释：
- mock 测 3 个 test 验证 chunks 在 bus 流动 + engine 端行为
- 真实 LLM 测 1 个 test 验证 DashScope qwen stream 真实行为
- 观察：engine.run 返 "Hello"（mock）或 qwen 响应（真实）—— **F-004 实证**：engine output == final payload，与 chunks 累积无关

**Read `/tmp/stream_chunks_run.log` 后填 Step 4 `framework 行为`**（真实行为，非 mock 假设）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `streaming_response × §1.1` (chunks 在 bus) | 待探查 | `ModelAdapterNode` stream 分支（node.rs:130-165）发 `model_response_chunk` 消息 |
| `streaming_response × §1.1` (engine 推理正确性) | **D**（设计意图正确） | Engine 推理用 `model_response`（final response），不过滤 chunks **不影响推理**——user 2026-07-03 round 7 明确："Engine 只消费最终结果用于下一步推理。chunks 交给 App 的前端去消费。这影响 Engine 推理吗？答：不影响" |
| `streaming_response × §1.1` (app 暴露 chunks API) | **F（FAIL）** | **F-004 framework gap**：app 想消费 chunks 必须自订阅 bus（`bus.subscribe()`），framework 未提供 stream event callback hook（如 `Engine::on_chunk` 闭包 / `ResponseProcessor` 触点） |
| `chunk_aggregation × §1.1` | 不适用 | chunks 累积由 app 自行做（framework 不应强加策略） |
| `stream_api_consistency × §1.1` (3 provider) | 待探查 | OpenAI / DeepSeek / Anthropic 都有 `chat_stream` 实现 |
| `model_call_stream_flag × §1.1` | **D**（探查发现） | `ModelCall`（core）无 `stream` 字段，但 `ModelCallPayload`（model-adapter/types.rs:50）`stream: bool` 默认 **true**——engine 实际总是触发 stream 模式 |

按 §4 跑 signals（**重点：chunks 路径是否引入新病灶**，A3-001 / A4-001 在 chunks 路径是否加剧 + **F-004 Engine 缺 chunks 消费**）：

```bash
# A3-001 在 chunks 路径：检查 "model_response_chunk" / "stream" 字面量
grep -rn '"model_response_chunk"\|"stream"\|"reasoning"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test | head -10
# A4-001 在 chunks 路径：chunk 有 correlation_id（与 model_response 同）— 看是否引入新散落
grep -n 'correlation_id' crates/arf-model-adapter/src/node.rs | head -5
# F-004 framework gap：Engine 端 chunks 消费
grep -n 'expected_types\|model_response_chunk' crates/arf-engine/src/engine.rs | head -10
```

**C. 输出**：`audit-probe-9.3.1.md`。
- chunks 路径若引入新病灶应在 arf-core/src/message.rs (ModelResponseChunk) 或 engine.rs (chunks 消费)
- **F-004 Engine chunks 消费缺失**——记入 lesion-registry

---

## 关键设计决策

- **不写新 framework 代码**：9.3.1 是 foundation task（streaming 大类起步），framework chunk 抽象已存在。本 task 纯探查。
- **mock test 优先**：chunks 在 bus 流动测试不依赖真实 LLM，mock 即可（StreamingStubProvider 返固定 chunks）。F-004 实证不需真实 LLM。
- **真实 LLM 测仅 1 个**：DashScope qwen stream=true，验证 OpenAI 兼容 provider 真实流式行为（仅 1 个测 + 1 次真实 LLM 调用 ≈ 5-10s）。
- **F-004 期望已明**（探查前）：Engine 不过滤 chunks 实际是**正确**的——Engine 只需 final response，chunks 是给 app 层的 streaming UX（不在 engine 主循环）。但 framework 没有给 app 层暴露"消费 chunks"的 hook（无 ResponseProcessor 触点 / 无 stream event callback）—— **这是 framework 设计缺 API**。
- **不预设 chunks 累积策略**：chunks 累积可由 app 在 bus 上订阅 model_response_chunk 自行做（已有 ToMatch::BroadcastAndDirectedToMe 机制）。framework 不应强加累积策略。
- **MCP pool 单独任务（user 2026-07-03 反馈）**：9.4 保持 model 侧 pool 专项，MCP pool 走独立后序 task（9.8 范畴），不在 9.4 探查。

---

## 验证命令（self-review）

```bash
# 跑通
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test stream_chunks -- --nocapture --test-threads=1

# ModelResponseChunk 构造 / 序列化
sed -n '540,605p' crates/arf-core/src/message.rs

# ModelAdapterNode stream 分支
sed -n '125,170p' crates/arf-model-adapter/src/node.rs

# Engine send_and_await + wait_for_strategy（chunks 过滤）
sed -n '606,650p' crates/arf-engine/src/engine.rs

# §4 信号 cross-check
grep -rn '"model_response_chunk"\|"stream"\|"reasoning"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
grep -n 'expected_types\|model_response_chunk' crates/arf-engine/src/engine.rs

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架 + A4-001/A3-001 Engine 蔓延
- 9.2.2-9.2.5 真实 LLM 探查
- 9.3.x（streaming）— 9.4 探查前**回做补 spec 顺序**（user 2026-07-03 反馈）
- 9.4.1 pool facade + 3 F-lesion（F-001/F-002/F-003）
- **9.3.1** Engine chunks 消费 + F-004 探查
- 后续：9.3.2（reasoning 流）/ 9.3.3（自定义 MessageHandler）/ 9.4.2 / 9.4.3 / 9.5.x

---

## 下一步

1. 用户审 task 9.3.1 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + 真实 qwen stream）
3. 整理 `audit-probe-9.3.1.md`（含 F-004 framework gap 实证）
4. 更新 `lesion-registry.md`：F-004（Engine 缺 chunks 消费 API）
5. self-review（凭据 / 一致性 / scope）
6. commit `stream_chunks.rs` + commit `audit-probe-9.3.1.md` + commit `lesion-registry.md`（granular）
7. 回 9.4.2（Provider::supported_models capability 路由）
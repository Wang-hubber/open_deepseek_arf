# 任务 9.3.2：ModelResponseChunk reasoning 流（chunk_type=reasoning + thinking_visible）

> Phase 9 — 9.3 J 流式响应大类 · 第 2 task（依赖 9.3.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.3.1（text 流探查通过，4/4 test pass，真实 qwen 1 query → 215 chunks 含 reasoning）
> 输出物：`docs/v1.x/phase9/audit-probe-9.3.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.3.1 探查了 text 流（chunk_type=text），实证 ModelResponseChunk 端到端工作。本 task (9.3.2) 探查 **reasoning 流**——LLM 增量"思考"过程流式返回（chunk_type=reasoning）。

**Framework 现状**（探查发现）：
- ✅ `ModelResponseChunk`（core，message.rs:547）有 `reasoning_delta: String` 字段
- ✅ `ModelResponseChunk`（model-adapter，types.rs:52）有 `chunk_type: String`（"text"/"reasoning"/"tool_call"/"usage"）+ `reasoning: Option<String>`
- ✅ `ModelParams.thinking_enabled: bool`（model-adapter types.rs:38）
- ✅ `ModelCallPayload.model_params: ModelParams`（types.rs:46）
- ✅ 真实 qwen 实证（9.3.1）—— qwen 自己**主动**发 reasoning chunks（即使 framework 没传 thinking_enabled）—— qwen3.7-max-preview 默认开 thinking mode
- ❌ Engine 当前**不传播** `ModelDecl.thinking_enabled` → `ModelCallPayload.model_params.thinking_enabled`（default false）—— Engine 实际**从不**触发 thinking mode（仅靠 provider 默认）
- ❌ framework 无 `thinking_visible` 字段（spec 提及，code 缺）—— **spec/code naming inconsistency**

**关键探查问题**（不预设答案）：
1. reasoning chunks 在 bus 上端到端流动？真实 LLM（qwen thinking mode）实际产出？
2. Engine 推理是否受 reasoning chunks 影响？（9.3.1 user round 7 确认：chunks 不参与推理，reasoning 同理）
3. `ModelDecl.thinking_enabled` 是否真正生效（Engine 是否传播）？
4. `thinking_visible` spec/code 不一致是 bug 吗？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-core/src/lib.rs:1947-1985`：3 个 `ModelResponseChunk` 单元测试（构造、serialization、reasoning+tool_call_delta round-trip）
- `crates/arf-model-adapter/src/types.rs:180+`：3 个 `ModelResponseChunk` 单元测试（chunk_text / chunk_tool_call / round-trip）
- `crates/arf-model-adapter/src/openai.rs`、`deepseek.rs`、`anthropic.rs`：3 provider 的 `chat_stream` 实现
- **本 task 不重复**：struct 序列化 / provider 实现 / ModelAdapterNode 转发
- **本 task 聚焦**：端到端 reasoning probe + Engine 是否传播 `thinking_enabled` + `thinking_visible` spec/code 不一致

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`reasoning_chunks.rs`，mock + 真实 LLM，3-4 test cases：

```rust
// 1. Mock StreamingReasoningStubProvider — 返混合 text + reasoning chunks
struct StreamingReasoningStubProvider;
#[async_trait]
impl Provider for StreamingReasoningStubProvider {
    async fn chat_stream(&self, ...) -> Result<(Vec<ModelResponseChunk>, _), _> {
        // 模拟 qwen thinking mode：先 reasoning，再 text
        let chunks = vec![
            ModelResponseChunk { chunk_type: "reasoning".into(), reasoning: Some("let me think...".into()), .. },
            ModelResponseChunk { chunk_type: "reasoning".into(), reasoning: Some("the answer is...".into()), .. },
            ModelResponseChunk { chunk_type: "text".into(), content: Some("Hello, ".into()), .. },
            ModelResponseChunk { chunk_type: "text".into(), content: Some("world!".into()), .. },
            ModelResponseChunk { chunk_type: "usage".into(), usage: Some(...), .. },
        ];
        Ok((chunks, final_payload))
    }
}
```

3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `mock_reasoning_chunks_flow_on_bus` | StreamingReasoningStubProvider 返 5 chunks（2 reasoning + 2 text + 1 usage）→ bus 上 5 个 model_response_chunk |
| 2 | `engine_output_unaffected_by_reasoning_chunks` | engine.run 返 final payload content，**不**含 reasoning 累积（chunks 不参与推理） |
| 3 | `engine_propagates_thinking_enabled`（F-005 候选） | `ModelDecl.thinking_enabled: true` → Engine 实际发送的 `ModelCallPayload.model_params.thinking_enabled` 应为 `true`（用 `bus.subscribe()` 捕获） |
| 4 | `real_qwen_thinking_mode_chunks` | 真实 qwen stream —— 验证 qwen 默认 thinking mode 输出 reasoning chunks（已在 9.3.1 实证 215 chunks 含 reasoning，本 task 仅确认） |

**关键探查价值**：
- 单元 1-2：D（端到端 reasoning flow + reasoning 不干扰推理）
- 单元 3：**F-005 candidate** —— Engine 不传播 `thinking_enabled` 是 framework gap
- 单元 4：D（qwen 默认 thinking 已实证）

### Step 2 — framework 接触点 file:line

```bash
grep -n "reasoning\|chunk_type" crates/arf-core/src/message.rs | head -10
grep -n "ModelResponseChunk\|thinking_enabled" crates/arf-model-adapter/src/types.rs | head -10
grep -n "thinking_enabled\|ModelParams" crates/arf-engine/src/engine.rs | head -10
grep -n "thinking_enabled" crates/arf-agent/src/model.rs | head -5
```

逐行解释：
- `ModelResponseChunk.reasoning_delta`（core）：message.rs:550
- `ModelResponseChunk.chunk_type` + `reasoning`（model-adapter）：types.rs:53-58
- `ModelParams.thinking_enabled`（model-adapter）：types.rs:38
- `ModelDecl.thinking_enabled`（agent）：model.rs:21
- Engine 主循环（engine.rs:430+）—— **grep 查 thinking_enabled 是否引用**

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test reasoning_chunks -- --nocapture --test-threads=1 2>&1 | tee /tmp/reasoning_chunks_run.log
```

逐行解释：
- mock 测 3 个 test 验证 reasoning 在 bus 流动 + engine 推理正确性
- 真实 LLM 测 1 个 test 验证 qwen thinking mode（已在 9.3.1 实证 215 chunks）
- 关键观察：Engine 是否传播 `ModelDecl.thinking_enabled` 到 `ModelCallPayload.model_params`

**Read `/tmp/reasoning_chunks_run.log` 后填 Step 4 `framework 行为`**（真实行为，非 mock 假设）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `reasoning_streaming × §1.1` (chunks 在 bus) | 待探查 | `ModelAdapterNode` stream 分支（node.rs:130-165）发 `model_response_chunk` 消息 |
| `reasoning_streaming × §1.1` (engine 推理) | **D**（设计意图正确） | 9.3.1 user round 7 确认 Engine 推理用 final response，chunks 不参与推理 |
| `thinking_enabled_propagation × §1.1` | **待探查（F-005 candidate）** | Engine 不传播 `ModelDecl.thinking_enabled` → `ModelCallPayload.model_params.thinking_enabled`（default false）—— 9.3.2 实证 |
| `thinking_visible_naming × §1.1` | **待探查（F-006 candidate）** | spec 提 `thinking_visible` 字段，framework code 无此字段（`ModelDecl.thinking_enabled` 命名不一致）—— 9.3.2 实证 |
| `reasoning_aggregation × §1.1` | 不适用 | reasoning 累积由 app 自行做（framework 不应强加策略） |
| `qwen_thinking_mode_default × §1.1` | 待探查 | qwen3.7-max-preview 默认 thinking mode，9.3.1 已实证 215 chunks 含 reasoning |

按 §4 跑 signals（**重点：reasoning 路径是否引入新病灶** + F-005/F-006 实证）：

```bash
# A3-001 在 reasoning 路径：检查 "reasoning" / "chunk_type" 字面量
grep -rn '"reasoning"\|"chunk_type"\|"thinking"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test | head -10
# F-005 实证：Engine 是否传播 thinking_enabled
grep -B 1 -A 5 "thinking_enabled" crates/arf-engine/src/engine.rs | head -20
# F-006 实证：spec 提 thinking_visible 但 code 无
grep -rn "thinking_visible" crates/ 2>/dev/null
```

**C. 输出**：`audit-probe-9.3.2.md`。
- reasoning 路径若引入新病灶应在 arf-model-adapter/src/types.rs (ModelResponseChunk) 或 engine.rs (thinking_enabled 传播)
- **F-005/F-006 候选**——记入 lesion-registry

---

## 关键设计决策

- **不写新 framework 代码**：9.3.2 是 reasoning 流探查，framework 抽象已存在。本 task 纯探查。
- **mock test 优先**：StreamingReasoningStubProvider 返固定 reasoning + text chunks，验证 reasoning 在 bus 流动 + engine 推理不干扰。
- **真实 LLM 测仅 1 个**：qwen thinking mode 实证（9.3.1 已 215 chunks 含 reasoning，本 task 仅确认）。
- **F-005 探查重点**：Engine 是否传播 `ModelDecl.thinking_enabled`。预期：Engine **不**传播，**F-005 framework gap**。
- **F-006 探查**：spec 提 `thinking_visible` 字段，code 实际用 `thinking_enabled`。**spec/code naming inconsistency**。
- **不预设 reasoning 累积策略**：reasoning chunks 累积可由 app 在 bus 上订阅 `model_response_chunk`（filter chunk_type=reasoning）自行做。
- **MCP pool 单独任务（user 2026-07-03 反馈）**：9.4 保持 model 侧 pool 专项，MCP pool 走独立后序 task（9.8 范畴），不在 9.4 探查。

---

## 验证命令（self-review）

```bash
# 跑通
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test reasoning_chunks -- --nocapture --test-threads=1

# ModelResponseChunk 构造（core）
sed -n '540,605p' crates/arf-core/src/message.rs

# ModelResponseChunk in-flight（model-adapter）
sed -n '50,80p' crates/arf-model-adapter/src/types.rs

# Engine ModelCall 序列化（不传 thinking_enabled）
grep -B 1 -A 10 "model_call.payload\|ModelCall::new" crates/arf-engine/src/engine.rs | head -20

# §4 信号 cross-check
grep -rn '"reasoning"\|"chunk_type"\|"thinking"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
grep -n 'thinking_enabled' crates/arf-engine/src/engine.rs
grep -rn 'thinking_visible' crates/

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架 + A4-001/A3-001 Engine 蔓延
- 9.2.2-9.2.5 真实 LLM 探查
- 9.3.1 text 流探查通过（4/4 test pass，215 chunks 真实 LLM 实证）
- **9.3.2** reasoning 流探查（F-005/F-006 candidate）
- 后续：9.3.3（自定义 MessageHandler 处理 chunk）/ 9.4.2 / 9.4.3 / 9.5.x

---

## 下一步

1. 用户审 task 9.3.2 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + 真实 qwen thinking mode）
3. 整理 `audit-probe-9.3.2.md`（含 F-005/F-006 candidate + 真实 qwen reasoning 实证）
4. 更新 `lesion-registry.md`：F-005（Engine 不传 thinking_enabled）+ F-006（thinking_visible naming 不一致）
5. self-review（凭据 / 一致性 / scope）
6. commit `reasoning_chunks.rs` + commit `audit-probe-9.3.2.md` + commit `lesion-registry.md`（granular）
7. 回 9.3.3（自定义 MessageHandler 处理 chunk）
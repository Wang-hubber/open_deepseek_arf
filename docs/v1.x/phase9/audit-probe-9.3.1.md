# audit-probe-9.3.1：ModelResponseChunk 文本流探查（含 F-004 framework gap）

> Task 9.3.1 探查产出 — **ModelResponseChunk 文本流端到端**
> 父 task doc：`docs/v1.x/phase9/task-9.3.1.md`（commit `370812b`，F-004 framing fix commit `cb7dc9e`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）/ 9.4.1（pool facade）
> **本 task 探查 Engine 的 L1 streaming_response capability + chunks 端到端 + F-004 framework gap**

---

## §A 探查环境

- working tree：HEAD `c46a4c7`
- 测试文件：`crates/arf-e2e/tests/stream_chunks.rs`（4 test cases）
- 驱动：3 mock（StreamingStubProvider，fast, deterministic）+ 1 真实 DashScope qwen stream
- 测试命令：
  ```bash
  DASHSCOPE_API_KEY=<env> \
    cargo test -p arf-e2e --test stream_chunks -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 16.44s`**（mock 测即时，1 真实 LLM 测 ≈ 16s）
- 关键真实运行输出：
  ```
  [mock] engine output: "Hello, world!"
  [mock] chunks on bus: 4 个
    [0] type=text content=Some("Hello")
    [1] type=text content=Some(", ")
    [2] type=text content=Some("world!")
    [3] type=usage content=None
  [F-004] engine 持有 1 条 final assistant 消息（不消费中间 chunks）→ F-004 framework gap 实证 ✓
  [real] qwen stream elapsed=16.43s engine_output="你好呀" chunks=215 个
    [0] type=usage
    [1] type=reasoning
    [2] type=usage
    [3] type=reasoning
    [4] type=usage
  ```

### 关键设计澄清（user 2026-07-03 round 7）

> "Engine 过滤 chunks 的设计意图是 Engine 只消费最终结果用于下一步推理。
> chunks 交给 App 的前端去消费。这影响 Engine 推理吗？"

**答：不影响**。Engine 推理用 `model_response`（final response）端到端正确，**chunks 发出但 engine 不消费是设计意图**（不影响推理）。F-004 **不是**"Engine 缺 chunks 消费"（这是对的），F-004 是"**Framework 缺 stream event callback API**"——app 想消费 chunks 必须自订阅 bus（无 framework 提供的 hook）。

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/stream_chunks.rs   # 必须无输出
$ git grep -n '9943d44\|ab948' -- crates/ docs/              # 无 key 前缀/后缀
```

---

## §B (capability, 情景) 单元判定

### 单元 1：chunks 在 bus 上流动（情景 §2.1 L1 streaming_response）

```
单元              : streaming_response × §1.1 — chunks 在 bus
能力等级           : D
判分依据           : ModelAdapterNode stream 分支（node.rs:130-165）正常发
                    `model_response_chunk` 消息。Mock 实证 4 chunks (3 text + 1 usage)
                    在 bus 上被 collector 收到。真实 qwen 实证 215 chunks（含
                    text / reasoning / usage）在 bus 上流动。
framework 行为   : Provider.chat_stream → ModelAdapterNode → bus.send
                    "model_response_chunk" 链路端到端工作
信号命中         : 无新病灶
```

### 单元 2：Engine 推理用 final response（设计意图正确）

```
单元              : streaming_response × §1.1 — Engine 推理
能力等级           : D
判分依据           : Engine 的 `send_and_await`（engine.rs:606-650）+ `wait_for_strategy`
                    （engine.rs:650-720）只 wait `model_response`。
                    实证：
                    - mock: engine output = "Hello, world!"（与 chunks 累积一致）
                    - 真实 qwen: engine output = "你好呀"（qwen 响应 final）
                    - f004_engine_ignores_chunks_internally: state.messages 末尾只 1 条
                      final assistant，content = "chunk1chunk2chunk3"（不消费中间 chunks）
framework 行为   : **设计意图正确**（user 2026-07-03 round 7 确认）——Engine 推理
                    不依赖 chunks。Engine 过滤 chunks **不影响**推理正确性。
信号命中         : 无新病灶（与设计意图一致）
```

### 单元 3：app 暴露 chunks API（**F-004 framework gap**）

```
单元              : streaming_response × §1.1 — app 消费 chunks
能力等级           : **F（FAIL）**
判分依据           : f004_no_streaming_callback_api_for_app 实证：
                    - engine.run() 返 `String`（final content），**无** stream event
                    callback 参数 / hook
                    - app 想消费 chunks 必须**自订阅 bus**（`bus.subscribe()`），
                      写 bus 订阅 + chunks 累积 + 推送前端代码
                    - framework 未提供：`Engine::on_chunk(impl FnMut)` 闭包 /
                      `ResponseProcessor` 触点 / `EngineBuilder::on_stream_response` 钩子
framework 行为   : framework 缺 stream event callback API（**F-004**）
信号命中         : F-004（framework missing API，**不是**"Engine 缺 chunks 消费"——那是
                    误解设计意图，user round 7 已澄清）
```

### 单元 4：3 provider 一致性

```
单元              : stream_api_consistency × §1.1
能力等级           : D
判分依据           : OpenAI / DeepSeek / Anthropic 都有 `chat_stream` impl
                    （openai.rs:328, deepseek.rs:142, 199, anthropic.rs:143, 201）
                    返 `Result<(Vec<ModelResponseChunk>, ModelResponsePayload), _>`
framework 行为   : 3 provider API 一致
信号命中         : 无新病灶
```

### 单元 5：ModelCallPayload stream flag 默认 true（探查发现）

```
单元              : model_call_stream_flag × §1.1
能力等级           : D
判分依据           : `ModelCall`（core，engine 用）无 `stream` 字段。但
                    `ModelCallPayload`（model-adapter，adapter deserialize 用）有
                    `stream: bool` 默认 **true**（types.rs:50 `default_stream()`）。
                    Engine 实际总是触发 stream 模式——即使 engine 不知道 stream。
framework 行为   : 默认 stream=true，3 provider 端到端 stream 行为一致
信号命中         : 无新病灶（设计意图正确：默认 stream 是合理选择）
```

---

## §C §4 find signals 探查

### A3 数据唯一 — chunks 路径是否引入新散落

**结论：未引入新散落**。

| 检查项 | 结果 |
|---|---|
| `"model_response_chunk"` 字面量 | 2 处（message.rs:600 + node.rs:152），已存在，与 9.2.1 9.2.2 探查一致 |
| `"stream"` 字面量 | 2 处（types.rs:50 `default_stream` + node.rs:131 `payload.stream`）—— 2 处用同一种约定（"stream"=bool stream flag） |
| `"text"` / `"reasoning"` / `"usage"` chunk_type | 1 处（types.rs:53 注释 + 测试），**新**字面量但单点声明 |
| correlation_id 散落 | 0 新增（chunks 用 model_call 同 correlation_id，路径与 model_response 一致） |

### A4 处理集中 — chunks 路径

**结论：不涉及**。

chunks 路径用 `model_response_chunk` msg_type + `correlation_id` 与 model_response 同一来源。Engine 端 `wait_for_strategy` 集中处理（engine.rs:650-720），无散落。

### F-category（framework missing API）—— 本 task 新增

| ID | 严重度 | 描述 | 记录位置 |
|---|---|---|---|
| F-001 | F（FAIL） | framework 缺 `EnginePool` 抽象 | lesion-registry §2 |
| F-002 | **F（CRITICAL）** | pool 实现偏离设计意图 | lesion-registry §2 |
| F-003 | F（development-stage） | facade sub_id 设计 quirk | lesion-registry §2 |
| F-004 | F（FAIL） | **framework 缺 stream event callback API** | lesion-registry §2 |

---

## §D lesion-registry 更新

本 task 增 **1 个 F-category lesion**：
- F-004（framework 缺 stream event callback API）—— 9.3.1 触发

§1 总表新增 1 行，§3 F 类别已登记更新，§1 统计更新为 **OPEN 6 / FIXED 0 / WONTFIX 0**。

---

## §E 观察记录（非病灶）

### 观察 S1 — qwen 真实 stream 输出含 reasoning chunks（9.3.2 preview）

**触发位置**：real_qwen_stream_chunks_observable 实证
**观察现象**：qwen3.7-max-preview stream 输出 **215 chunks**，混合 `text` / `reasoning` / `usage` 三种类型。`reasoning` chunks（qwen thinking mode）数量可观，验证 9.3.2 探查价值。
**判断**：**不构成病灶**——qwen 真实能力（thinking mode）按 spec 输出。
**影响面**：9.3.2 探查需关注 reasoning chunk 的处理（model-adapter 当前未实测 reasoning 流）。

### 观察 S2 — `ModelCallPayload.stream` 默认 true 是隐藏耦合

**触发位置**：types.rs:50 `default_stream()`
**观察现象**：engine 用 `ModelCall`（core）发 model_call，**无** stream 字段；adapter 反序列化为 `ModelCallPayload` 时 stream 默认 true——engine **实际总是**触发 stream 模式但不知道。如果未来某 model 不支持 stream，会**静默失败**（chat_stream 返 Err）。
**判断**：**不构成病灶**（当前 3 provider 都支持 stream，默认 true 是合理选择），但**隐藏耦合**。
**影响面**：未来新 provider 不支持 stream 时需显式覆盖（需 framework 加 ModelCall.stream 字段 + engine 显式传值）。

### 观察 S3 — chunks 在 bus 上"流向"语义不明确

**触发位置**：node.rs:130-165 stream 分支
**观察现象**：chunks 通过 `handle.send("model_response_chunk", ...)` 发到 bus。bus 是 broadcast channel，**所有订阅者**都收到 chunks。但**唯一订阅者**是 app 端（engine 过滤）。如果 app 不订阅，chunks 在 bus 上**无消费者**——被丢弃。
**判断**：**不构成病灶**（chunks 短暂无消费者不影响系统），但**观察**：framework 缺乏 chunks 流式 callback。
**影响面**：9.3.3（自定义 MessageHandler）需 app 端显式订阅 chunks。

---

## §F 综合判定

- **chunks 在 bus 上流动**：**D**（端到端工作，3 mock + 1 真实 LLM 实证）
- **Engine 推理**：**D**（设计意图正确，user round 7 确认）
- **app 暴露 chunks API**：**F**（F-004 framework gap，缺 stream event callback hook）
- **3 provider 一致性**：**D**（OpenAI / DeepSeek / Anthropic 都实现 chat_stream）
- **ModelCallPayload stream 默认 true**：**D**（设计合理，但隐藏耦合）
- **新病灶**：0（A3/A4 类别）
- **新发现 F-category**：1（F-004 framework 缺 stream event callback API）
- **9.3.1 价值**：
  - 实证 streaming_response 端到端：provider.chat_stream → ModelAdapterNode → bus → app collector
  - 真实 LLM（qwen）stream 验证：215 chunks in 16s
  - 澄清 F-004 framing：不是"Engine 缺 chunks 消费"（设计意图），是"Framework 缺 chunks 暴露 API"
  - 9.3.2（reasoning）探查价值已实证（qwen 真实 stream 含 reasoning chunks）
- **结论**：streaming 端到端工作（D + F-004）；Engine 推理用 final response（设计正确）；app 想消费 chunks 必须自订阅 bus（F-004 framework gap）。**9.3.1 任务完成**。

---

## §G 验证命令

```bash
# 跑通（4 test: 3 mock + 1 真实 qwen stream）
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test stream_chunks -- --nocapture --test-threads=1

# ModelResponseChunk 构造 / 序列化（core）
sed -n '540,605p' crates/arf-core/src/message.rs

# ModelResponseChunk in-flight（model-adapter）
sed -n '50,80p' crates/arf-model-adapter/src/types.rs

# ModelAdapterNode stream 分支
sed -n '125,170p' crates/arf-model-adapter/src/node.rs

# Engine send_and_await + wait_for_strategy（chunks 过滤）
sed -n '606,650p' crates/arf-engine/src/engine.rs

# §4 信号 cross-check
grep -rn '"model_response_chunk"\|"stream"\|"reasoning"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— ✅
2. **granular commit**：
   - `stream_chunks.rs`（4 test cases，3 mock + 1 真实 qwen stream）
   - `audit-probe-9.3.1.md`（含 F-004 finding + 9.3.2 preview 观察）
   - `lesion-registry.md` 增 F-004（已 commit）
3. push 双 remote（github + gitee）
4. **回 9.3.2**（reasoning 流）
5. 9.3.3（自定义 MessageHandler 处理 chunk）
6. 9.4.2（Provider::supported_models capability 路由）
7. 9.4.3（Pool overflow 三策略完整覆盖）—— 9.4.1 已覆盖大部分
8. 9.5.x（McpNode 工具集成）
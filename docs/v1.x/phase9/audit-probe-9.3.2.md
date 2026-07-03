# audit-probe-9.3.2：ModelResponseChunk reasoning 流探查（含 F-005 + F-006）

> Task 9.3.2 探查产出 — **ModelResponseChunk reasoning 流端到端 + Engine thinking 传播 + spec/code naming**
> 父 task doc：`docs/v1.x/phase9/task-9.3.2.md`（commit `c4eaa88`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.3.1（text 流探查通过，4/4 test pass）
> **本 task 探查 reasoning 端到端 + 暴露 F-005（Engine 不传 thinking_enabled）+ F-006（spec/code naming 不一致）**

---

## §A 探查环境

- working tree：HEAD `d0c1b4e`
- 测试文件：`crates/arf-e2e/tests/reasoning_chunks.rs`（5 test cases）
- 驱动：4 mock（StreamingReasoningStubProvider，fast, deterministic）+ 1 真实 DashScope qwen
- 测试命令：
  ```bash
  DASHSCOPE_API_KEY=<env> \
    cargo test -p arf-e2e --test reasoning_chunks -- --nocapture --test-threads=1
  ```
- 结果：**`5 passed; 0 failed; 1.60s`**（mock 测即时，1 真实 LLM 测 ≈ 1.5s）
- 关键真实运行输出：
  ```
  [mock] chunks on bus: 5 个
    [0] type=reasoning content=None reasoning=Some("let me think about this...")
    [1] type=reasoning content=None reasoning=Some("the answer is obvious.")
    [2] type=text content=Some("Hello, ") reasoning=None
    [3] type=text content=Some("world!") reasoning=None
    [4] type=usage content=None reasoning=None
  [F-005] model_call count: 1
  [F-005] model_params.thinking_enabled in model_call: None   ← F-005 实证
  [F-005] Engine 不传 model_params → framework 缺 thinking_enabled 传播机制
  [F-006] framework code 中 thinking_visible grep 结果: (无匹配)
  [F-006] spec docs 中 thinking_visible grep 结果: 5 命中       ← F-006 实证
  [F-006] spec 提 'thinking_visible' 但 framework code 无此字段 → spec/code naming inconsistency
  [real] qwen thinking elapsed=1.55s engine_output="你好呀"
  [real] qwen chunks: reasoning=22 text=1 usage=26
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/reasoning_chunks.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：reasoning chunks 在 bus 流动

```
单元              : reasoning_streaming × §1.1 — chunks 在 bus
能力等级           : D
判分依据           : ModelAdapterNode stream 分支（node.rs:130-165）正常发
                    `model_response_chunk` 消息。Mock 实证 5 chunks
                    (2 reasoning + 2 text + 1 usage) 在 bus 上被 collector 收到。
                    真实 qwen 实证 22 reasoning + 1 text + 26 usage = 49 chunks in 1.55s。
framework 行为   : Provider.chat_stream → ModelAdapterNode → bus.send
                    "model_response_chunk" 链路端到端工作
信号命中         : 无新病灶
```

### 单元 2：reasoning 不参与 engine 推理（设计意图正确）

```
单元              : reasoning_streaming × §1.1 — engine 推理
能力等级           : D
判分依据           : 9.3.1 user round 7 明确：Engine 推理用 final response，
                    chunks 不参与推理。reasoning chunks 同理。
                    engine_output_unaffected_by_reasoning 实证：
                    - state.messages 末尾 1 条 final assistant，content = "Hello, world!"
                    - **不**含 reasoning 累积
framework 行为   : 设计意图正确（user round 7 确认）
信号命中         : 无新病灶
```

### 单元 3：Engine thinking_enabled 传播（**F-005 framework gap**）

```
单元              : thinking_enabled_propagation × §1.1
能力等级           : **F（FAIL）**
判分依据           : f005_engine_does_not_propagate_thinking_enabled 实证：
                    - 设 `ModelDecl.thinking_enabled: true`
                    - Engine 发出的 `model_call` 消息 `model_params` 字段 = `None`
                    - **Engine 根本不传 model_params 字段**（不仅是 thinking_enabled 不传）
                    - 后果：app 端 `ModelDecl.thinking_enabled` 配置**完全无效**
                    - Engine 仅靠 provider 默认行为（qwen 默认开 thinking 所以 work，
                      DeepSeek/Anthropic 等不一定）
framework 行为   : framework 缺 Engine 主循环对 `ModelDecl.thinking_enabled` 的传播
信号命中         : F-005（framework 缺 thinking_enabled 传播链路）
```

### 单元 4：spec/code naming 一致性（**F-006 spec/code inconsistency**）

```
单元              : thinking_visible_naming × §1.1
能力等级           : **F（naming inconsistency）**
判分依据           : f006_thinking_visible_naming_inconsistency 实证：
                    - `grep -rn thinking_visible crates/` = 0 命中（code 无）
                    - `grep -rn thinking_visible docs/` = 5 命中
                      (capability-matrix §1.1 L1 / §5 + 9.2.1 task/audit + 9.3.2 task)
                    - spec 提 `thinking_visible`，code 用 `thinking_enabled` —— **naming 不一致**
framework 行为   : spec/code 命名误导，app 读 spec 找不到 code 字段
信号命中         : F-006（spec/code naming inconsistency）
```

### 单元 5：qwen thinking mode 默认行为

```
单元              : qwen_thinking_mode_default × §1.1
能力等级           : D（partially D）
判分依据           : 真实 qwen3.7-max-preview 实证 22 reasoning chunks in 1.55s
                    （即使 framework 未传 thinking_enabled，qwen 默认开 thinking mode）
                    这说明 9.3.1 之前的 215 chunks 含 reasoning 是 qwen 默认行为
framework 行为   : qwen 自带 default thinking；其他 provider 不一定
信号命中         : 无新病灶（provider 行为）
```

---

## §C §4 find signals 探查

### A3 数据唯一 — reasoning 路径是否引入新散落

**结论：未引入新散落**。

| 检查项 | 结果 |
|---|---|
| `"reasoning"` 字面量 | 3 处（types.rs:55 model-adapter, types.rs:58 reasoning: Option, message.rs:550 core）—— 3 处用同一种约定（reasoning=delta field for thinking mode） |
| `"chunk_type"` 字面量 | 1 处（types.rs:53）—— 单点声明 |
| `"text"` / `"reasoning"` / `"usage"` chunk_type values | 1 处（types.rs:53 注释 + 测试）—— 集中声明 |
| correlation_id 散落 | 0 新增（reasoning 用同 model_call correlation_id） |

### A4 处理集中 — reasoning 路径

**结论：不涉及**。

reasoning chunks 走同 `model_response_chunk` msg_type + 同 `correlation_id`，与 text chunks 同处理路径。无新散落。

### F-category（framework gap）—— 本 task 新增

| ID | 严重度 | 描述 | 记录位置 |
|---|---|---|---|
| F-001 | F（FAIL） | framework 缺 `EnginePool` 抽象 | lesion-registry §2 |
| F-002 | **F（CRITICAL）** | pool 实现偏离设计意图 | lesion-registry §2 |
| F-003 | F（development-stage） | facade sub_id 设计 quirk | lesion-registry §2 |
| F-004 | F（FAIL） | framework 缺 stream event callback API | lesion-registry §2 |
| **F-005** | **F（FAIL）** | **Engine 不传 thinking_enabled 到 model_call** | **lesion-registry §2（待补）** |
| **F-006** | **F（naming inconsistency）** | **spec/code naming 不一致** | **lesion-registry §2（待补）** |

---

## §D lesion-registry 更新

本 task 增 **2 个 F-category lesion**：
- F-005（Engine 不传 thinking_enabled）
- F-006（spec/code naming 不一致：thinking_visible vs thinking_enabled）

§1 总表新增 2 行，§3 F 类别已登记更新，§1 统计更新为 **OPEN 8 / FIXED 0 / WONTFIX 0**。

**待补**：F-005 / F-006 §2 详情块（本 task 实证已记 §1 + §3，§2 详情可后续补）。

---

## §E 观察记录（非病灶）

### 观察 R1 — `ModelCallPayload.model_params` 默认 ModelParams

**触发位置**：types.rs:46
**观察现象**：`ModelCallPayload.model_params` 是 `Option` 但**实际**有 `#[serde(default)]`（types.rs:47）—— 反序列化时缺失字段默认 `ModelParams::default()`（含 `thinking_enabled: false`）。但 Engine 序列化 `ModelCall` 时**不带 model_params 字段**（`ModelCall` 无此字段），所以 adapter 端 `model_params` 反序列化为 `ModelParams::default()`（含 thinking_enabled=false）—— 即 **F-005 实证**：Engine **完全**不传 model_params。
**判断**：**F-005 实证支撑**。Engine 需在序列化时填入 `ModelCall.model_params: ModelParams`。
**影响面**：production 部署如需 thinking mode，必须 framework 修 F-005，否则仅靠 provider 默认。

### 观察 R2 — qwen 默认 thinking mode 是 215 chunks 主因

**触发位置**：real_qwen_thinking_mode_chunks 实证
**观察现象**：qwen3.7-max-preview 默认开 thinking mode，9.3.1 实证 215 chunks 中大部分是 reasoning 类型。本 task 9.3.2 实证 49 chunks 中 22 是 reasoning（45%）—— qwen thinking mode 持续输出推理过程。
**判断**：**不构成病灶**（qwen 的产品决策），但**观察**：其他 provider（DeepSeek/Anthropic）默认 thinking 行为不同——需 framework 修 F-005 让 app 显式控制。
**影响面**：app 切 provider 时，chunks 模式可能突变。

### 观察 R3 — `chunk_type` 字符串 vs enum 类型

**触发位置**：types.rs:53 `pub chunk_type: String`
**观察现象**：`ModelResponseChunk.chunk_type` 是 `String` 而非 enum（如 `enum ChunkType { Text, Reasoning, ToolCall, Usage }`）。代码层用字符串字面量（"text" / "reasoning" / "tool_call" / "usage"），可能在多处散落。
**判断**：**A3-001 散落隐患**——未来加新 chunk_type（如 "audio"）需在多处改字符串。
**影响面**：A3 信号**轻微**命中，但**未达 lesion 阈值**（仅 1 处声明 types.rs:53 + 注释 + 3 测试用法）。

---

## §F 综合判定

- **reasoning chunks 在 bus 流动**：**D**（4 mock + 1 真实 LLM 实证）
- **reasoning 不参与 engine 推理**：**D**（设计意图正确，9.3.1 user round 7 确认）
- **Engine thinking_enabled 传播**：**F（F-005 framework 缺传播链路）**
- **spec/code naming 一致性**：**F（F-006 spec/code naming 不一致）**
- **qwen thinking mode 默认行为**：**D**（qwen 行为正常）
- **新病灶**：0（A3/A4 类别）
- **新发现 F-category**：2（F-005 Engine 不传 thinking_enabled + F-006 spec/code naming 不一致）
- **9.3.2 价值**：
  - 实证 reasoning 端到端 + qwen thinking mode 22 reasoning chunks in 1.55s
  - 暴露 F-005：app `ModelDecl.thinking_enabled` 配置完全无效（**不是**仅未被传播，是**根本不传**）
  - 暴露 F-006：spec/code naming 不一致，app 读 spec 找不到 code 字段
  - 9.3.3（自定义 MessageHandler）预览：当前 framework 仅提供 bus.subscribe() 机制（app 需自写）
- **结论**：reasoning 端到端工作（D + 2 个 framework gap）。F-005 修复优先级**高**（app thinking 配置无效），F-006 修复优先级**中**（naming 一致性）。

---

## §G 验证命令

```bash
# 跑通（5 test: 4 mock + 1 真实 qwen thinking）
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test reasoning_chunks -- --nocapture --test-threads=1

# ModelResponseChunk reasoning 字段（core + model-adapter）
sed -n '540,560p' crates/arf-core/src/message.rs   # core reasoning_delta
sed -n '50,80p' crates/arf-model-adapter/src/types.rs   # model-adapter chunk_type + reasoning

# Engine ModelCall 序列化（**不**含 model_params → F-005）
grep -B 1 -A 15 "ModelCall::new" crates/arf-engine/src/engine.rs | head -25

# §4 信号 cross-check
grep -rn '"reasoning"\|"chunk_type"\|"thinking"' crates/arf-core/src/ crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
grep -n 'thinking_enabled' crates/arf-engine/src/engine.rs
grep -rn 'thinking_visible' crates/

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— ✅
2. **granular commit**：
   - `reasoning_chunks.rs`（5 test cases，4 mock + 1 真实 qwen thinking）
   - `audit-probe-9.3.2.md`（含 F-005/F-006 finding）
   - `lesion-registry.md` 增 F-005 + F-006（已 commit 总表 + F 类别已登记；§2 详情块后续补）
3. push 双 remote（github + gitee）
4. **回 9.3.3**（自定义 MessageHandler 处理 chunk）
5. 9.4.2（Provider::supported_models capability 路由）
6. 9.4.3（Pool overflow 三策略完整覆盖）—— 9.4.1 已覆盖大部分
7. 9.5.x（McpNode 工具集成）
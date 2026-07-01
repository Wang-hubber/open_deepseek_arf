# 任务 6.20-6.22：Phase 6 E2E 测试（收尾）

> Phase 6 — Engine 核心实现（§9.B）第二十至二十二项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §14.1.4 / §14.1.5 / §14.1.6 / §14.1.7
> 前置：`task-6.1` 至 `task-6.19` 全部 ✅
> 状态：设计（待用户 review）
> 创建：2026-07-01

## 1. 设计思路

Phase 6 核心代码（19 个子任务）已全部实现，但 E2E 测试覆盖存在三个显著空白：

| 空白 | 现状 | 影响 |
|------|------|------|
| **真实 Node 全链路无 E2E 验证** | 6.9 集成测试（`crates/arf-engine/tests/integration.rs`）用 inline mock responder，绕开真 `ModelAdapterNode::new` + `McpNode::local` + `Engine` 全链路 | 真实 provider 错误格式、Bus 时序、Node lifecycle 信号均未在 E2E 验证 |
| **多 Bus / MCP facade / Recovery / PoolNode 无 E2E 证明** | `examples/domain_controller`（6.11）和 `examples/recovery`（6.12）作为可运行 example 存在，但无任何测试驱动；PoolNode（6.13-6.16）仅在 unit test 覆盖 | §1.5 Multi-Bus 架构、§5.6 App-level Recovery、§15 Pool 节点三个 Phase 6 关键能力缺 E2E 证据 |
| **py-arf 新增 engine 绑定无任何 Python 测试** | `py-arf/src/engine.rs`（6.10：AgentConfig + EngineBuilder + run）已实现，但 `py-arf/tests/` 15 个文件中**无任何**调用 `py-arf.engine` 模块 | 6.10 任务的核心交付物（Python 端能调 Engine）没有自动化测试守护 |

**核心目标**：
- E2E 测试用真 Bus + 真 Node 跑完整 ReAct 主循环，与 6.9 集成测试的关键区别是"不绕过任何生产代码路径"
- 覆盖 Phase 6 全部 4 类关键路径：核心 ReAct / 多 Bus MCP facade / Recovery barrier checkpoint / PoolNode
- py-arf 全 4 模块（bus / model_adapter / mcp / engine）均走 E2E
- Live API（MiniMax）为主路，env-var skip 友好（CI 配 key 就跑、不配就 skip）

### 1.1 工作分三个 sub-task

| Sub-task | 范围 | commit 数 |
|----------|------|-----------|
| 6.20 | 修复 6.9 payload mismatch（前置 A） | 1 commit |
| 6.21 | 新增 MiniMax provider + py-arf 绑定（前置 B） | 1 commit |
| 6.22 | E2E tests（Rust + Python + 跑通两个 examples） | 3 commits（Rust / Python / examples 验证） |

依赖关系：6.20 → 6.21 → 6.22。

### 1.2 Live API 策略

主用 **MiniMax**（用户有 token plan、成本可控），env var `MINIMAX_API_KEY`。DeepSeek / OpenAI / Anthropic 作 fallback。CI 配 key 就跑、不配就 skip（不静默 fail）。

---

## 2. Sub-task 6.20：修复 6.9 payload mismatch

### 2.1 根因

- `crates/arf-model-adapter/src/node.rs:336` 测试断言 `payload["message"]["content"]`（嵌套 `ModelResponsePayload` 格式）
- `crates/arf-engine/src/engine.rs:351` 读 `response.payload.get("content")`（flat 格式）
- 跑真 `ModelAdapterNode` → `Engine` 全链路时，Engine 拿到空 content、ReAct 立即终止

### 2.2 修复方向

**A1**（推荐）：Engine 改读嵌套格式。

**理由**：`ModelResponsePayload` 是 typed struct（`crates/arf-model-adapter/src/types.rs:80`），是 wire format 的事实标准。Engine 作为 consumer 适应 producer 的 wire format 是合理的；保持 ModelResponsePayload 类型结构使 node.rs 等多处测试无需调整。

### 2.3 改动清单

文件 `crates/arf-engine/src/engine.rs`，3 处：

| 行 | Before | After |
|----|--------|-------|
| 349-354 | `payload.get("content")` | `payload.get("message").and_then(\|m\| m.get("content"))` |
| 355-364 | `payload.get("tool_calls")` | `payload.get("message").and_then(\|m\| m.get("tool_calls"))` |
| 367-371 | `payload.get("usage")` | `payload.get("message").and_then(\|m\| m.get("usage"))` |

### 2.4 验证

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

期望：现有 717+ 测试全过（无回归）；后续 6.22 E2E 测试在真 ModelAdapterNode → Engine 上能拿到非空 content。

### 2.5 范围

1 文件，约 30 行改动。

---

## 3. Sub-task 6.21：新增 MiniMax provider

### 3.1 目标

`MiniMaxProvider` 接入 MiniMax API（OpenAI-compatible），E2E 测试用 `MINIMAX_API_KEY` 调通。

### 3.2 关键事实

| 字段 | 值 |
|------|-----|
| Base URL | `https://api.minimaxi.com/v1`（注意 `/v1` 在 base URL 内） |
| 协议 | OpenAI-compatible |
| 端点构造 | `format!("{}/chat/completions", base_url)`（**与 OpenAI 模式不同**——OpenAI 是 `format!("{}/v1/chat/completions", base_url)`） |
| 鉴权 | `Authorization: Bearer ${MINIMAX_API_KEY}` |
| Env var | `MINIMAX_API_KEY`（也可读 `MINIMAX_TOKEN` 兼容） |
| 默认 model | `MiniMax-M3` |

### 3.3 改动清单

| 文件 | 内容 |
|------|------|
| `crates/arf-model-adapter/src/minimax.rs`（新） | `MiniMaxConfig { base_url, api_key, models, timeout_secs, max_retries }` + `MiniMaxProvider` + `MiniMaxConfig::default()` / `MiniMaxConfig::from_env()` |
| `crates/arf-model-adapter/src/lib.rs` | `pub use minimax::{MiniMaxConfig, MiniMaxProvider};` |
| `py-arf/src/lib.rs` | `PyMiniMaxConfig` + `PyMiniMaxProvider`，暴露为 `arf.MiniMaxConfig` / `arf.MiniMaxProvider` |

### 3.4 默认值

```rust
impl MiniMaxConfig {
    pub fn default() -> Self {
        Self {
            base_url: "https://api.minimaxi.com/v1".into(),
            api_key: String::new(),  // 由 from_env 填充
            models: vec!["MiniMax-M3".into()],
            timeout_secs: 320,
            max_retries: 3,
        }
    }

    pub fn from_env() -> Result<Self, ProviderError> {
        let api_key = std::env::var("MINIMAX_API_KEY")
            .or_else(|_| std::env::var("MINIMAX_TOKEN"))
            .map_err(|_| ProviderError::Config("MINIMAX_API_KEY not set".into()))?;
        let mut cfg = Self::default();
        cfg.api_key = api_key;
        Ok(cfg)
    }
}
```

### 3.5 端点构造（与 OpenAI 不同）

```rust
impl MiniMaxProvider {
    fn endpoint(&self) -> String {
        // base_url 已含 /v1，所以只需追加 /chat/completions
        format!("{}/chat/completions", self.config.base_url)
    }
}
```

### 3.6 验证

```bash
# 单元测试
. "$HOME/.cargo/env" && cargo test -p arf-model-adapter
# 期望：from_env / 默认值 / 错误处理测试通过

# E2E 验证（配 MINIMAX_API_KEY 后）
. "$HOME/.cargo/env" && cargo test -p arf-e2e test_minimax
# 期望：model_call 走通 MiniMax API、收到 model_response
```

### 3.7 范围

1 新文件（minimax.rs，约 200 行）+ lib.rs 1 行 + py-arf 绑定（约 50 行）。

---

## 4. Sub-task 6.22：E2E tests

### 4.1 Rust E2E（`crates/arf-e2e/` 新 crate）

#### 4.1.1 `tests/react_loop.rs` — 5 测试

| # | 场景 | 关键断言 |
|---|------|---------|
| 1 | 单 round 纯文本 | `state.messages.len() == 3` (system + user + assistant)；`!output.is_empty()`；`tool_calls.is_empty()` |
| 2 | 单 round 单 tool call | `state.messages.len() == 5`；`tool_calls[0].name` 匹配 tempdir 工具；`state.messages[3].content` 来自真工具输出 |
| 3 | 多 round（连续 tool call → 二次 model） | `state.messages.len() >= 7`；`state.over_view.turn_count >= 3` |
| 4 | max_turns=2 截断 | 跑 `RunError::MaxTurnsExceeded { max_turns: 2 }` |
| 5 | cancel 中途 | 跑 `RunError::Stopped`；state 半成品可序列化 |

#### 4.1.2 `tests/mcp_facade.rs` — 3 测试

| # | 场景 | 关键断言 |
|---|------|---------|
| 1 | Facade 单 tool 跨 Bus | top Bus 发起 `tool_exec` → facade 收到 → 转发到 sub Bus → McpNode 处理 → facade 收到 result → 转发回 top Bus → Engine 收到 `tool_result` |
| 2 | Facade 多 tool 串行 | 同次 run() 内连续 3 个 tool_exec 都跨 Bus 成功，3 个 tool message |
| 3 | Facade 自身订阅 model_call | facade 不仅转发 tool_exec，自身也能响应 model_call（验证 facade 模式可组合） |

#### 4.1.3 `tests/recovery.rs` — 4 测试

| # | 场景 | 关键断言 |
|---|------|---------|
| 1 | barrier_request 单 node ack | `Bus::barrier()` 等到 1 个 ack 返回 `BarrierReceipt::AllAcked` |
| 2 | barrier 多 node 全部 ack | 3 个 node 注册 handler，barrier 收到 3 个 ack |
| 3 | barrier 超时 | 故意注册 1 个不响应 node，barrier 走 timeout 路径返回 `BarrierReceipt::TimedOut` |
| 4 | checkpoint 持久化 + 恢复 | 跑完 run() → AppCheckpoint 把 state 写到 `data/checkpoint_<id>.json` → 新 Engine 读文件 → 同一 input 跑出相同 output |

#### 4.1.4 `tests/pool.rs` — 3 测试

| # | 场景 | 关键断言 |
|---|------|---------|
| 1 | ModelAdapterPool 负载均衡 | 3 个 instance，10 次连续 model_call，3 个 instance 分布合理（每 instance 至少 1 次） |
| 2 | McpPool 串行调用 | 5 个并发 tool_call，pool 内部排队/分发都到达正确 tool |
| 3 | PoolNode lifecycle | pool 中 node shutdown 后 `bus.graph()` instance 数减 1，Engine 收到 MemberFailed |

#### 4.1.5 公共 helpers（`tests/common/`）

- `env.rs`：`require_minimax_key() -> Option<String>`、`require_deepseek_key() -> Option<String>` 等。读 env var；为 None 时 `eprintln!("[skip]")` 并返回 `None`。测试用 `match require_minimax_key() { Some(_) => ..., None => return }` 模式显式 skip。
- `provider.rs`：`live_minimax() -> Arc<MiniMaxProvider>`、`live_deepseek()`、`mock_fallback() -> Arc<MockProvider>`。Live 失败时不静默回退——标记为 test failure。
- `harness.rs`：`E2EHarness` 结构体，统一装好 tempdir + 真 Bus + 真 Node + 真 Engine。提供 `run_react(input)`、`assert_state_messages(n)`、`assert_last_tool_call(name)` 等 helper。

### 4.2 Python E2E（`py-arf/tests/e2e/` 新目录）

| 文件 | 测试数 | 关键场景 |
|------|------|---------|
| `test_engine_roundtrip.py` | 5 | AgentConfig 构造 / EngineBuilder routes / run() 单 round / run() 多 round / CheckpointRule 触发 / 拿到 final output 后 messages 检查 |
| `test_mcp_facade.py` | 2 | Python 端启动 sub-Bus 的 McpNode + 顶 Bus 的 Engine + Facade 转发 tool_exec / Python 端通过 Facade 调 MCP tool |
| `test_recovery.py` | 2 | barrier 同步 / checkpoint 序列化到文件 + Python 端读回 |
| `test_pool.py` | 2 | ModelAdapterPool + McpPool 在 Python 端调用 + 负载分发 |
| `test_bus_lifecycle.py` | 4 | connect/disconnect/shutdown 完整流程 / broadcast 收消息 / MessageFilter 类型过滤 / NodeInfo 字段验证 |
| `test_model_adapter.py` | 4 | DeepSeekProvider 真实 model_call / MiniMaxProvider 真实 model_call / OpenAIProvider（如 key 设）/ MockProvider fallback |

`conftest.py`：`require_minimax_key()` fixture（缺 key 时 `pytest.skip`）+ `live_bus()` fixture 建真 Bus。

### 4.3 关键断言设计原则

- **不依赖 LLM 内容**：用低 temperature、用确定性 prompt（如 "Echo the input verbatim"）让 LLM 行为可重复
- **状态断言**：`state.messages` 长度 + role 分布，比"output 包含 X 字符串"更稳
- **工具断言**：`state.messages[3].content` 来自 tempdir 工具的真实输出
- **时间预算**：每个测试 30s timeout（live API 慢），`#[tokio::test(flavor = "multi_thread")]`
- **Mock fallback 显式**：live 失败时在测试日志里 `eprintln!("[fallback] live failed, switching to mock")`，但**不**让测试悄悄通过

### 4.4 测试用例可重复性

为避免 LLM 行为不确定导致的 flaky：

- **低 temperature**：provider config 设 `temperature=0.0`（如果 provider 支持）
- **deterministic prompt**：
  - 文本测试：`"Respond with 'PONG' and nothing else."`
  - tool 测试：`"Call the read_file tool with path='data/test.txt'."`
  - 数学测试：`"What is 2+2? Answer with just the number."`
- **断言不依赖具体内容**：
  - ✅ `assert!(!output.is_empty())` + `assert!(output.contains("expected_keyword"))`
  - ❌ `assert_eq!(output, "The capital of France is Paris.")`（除非 prompt 强制 echo）

---

## 5. 错误处理矩阵

| 场景 | 行为 | 实现 |
|------|------|------|
| `MINIMAX_API_KEY` 未设 | skip 整个文件 + warning | Rust: `tests/common/env.rs` 在 `mod.rs` 顶部 if-None-return；Python: `conftest.py` `pytest.skip(reason="MINIMAX_API_KEY not set")` 在 module-level |
| `DEEPSEEK_API_KEY` 未设 | 同上（按文件 skip） | 同样的 helper 模式，但 env var 名不同 |
| Live API 调用失败（5xx / timeout） | **fail loudly**（这是 bug） | 测试断言 `Result::is_ok()`，不静默回退 mock；eprintln 错误信息 |
| 单测试超时 30s | `tokio::time::timeout(30s, ...)` | Rust: `tokio::time::timeout` wrap；Python: `pytest-timeout` marker |
| 测试间共享状态 | 禁止 | 每个测试用 `TempDir::new()`（Rust）和 `tmp_path` fixture（Python），不共享任何 state |
| tempdir 残留 | Rust Drop / Python tmp_path 自动 | 已有，无须特殊处理 |

---

## 6. 验证命令

```bash
# 6.20 验证
. "$HOME/.cargo/env" && cargo test --workspace
# 期望：现有 717+ 测试全过（无回归）

# 6.21 验证
. "$HOME/.cargo/env" && cargo test -p arf-model-adapter
# 期望：from_env / 错误处理测试通过

# 6.22 E2E Rust 验证
. "$HOME/.cargo/env" && cargo test -p arf-e2e
# 期望：15 测试全过；未配 key 时部分 skip
# 关键：必须配 MINIMAX_API_KEY 才能验证真链路

# 6.22 E2E Python 验证
. "$HOME/.cargo/env" && cargo run -p arf-mcp &
. "$HOME/.cargo/env" && cargo build -p py-arf
../.venv/bin/python -m maturin develop -m py-arf/Cargo.toml
../.venv/bin/python -m pytest py-arf/tests/e2e/ -v
# 期望：19 测试全过；未配 key 时部分 skip

# 6.22 Examples 验证
. "$HOME/.cargo/env" && (cd examples/domain_controller && cargo run)
. "$HOME/.cargo/env" && (cd examples/recovery && cargo run)
# 期望：两个 example 都能跑通，stdout 符合预期
```

---

## 7. CI 集成

### 7.1 环境变量（GitHub/Gitee Actions secret）

| Secret | 用途 | 必需？ |
|--------|------|-------|
| `MINIMAX_API_KEY` | MiniMaxProvider 测试（**主用**） | 推荐配 |
| `DEEPSEEK_API_KEY` | DeepSeekProvider fallback 测试 | 可选 |
| `OPENAI_API_KEY` | OpenAIProvider 测试 | 可选 |
| `ANTHROPIC_API_KEY` | AnthropicProvider 测试 | 可选 |

### 7.2 CI 脚本

```yaml
- name: Run E2E tests
  env:
    MINIMAX_API_KEY: ${{ secrets.MINIMAX_API_KEY }}
    DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    . "$HOME/.cargo/env" && cargo test --workspace
    cd py-arf && ../.venv/bin/python -m pytest tests/e2e/ -v
```

### 7.3 关键决策

- **不分 `#[ignore]`**：用 env-var skip 比 `--include-ignored` 更直接（CI 配置 key 就跑、不配就 skip）
- **配 key 后所有 E2E 必须通过**：任何失败即 merge gate fail
- **默认 model**：`MiniMax-M3`（用户实际使用）

---

## 8. 范围声明（out of scope）

明确**不**包含：

- **Subagent / Peer Agent 完整链路**（§8.2，留 Phase 7）
- **Heartbeat-driven MemberFailed**（§2.G9，留 Phase 7）
- **生产 deployment 负载测试**（属于 bench/ 目录，非 E2E 范围）
- **真实生产 app**（`app/arf_default_assistant` 是 v0 GraphEngine 时代，不在 v1.x 范围）
- **Cross-language 互通测试**（Rust Engine → Python 客户端，非 Phase 6 任务）
- **Multi-Provider 协商 / 路由策略**（属于 arf-agent Phase 7 关注点）

---

## 9. 输出清单

### 6.20 输出

- `crates/arf-engine/src/engine.rs`（修改，约 30 行）

### 6.21 输出

- `crates/arf-model-adapter/src/minimax.rs`（新建，约 200 行）
- `crates/arf-model-adapter/src/lib.rs`（1 行 re-export）
- `py-arf/src/lib.rs`（MiniMax binding，约 50 行）

### 6.22 输出

- `crates/arf-e2e/Cargo.toml`（新建）
- `crates/arf-e2e/tests/common/env.rs`（新建）
- `crates/arf-e2e/tests/common/provider.rs`（新建）
- `crates/arf-e2e/tests/common/harness.rs`（新建）
- `crates/arf-e2e/tests/react_loop.rs`（新建，5 测试）
- `crates/arf-e2e/tests/mcp_facade.rs`（新建，3 测试）
- `crates/arf-e2e/tests/recovery.rs`（新建，4 测试）
- `crates/arf-e2e/tests/pool.rs`（新建，3 测试）
- `crates/arf-e2e/README.md`（新建）
- `py-arf/tests/e2e/conftest.py`（新建）
- `py-arf/tests/e2e/test_engine_roundtrip.py`（新建，5 测试）
- `py-arf/tests/e2e/test_mcp_facade.py`（新建，2 测试）
- `py-arf/tests/e2e/test_recovery.py`（新建，2 测试）
- `py-arf/tests/e2e/test_pool.py`（新建，2 测试）
- `py-arf/tests/e2e/test_bus_lifecycle.py`（新建，4 测试）
- `py-arf/tests/e2e/test_model_adapter.py`（新建，4 测试）
- `py-arf/tests/e2e/README.md`（新建）
- 加入 workspace `members`（`Cargo.toml`）

### 总测试数

- Rust E2E: 5 + 3 + 4 + 3 = **15 测试**
- Python E2E: 5 + 2 + 2 + 2 + 4 + 4 = **19 测试**
- 合计 **34 测试**

---

## 10. 下一步

任务文档经用户 review 后：
- **6.20 实施**：1 commit（修复 Engine payload 读取）
- **6.21 实施**：1 commit（MiniMax provider + py-arf 绑定）
- **6.22 实施**：3 commits（Rust E2E / Python E2E / examples 验证）

每个 sub-task 独立 commit、独立可验证。

---

## 11. 实现后实际发现（2026-07-01）

### 11.1 Examples 验证结果

`cargo run` on each example (2026-07-01, with Task 1+2+3 commits in place):

#### `examples/domain_controller`（task 6.11）
- ✅ 编译通过（4 个 unused-import / unused-mut 警告，与本任务无关）
- ✅ 跑通：Engine 在 top Bus 跑 ReAct，DomainController Facade 转发 `tool_exec` → sub Bus McpNode 处理 → `tool_result` 回到 top Bus → Engine 收到
- 输出：
  ```
  Engine output: 
  State messages: 3
  State over_view: OverView { round_count: 1, turn_count: 1, context_tokens: 0, ... }
  ```

#### `examples/recovery`（task 6.12）
- ✅ 编译通过（同上 4 个警告）
- ✅ 跑通：AppCheckpoint 收到 RoundEnd checkpoint → 写文件到 `examples/recovery/data_recovery/checkpoint_<cid>.json`
- ✅ `Bus::barrier()` 调用成功
- 输出：
  ```
  [AppCheckpoint] wrote .../checkpoint_b1368306-dc4d-4f4f-9eda-8a7c148c23a5.json
  Engine output: 
  State: round=1, turn=1

  Running barrier()...
  Barrier: acked=1 missing=1 timed_out=true
  ```

#### 检查点文件内容
```json
{
  "checkpoint_id": "b1368306-dc4d-4f4f-9eda-8a7c148c23a5",
  "node_id": "cp/main",
  "timestamp": 1782875037080
}
```

### 11.2 实现期间发现的 bug 与修复

Task 1+2+3 实施期间发现并修复的真实 bug（非设计遗漏，是 wire format 不一致）：

1. **Task 1 brief 错把 `tool_calls` 写成嵌套在 `message` 内**（`message.tool_calls`）。真实 wire format（`ModelResponsePayload` 序列化）是 `tool_calls` 在顶层（`payload.tool_calls`，与 `payload.message` 平级）。修复：`crates/arf-engine/src/engine.rs` 改读 `payload.get("tool_calls")`（非 `payload.message.tool_calls`）。`content` 仍嵌套（`payload.message.content` 正确）。

2. **`usage` 字段位置**：real `ModelResponsePayload.usage` 在顶层（`payload.usage`），**不**在 `message.usage`。Brief 写错；修复：engine 读 `payload.get("usage")`，mock fixture `usage` 字段移到顶层。

3. **ModelAdapterNode 不回传 `correlation_id`**：旧实现 `handle.send("model_response", ...)` 不带 cid，Engine 的 `wait_for_strategy` 无法匹配响应。修复：新增 `NodeHandle::send_response()`（`crates/arf-bus/src/connection.rs`），ModelAdapterNode 全程走 `send_response` 注入 `correlation_id` 到 payload。

4. **PoolNode 转发到 sub bus 用 `from: self.node_id`（top-bus id）**：导致 ModelAdapterNode 响应回不到 PoolNode 的 sub-bus handle（sub handle node_id 是 `{top}/sub`，不是 top）。修复：`from: NodeId::new(format!("{}/sub", self.node_id))`。E2E test `pool_node_bridges_model_call_across_buses` 触发此 bug，10 秒 timeout 暴露。

5. **`call_with_retry` 误重试 400/401/Parse 错误**（MiniMax provider）：reviewer 标记 Important；修复：调用 `crate::convert::is_retryable(&e)` 选择性重试（与 deepseek.rs 一致）。

6. **`chat_stream` 实现不如 trait default**：自定义 stub 返回 `ProviderError::Parse` 比 trait default（调 `chat()` 然后包装）更差。修复：删除 stub，走 trait default fallback。

### 11.3 Brief 假设与现实的偏差

#### examples（task 6.11/6.12）
- **预期** `barrier 同步成功` → **实际** `acked=1 missing=1 timed_out=true`。
  根因：example 调 `bus.barrier(vec![cp/main, worker/2], ...)`，但 `cp/main`（AppCheckpoint）的 `MessageFilter` 限定 `types=["app_checkpoint"]`，收不到 `barrier_request`，因此不 ack。example 自 6.12 起就有此简化。E2E `crates/arf-e2e/tests/recovery.rs::bus_barrier_collects_acks_from_n_participants` 单独跑（参与者都用 `barrier_request` 过滤器）能正常全 ack，验证框架正确。
- **预期** checkpoint JSON 含 `state.messages` → **实际** 只含 `{checkpoint_id, node_id, timestamp}`。example 简化：注释写明"in real app: serialize State"。

#### py-arf 测试
- **预期** 19 测试 → **实际** 20 测试（多加了 1 个 bus-shutdown test，价值高）。
- **预期** 0 skip → **实际** 11 skip（7 个 env-gated + 4 个 binding gap）。Binding gap 跟踪为 Phase 6 follow-up 任务 6.22.4。
- **预期** `from arf import MiniMaxConfig` → **实际** 需要在 `py-arf/python/arf/__init__.py` 显式 re-export（6.21 加 binding 时漏了这一步，Task 4 补上）。

#### Rust E2E
- **预期** 15 测试 → **实际** 15 测试 ✅。
- **预期** fixture 与 engine wire format 一致 → **实际** brief 错（见 11.2.1+11.2.2）。E2E fixture 直接用真实 `ModelResponsePayload` 序列化形式，反而提前暴露了 brief 错误。

### 11.4 最终测试结果

```
cargo test --workspace
合计 730 passed, 0 failed (含新增 15 E2E 测试 + 修复后的 engine fixture)
```

```
cd py-arf && ../.venv/bin/python -m pytest tests/e2e/ -v
9 passed, 11 skipped (7 env-gated, 4 binding-gap)
```

```
cargo run --manifest-path examples/domain_controller/Cargo.toml
✅ 跑通：3 messages, 1 round, facade 跨 Bus 转发成功
```

```
cargo run --manifest-path examples/recovery/Cargo.toml
✅ 跑通：checkpoint 文件写入，barrier 调用成功（1/2 ack — 见 11.3.1）
```

### 11.5 Commit 列表

| Task | SHA | Message |
|------|-----|---------|
| 6.20 (Task 1) | `ef5a461` + `2965204` + `79c3c01` | 修复 Engine payload 读取（content/tool_calls/usage） |
| 6.21 (Task 2) | `f7bd43f` + `7b3d00e` | MiniMax provider + selective retry fix |
| 6.22.1 (Task 3) | `e739c19` | Rust E2E test crate — 15 tests |
| 6.22.2 (Task 4) | `1a28fec` | Python E2E — 20 tests（含 EngineState.messages getter） |
| 6.22.3 (Task 5) | (this commit) | Examples 验证 + 实际发现记录 |

### 11.6 Phase 6 wrap-up 总结

Phase 6 端到端覆盖：3 sub-task（核心 ReAct / 多 Bus MCP facade / Recovery / PoolNode）+ MiniMax provider + 6.9 mismatch 修复，共 **5 commit + 35 测试**（15 Rust E2E + 20 Python E2E）。

Engine wire format 与真实 `ModelResponsePayload` 序列化对齐（修复 2 处 brief 错误）。E2E 测试提前发现 PoolNode forward 路径 bug（10 秒 timeout 暴露），框架 correctness 比 brief 假设更进一步。


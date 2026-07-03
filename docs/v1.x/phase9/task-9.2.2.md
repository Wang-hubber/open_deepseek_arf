# 任务 9.2.2：Engine + ReAct 主循环（真实阿里百炼 qwen）

> Phase 9 — 9.2 B 单 agent 骨架 · 第 2 task（依赖 9.2.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.1（Engine + 单 ModelAdapter，mock chat）已 stable
> 输出物：`docs/v1.x/phase9/audit-probe-9.2.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果
> **本 task 首次用真实 LLM**（阿里百炼 qwen），验证 framework↔真实模型端到端

---

## 设计思路

9.2.1 用 scripted mock 验证 chat 骨架；9.2.2 探查 **ReAct 主循环**，并**首次接入真实 LLM**（阿里百炼 qwen，OpenAI 兼容端点）：

- **单 round 纯文本**：真实模型回文本 → final（无 tool）
- **多 round tool loop**：给真实 echo tool，探查真实模型是否触发 `model_call`（含 tool_call）→ `tool_exec` → `tool_result` → 再 `model_call` → final 的完整 ReAct 环
- **max_turns 边界**：真实模型下 max_turns 截断是否生效

**用真实模型的探查价值**（mock 无法覆盖）：
1. framework↔真实 LLM 端到端：真实 `model_response` 解析、真实 tool_call 格式、真实多 round 状态累积
2. **A4-001 / A3-001 真实 payload 验证**：真实 model_call/model_response 往返下，correlation_id 匹配（A4-001）与消息类型契约（A3-001）是否在真实数据上正确工作
3. 9.2.1 mock 结论的真实性回归

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 真实模型接入与凭据安全

### Provider 配置

阿里百炼 DashScope 提供 OpenAI 兼容端点，直接复用 framework 的 `OpenAIProvider`：

```rust
use arf_model_adapter::{OpenAIConfig, OpenAIProvider};

let cfg = OpenAIConfig {
    endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions".into(),
    api_key: std::env::var("DASHSCOPE_API_KEY").expect("DASHSCOPE_API_KEY"),
    models: vec!["qwen3.7-max-preview".into()],   // 跑时以 API 实际接受的模型名为准
    ..OpenAIConfig::default()
};
let provider: Arc<dyn Provider> = Arc::new(OpenAIProvider::new(cfg));
// 注入 harness：ProviderKind::Live(provider)
```

- `OpenAIConfig` doc 已举例 dashscope 端点（openai.rs:31-32）
- harness 用 `provider.supported_models().first()` 作 model_name（harness.rs:210），即 `models[0]`

### 凭据安全铁律（EXTREMELY IMPORTANT）

> **API key 绝不写进任何文件**。github / gitee 均为 public repo，key 一旦 commit 即泄露。

- key **仅**经环境变量 `DASHSCOPE_API_KEY` 传入；测试代码只 `std::env::var("DASHSCOPE_API_KEY")` 读取
- **env-gate**：env var 缺失 → 打印 skip message + `return`（不 fail），使无 key 环境（CI / 他人）不因缺 key 挂
- 跑法：`DASHSCOPE_API_KEY=sk-xxx cargo test -p arf-e2e --test react_live_qwen -- --nocapture --ignored`
- 建议加 `#[ignore]`：默认 `cargo test` 不跑真实网络调用，仅显式 `--ignored` 时跑
- **self-review 必查**：commit 前 `git grep 'sk-'` 确认无 key 字面量入库

---

## 与现有测试的边界

- `react_loop.rs`（5 test）用 scripted mock 验证 ReAct 功能（单/多 round、max_turns、cancel）
- **本 task 不重复 mock 功能**：9.2.2 独立写 `react_live_qwen.rs`，聚焦：
  - **真实 LLM** 端到端（mock 无法覆盖真实解析/tool 格式）
  - ReAct 主循环**能力等级判定**（tool_use / multi-round）
  - **真实 payload 下 A4-001/A3-001 验证**

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`react_live_qwen.rs`，env-gated，含 2-3 个真实模型子场景：

```rust
fn live_qwen() -> Option<Arc<dyn Provider>> {
    let key = std::env::var("DASHSCOPE_API_KEY").ok()?;   // 无 key → None → skip
    let cfg = OpenAIConfig {
        endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions".into(),
        api_key: key,
        models: vec!["qwen3.7-max-preview".into()],
        ..OpenAIConfig::default()
    };
    Some(Arc::new(OpenAIProvider::new(cfg)))
}

#[tokio::test]
#[ignore] // 真实网络调用，仅 --ignored 跑
async fn react_live_single_round_text() {
    let Some(provider) = live_qwen() else { eprintln!("skip: no DASHSCOPE_API_KEY"); return; };
    let mut h = E2EHarness::new(ProviderKind::Live(provider)).await.unwrap();
    let out = h.run_react("用一句话介绍你自己").await.expect("run");
    assert!(!out.is_empty(), "live model should return non-empty text");
    assert!(h.state.messages.len() >= 2);
}
```

多 round tool loop 子场景（真实模型 + echo tool）：
- `write_echo_tool(tmp)` 复用 react_loop.rs 模式
- `E2EHarness::builder(Live).with_mcp(true).tmpdir(tmp).build()`
- prompt 引导模型调 echo tool；探查真实模型**是否**触发 tool loop（不预设——真实模型可能直接回答）

```bash
$EDITOR crates/arf-e2e/tests/react_live_qwen.rs
```

### Step 2 — framework 接触点 file:line

```bash
grep -n 'pub async fn run\|dispatch_incoming\|wait_for' crates/arf-engine/src/engine.rs
grep -n 'model_call\|model_response\|tool_exec\|tool_result' crates/arf-engine/src/engine.rs | grep -v test
grep -n 'fn complete\|send_request\|impl Provider for OpenAIProvider' crates/arf-model-adapter/src/openai.rs
```

逐行解释：
- Engine::run 主循环 + wait_for 响应匹配（含 A4-001 手挖点 engine.rs:689）
- ReAct 环的消息类型（A3-001 裸字面量）
- OpenAIProvider::complete 真实 HTTP 调用

### Step 3 — framework 真实行为（真实网络）

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<从环境注入，不写文件> \
  cargo test -p arf-e2e --test react_live_qwen -- --nocapture --ignored 2>&1 | tee /tmp/react_live_run.log
```

逐行解释：
- key 经 env 传，命令行不写死于任何 committed 文件
- `--ignored` 显式跑真实网络子场景
- **探查观察**（不预设）：真实模型 out 非空；tool loop 是否触发；若模型名错，API 返回明确错误 → 调整 models[0]

**Read `/tmp/react_live_run.log` 后填 Step 4 `framework 行为`**（真实响应，非 mock）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `chat × §2.1`（真实模型） | 待探查 | 真实 LLM chat 端到端 |
| `tool_use × §2.2`（真实触发） | 待探查 | 真实模型是否触发 tool loop |
| `multi_tool_concurrent` | 不适用（留 9.5.x） | — |

按 §4 跑 signals（重点：真实 payload 下 A4-001/A3-001 是否如实工作 / 是否暴露新问题）：

```bash
grep -n 'correlation_id' crates/arf-engine/src/engine.rs | grep -v test    # 真实往返验证匹配
grep -rn '"model_call"\|"model_response"\|"tool_result"' crates/arf-engine/src/ | grep -v test
```

**C. 输出**：`audit-probe-9.2.2.md`。真实模型不产生代码病灶（病灶在 framework 抽象层，与 mock/live 无关）——预期仍是 A4-001/A3-001 蔓延实证 + 真实性确认；若真实往返暴露**新** framework 问题（如 correlation_id 在真实并发下失配），则新登记 + 追加 `lesion-registry.md`。

---

## 关键设计决策

- **真实模型 = OpenAIProvider + dashscope endpoint**：复用 framework 现成 OpenAI 兼容路径，零新代码
- **key 仅 env、env-gate skip、`#[ignore]`**：安全 + 不破坏无 key 环境的 CI
- **tool loop 不预设**：真实模型是否调 tool 由模型决定，探查真实行为
- **模型名跑时校准**：qwen3.7-max-preview 若 API 拒绝，按返回错误换 qwen-max / qwen3-max
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
# 凭据安全自查（必跑）
git grep -n 'sk-' -- crates/ docs/     # 必须无输出（无 key 入库）

# 真实模型跑通（key 经 env）
DASHSCOPE_API_KEY=<env> cargo test -p arf-e2e --test react_live_qwen -- --nocapture --ignored

# 无 key 时 skip 不 fail
cargo test -p arf-e2e --test react_live_qwen -- --ignored   # 应打印 skip、pass
```

---

## 与前序 task 的衔接

- 9.2.1 用 mock 验证 chat 骨架（D）+ 实证 A4-001/A3-001 蔓延至 Engine
- 9.2.2 首次用**真实 LLM** 验证 ReAct 主循环，并在**真实 payload** 下复核两病灶
- 后续 9.2.3（Checkpoint）/ 9.2.4（interrupt）/ 9.2.5（多 model）在此之上

---

## 下一步

1. 用户审 task 9.2.2 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（真实模型，key 经 env）
3. 整理 `audit-probe-9.2.2.md`
4. self-review（**含凭据安全 git grep 自查** / 占位 / 一致性 / scope）
5. commit `react_live_qwen.rs`（**确认无 key**）+ commit `audit-probe-9.2.2.md`（granular）
6. 进 9.2.3（Engine + 5 Checkpoint + 自定义 Rule）

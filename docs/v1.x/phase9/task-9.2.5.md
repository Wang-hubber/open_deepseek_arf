# 任务 9.2.5：Engine + 多 ModelAdapter 候选切换

> Phase 9 — 9.2 B 单 agent 骨架 · 第 5 task（依赖 9.2.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）/ 9.2.2（真实 qwen）/ 9.2.3（5 Checkpoint）/ 9.2.4（cancel / replay）
> 输出物：`docs/v1.x/phase9/audit-probe-9.2.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.1-9.2.4 探查了 Engine 的 chat / ReAct / Checkpoint / cancel 主路径（**单** ModelAdapter）；本 task (9.2.5) 探查 Engine 在**多** ModelAdapter 候选下的解析与路由：

- **ResourceRegistry::resolve_model**（registry.rs:253-269）：在 BusGraph 中**首个**匹配 `node_type="model"` + `capabilities.provider == decl.model.provider` 的节点
- **auto-derived `model_call` route**：resolve_model 返 NodeId → engine 内部用该 NodeId 直接 dispatch（不经过 `engine.routes` 查表——9.2.3 探查已确认）
- **多 ModelAdapterNode 共存**于同一 bus：每个节点有不同 `provider` 能力 → engine 按 AgentConfig.model.provider 解析
- **9.4.1 才覆盖** `ModelAdapterPoolNode`（pool_node.rs）作为 facade 包装。本 task 是 9.4.1 的**前置 foundation**：engine 自身能否处理多 raw ModelAdapterNode。

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `engine_single_model.rs`（9.2.1）：单 ModelAdapterNode 探查
- `react_live_qwen.rs`（9.2.2）：单真实 LLM 端到端
- `checkpoint_rules.rs`（9.2.3）：Checkpoint 5 位置 + 自定义 Rule
- `interrupt.rs`（9.2.4）：cancel / replay
- **本 task 不重复**单 model 行为；聚焦多 model 候选解析 + 路由：
  - 多 ModelAdapterNode 共存（不同 provider 能力）
  - Engine 按 AgentConfig.model.provider 解析
  - model_call 路由到正确节点
  - 解析错误（无匹配 provider → BuildError::MissingNodes）
- **9.4.1 才覆盖** PoolNode facade + pool overflow + sub-bus 网关

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`multi_model.rs`，mock 驱动，4-5 test cases：

```rust
// harness 加 with_extra_providers(Vec<Arc<dyn Provider>>) 方法：
//  - 1 个 extra provider = 1 个额外 ModelAdapterNode 接到 bus
//  - 每个 node 有不同 node_id（"model/e2e/extra-{i}"）+ 不同 provider 能力
//  - AgentConfig.model.provider 需与某 node 的 provider 能力匹配，否则 build 失败

#[tokio::test]
async fn multi_model_resolves_to_matching_provider() {
    // 主 model: provider = "primary"，response = "from primary"
    // 额外 model: provider = "secondary"，response = "from secondary"
    let primary = scripted_primary();
    let secondary = scripted_secondary();
    let mut h = E2EHarness::builder(ProviderKind::Mock(primary))
        .with_extra_providers(vec![secondary])
        .build().await.expect("build");
    let out = h.run_react("hi").await.expect("run");
    // AgentConfig.model.provider 默认 = "primary" → primary 响应
    assert_eq!(out, "from primary");
}

#[tokio::test]
async fn multi_model_agent_config_provider_picks_correct_node() {
    // 同样 2 个 model node，但用 .model_provider("secondary") 改 AgentConfig
    let primary = scripted_primary();
    let secondary = scripted_secondary();
    let mut h = E2EHarness::builder(ProviderKind::Mock(primary))
        .with_extra_providers(vec![secondary])
        .model_provider("secondary")
        .build().await.expect("build");
    let out = h.run_react("hi").await.expect("run");
    assert_eq!(out, "from secondary");
}

#[tokio::test]
async fn multi_model_unmatched_provider_errors_at_build() {
    // provider = "nonexistent" → BuildError::MissingNodes
    let primary = scripted_primary();
    let h = E2EHarness::builder(ProviderKind::Mock(primary))
        .with_extra_providers(vec![])
        .model_provider("nonexistent")
        .build().await;
    assert!(matches!(h, Err(e) if format!("{e:?}").contains("MissingNodes")));
}

#[tokio::test]
async fn multi_model_first_match_wins_when_same_provider() {
    // 2 个节点同 provider = "primary"（不常见但可能）：resolve 找首个
    // — registry.rs:254 find().expect "first match"
    let primary1 = scripted_named("primary", "from primary #1");
    let primary2 = scripted_named("primary", "from primary #2");
    let mut h = E2EHarness::builder(ProviderKind::Mock(primary1))
        .with_extra_providers(vec![primary2])
        .build().await.expect("build");
    let out = h.run_react("hi").await.expect("run");
    // 期望：首个匹配的 primary 节点（bus graph 顺序）响应
    assert_eq!(out, "from primary #1");
}
```

5 test cases 覆盖：

| # | test | 探查 |
|---|---|---|
| 1 | `multi_model_resolves_to_matching_provider` | 默认 AgentConfig.model.provider 解析首个匹配 |
| 2 | `multi_model_agent_config_provider_picks_correct_node` | 自定义 model_provider 选 secondary |
| 3 | `multi_model_unmatched_provider_errors_at_build` | 无匹配 provider → BuildError |
| 4 | `multi_model_first_match_wins` | 同 provider 多节点 → 首个匹配 |
| 5 | `multi_model_provider_capability_mismatch` | ModelAdapterNode 缺 provider 能力 → engine 解析失败 |

**关键探查价值**：
- 单元 1（多 model 解析）= §1.1 L4 model_switch capability
- 单元 2（自定义选择）= app 能否控制 engine 选哪个 model
- 单元 3/4/5 = 错误路径 + 解析边界
- L4 capability 等级 = D（framework 端到端供 `resolve_model` + auto-route model_call）

### Step 2 — framework 接触点 file:line

```bash
grep -n 'fn resolve_model\|model_call' crates/arf-engine/src/registry.rs | head -10
grep -n 'Provider::name\|provider.*capabilities\|capabilities.*provider' crates/arf-engine/src/registry.rs | head -10
grep -n 'model.*provider\|provider.*model' crates/arf-model-adapter/src/node.rs | head -10
```

逐行解释（按 file:line 锚定 framework 接触点）：
- `ResourceRegistry::resolve_model`（registry.rs:253-269）：在 BusGraph 中 `find()` 首个 `node_type="model"` + `capabilities.provider == decl.model.provider` 的节点
- `ModelAdapterNode` 在 bus 上的声明：`NodeInfo { node_type: "model", capabilities: { "provider": "..." } }`（harness.rs:251 区域）
- engine 内部 model_call dispatch：直接发到 resolve_model 返的 NodeId（**不**经过 `engine.routes` 查表——9.2.3 探查确认）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test multi_model -- --nocapture --test-threads=1 2>&1 | tee /tmp/multi_model_run.log
```

逐行解释：
- mock 驱动，多 scripted provider 实例（每个不同 name + response）
- 5 test cases 各跑独立 Engine + State
- 观察：resolve_model 返的 NodeId 决定哪个 node 收到 model_call，response 内容反映

**Read `/tmp/multi_model_run.log` 后填 Step 4 `framework 行为`**（真实行为，非 mock 假设）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `model_switch × §2.1` (D 路径多 model) | 待探查 | `resolve_model` 解析（registry.rs:253）+ auto-route model_call |
| `model_pool_overflow × §2.12` | 不适用（留 9.4.1） | PoolNode 是 9.4.1 内容 |
| `model_discovery × §2.12` (L4) | 部分（D 子集） | BusGraph 中 `node_type="model"` 节点自动被发现 |
| `resolve_model_error_path × §2.1` (D) | 待探查 | 无匹配 provider → `BuildError::MissingNodes`（registry.rs:266） |

按 §4 跑 signals（**重点：多 model 路径是否引入新病灶**，A3-001 / A4-001 在多 model 解析路径是否加剧）：

```bash
# A3-001 在多 model 路径：检查 provider / model_call 字面量散落
grep -rn '"model_call"\|"model"\|"provider"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test | head -20
# A4-001 在多 model 路径：resolve_model 返回 typed NodeId（不需挖 string）
grep -n 'fn resolve_model\|NodeId' crates/arf-engine/src/registry.rs | head -10
# capabilities JSON 路径：provider 是 JSON string key
grep -n '"provider"' crates/arf-engine/src/registry.rs crates/arf-model-adapter/src/node.rs | head -10
```

**C. 输出**：`audit-probe-9.2.5.md`。多 model 解析在 `ResourceRegistry::resolve_model`（registry.rs:253-269），若引入新病灶应集中在此。

---

## 关键设计决策

- **harness 加 `with_extra_providers(Vec<Arc<dyn Provider>>)`**：每 extra provider 创一个额外 ModelAdapterNode 接到 bus，NodeId = `model/e2e/extra-{i}`，capability `{"provider": provider_name}`。harness 端，不动 framework。
- **harness 加 `.model_provider(&str)`**：覆盖 `AgentConfig.model.provider` 默认值。测试可显式选 primary / secondary / 不存在。
- **不预设 resolve_model 的"首个"语义**：registry.rs:254 `find()` 是"首个"，但 BusGraph node 顺序由 bus.connect 调用顺序决定——framework 行为需实证。
- **probe 不动 PoolNode**：9.4.1 task 覆盖。
- **不测 `model_call` broadcast 行为**：engine 内部 model_call 是**单播**到 resolve_model 返的 NodeId（registry.rs:48 auto-derived route + engine.rs:466 model_call send），非广播。

---

## 验证命令（self-review）

```bash
# 跑通
cargo test -p arf-e2e --test multi_model -- --nocapture --test-threads=1

# resolve_model cross-check
grep -A 15 'fn resolve_model' crates/arf-engine/src/registry.rs

# harness changes
grep -n 'with_extra_providers\|model_provider' crates/arf-e2e/tests/common/harness.rs

# 凭据安全自查
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架 + A4-001/A3-001 Engine 蔓延
- 9.2.2 真实 LLM 端到端 chat + 工具 loop + 真实 payload 复测病灶
- 9.2.3 Engine Checkpoint 5 位置 + 自定义 Rule（无新病灶）
- 9.2.4 Engine cancel / interrupt + replay from session（无新病灶）
- **9.2.5** Engine 多 ModelAdapter 候选切换（resolve_model）
- 后续 9.4.1（ModelAdapterPoolNode facade）在 9.2.5 基础上

---

## 下一步

1. 用户审 task 9.2.5 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock 驱动）
3. 整理 `audit-probe-9.2.5.md`
4. self-review（凭据 / 一致性 / scope）
5. commit `multi_model.rs` + commit `audit-probe-9.2.5.md`（granular）
6. 进 9.4.1（ModelAdapterPoolNode facade）

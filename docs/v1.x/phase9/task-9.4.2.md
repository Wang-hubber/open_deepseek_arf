# 任务 9.4.2：Provider::supported_models capability-based 路由

> Phase 9 — 9.4 L4 模型能力大类 · 第 2 task（依赖 9.4.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.4.1（pool facade 探查通过）+ 9.2.5（多 ModelAdapter 候选切换，真实双 LLM 实证）
> 输出物：`docs/v1.x/phase9/audit-probe-9.4.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.4.1 探查了 pool facade 端到端 + 暴露 F-001/F-002/F-003。9.2.5 探查了多 ModelAdapter 候选切换（按 provider 匹配）。本 task (9.4.2) 探查 **provider `supported_models` capability-based 路由**——engine 是否按 `Provider::supported_models()` 返的 model list 路由 model_call？

**Framework 现状**（探查发现）：
- ✅ `Provider::supported_models` trait 方法（provider.rs:33）—— 返 `&[String]`
- ✅ `ModelAdapterNode::new` 在构造时调 `provider.supported_models()`（node.rs:38）—— 把 model list 塞到 `NodeInfo.capabilities.models`
- ✅ `ResourceRegistry::resolve_model`（registry.rs:253-269）—— 实际按 `n.capabilities.get("provider")` 匹配，**不**按 `model_name` 匹配
- ❌ `Provider::supported_models` 在路由时**完全不被使用**（仅作元数据塞 capabilities）

**关键探查问题**（不预设答案）：
1. Engine 实际按 `provider` 匹配（**已实证** 9.2.5）—— 但**不**按 `model_name` 匹配
2. 若 2 节点同 provider 但不同 `supported_models`，engine 选哪个？—— 预期：首个节点（忽略 model_name）
3. `supported_models` capability 是"元数据"还是"路由 key"？—— 当前是元数据，**不**是路由 key

**潜在 F-lesion 候选**：
- **F-007**：Engine 不按 `model_name` 路由，只按 `provider`——`Provider::supported_models` 在路由时完全无效

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.2.5 实证：qwen (provider="openai") + deepseek (provider="deepseek") 共存 → engine 按 provider 选 ✓
- `crates/arf-model-adapter/src/provider.rs:140`：单元测试 `provider_supported_models` 验证 trait 方法 ✓
- **本 task 不重复**：provider trait 单元测试 / 多 provider 共存（9.2.5 已实证）
- **本 task 聚焦**：
  - 端到端 probe：engine 是否按 `model_name` 路由？
  - F-007 实证：2 节点同 provider 但不同 model → engine 行为？

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`capability_routing.rs`，mock + 真实 LLM，3-4 test cases：

```rust
// 1. Two ScriptedProviders with SAME provider name but different model_name
//    (e.g., both "openai" but supports ["qwen3.7-max-preview"] vs ["qwen3.5-turbo"])
//    Verify engine routes to FIRST node regardless of cfg.model.model_name
```

3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `engine_routes_by_provider_not_model_name` | 2 节点同 provider="openai"，不同 `supported_models`（["qwen3.7"] vs ["qwen3.5"]），cfg.model.model_name = "qwen3.7" → engine 路由到首个节点（**忽略** model_name 匹配） |
| 2 | `engine_ignores_unsupported_model_name` | 1 节点 provider="openai" supports=["qwen3.7"]，cfg.model.model_name = "qwen3.5-turbo"（不在 supports 里） → engine **仍**路由（只检查 provider，不检查 model_name 是否在 supports） |
| 3 | `supported_models_in_capabilities_advertised` | 验证 NodeInfo.capabilities 含 `"models": [...]`（从 `Provider::supported_models()` 来），engine **当前不读**这字段 |
| 4 | `real_qwen_specific_model_name_routing` | 真实 qwen，`Provider::supported_models = ["qwen3.7-max-preview"]`，cfg.model.model_name = "qwen3.7-max-preview" → engine 路由 OK；但如果 model_name 是 "qwen3.5-turbo"（不在 supports） → engine **仍**路由（不检查 model_name） |

**关键探查价值**：
- 单元 1-2：**F-007 实证**（engine 不按 model_name 路由）
- 单元 3：D（`supported_models` 元数据正确传到 capabilities）
- 单元 4：真实 LLM 实证 F-007

### Step 2 — framework 接触点 file:line

```bash
grep -n "supported_models" crates/arf-model-adapter/src/{provider,node,pool_resource}.rs | head -10
grep -n "capabilities.*models\|capabilities.*provider" crates/arf-model-adapter/src/{node,pool_node}.rs | head -10
grep -n "resolve_model\|capabilities.get" crates/arf-engine/src/registry.rs | head -10
```

逐行解释：
- `Provider::supported_models` trait：provider.rs:33
- `ModelAdapterNode::new` 读 supported_models 塞 capabilities：node.rs:38
- `ModelAdapterPoolNode::connect` 写 advertised_provider 塞 capabilities：pool_node.rs:46-77
- `ResourceRegistry::resolve_model` 按 capabilities.provider 匹配：registry.rs:253-269

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test capability_routing -- --nocapture --test-threads=1 2>&1 | tee /tmp/capability_routing_run.log
```

逐行解释：
- mock 3 个 test 验证 F-007
- 真实 LLM 1 个 test 验证真实 qwen 行为

**Read `/tmp/capability_routing_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `provider_capability_routing × §2.1` (engine 路由) | **待探查（F-007 candidate）** | `ResourceRegistry::resolve_model`（registry.rs:253-269）按 `capabilities.provider` 匹配，**不**按 `model_name` 匹配 |
| `supported_models_advertised × §2.1` (capability 传播) | D | `ModelAdapterNode::new`（node.rs:38）正确把 `supported_models` 塞到 `NodeInfo.capabilities.models` |
| `unsupported_model_routing × §2.1` (越界不报错) | **待探查（F-007 candidate）** | 9.4.2 实证：cfg.model.model_name 不在 `supported_models` 时 engine **不**报错（仅按 provider 匹配） |

按 §4 跑 signals（**重点：capability 路由路径是否引入新病灶**，A3-001 / A4-001 是否加剧 + **F-007 实证**）：

```bash
# A3-001：检查 "model_name" / "provider" / "supported_models" 字面量散落
grep -rn '"model_name"\|"provider"\|"supported_models"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test | head -15
# A4-001：capability 路由是否集中
grep -n 'resolve_model\|fn route_model' crates/arf-engine/src/registry.rs | head -5
# F-007 实证：model_name 是否参与路由
grep -n 'model_name' crates/arf-engine/src/registry.rs | head -5
```

**C. 输出**：`audit-probe-9.4.2.md`。
- capability 路由路径若引入新病灶应在 registry.rs:253-269 (resolve_model)
- **F-007 候选**——记入 lesion-registry（若实证成立）

---

## 关键设计决策

- **不写新 framework 代码**：9.4.2 是探查 task（capability 路由已实现），framework 抽象已存在。本 task 纯探查 + 实证 F-007。
- **mock 测优先**：3 mock test 验证 capability 路由行为（不需要真实 LLM）。
- **F-007 探查重点**：Engine 是否按 `model_name` 路由？预期：**不**按 model_name 路由（仅按 provider）—— F-007 framework 缺 model-level 路由。
- **不测 provider pool（9.4.1 探查）**：9.4.1 探查了 pool facade，9.4.2 探查 routing 行为。
- **真实 LLM 测仅 1 个**：验证真实 qwen capability 行为。

---

## 验证命令（self-review）

```bash
# 跑通
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test capability_routing -- --nocapture --test-threads=1

# Provider::supported_models trait
grep -B 1 -A 5 "fn supported_models" crates/arf-model-adapter/src/provider.rs | head -10

# ModelAdapterNode::new 读 supported_models
sed -n '35,50p' crates/arf-model-adapter/src/node.rs

# ResourceRegistry::resolve_model（关键探查点）
sed -n '253,275p' crates/arf-engine/src/registry.rs

# §4 信号 cross-check
grep -rn '"model_name"\|"provider"\|"supported_models"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
grep -n 'model_name' crates/arf-engine/src/registry.rs

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.5 多 ModelAdapter 候选切换（按 provider 匹配）
- 9.4.1 pool facade + F-001/F-002/F-003
- **9.4.2** Provider::supported_models capability-based 路由（F-007 candidate）
- 后续 9.4.3（Pool overflow 三策略完整覆盖）—— 9.4.1 已覆盖大部分

---

## 下一步

1. 用户审 task 9.4.2 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + 真实 qwen）
3. 整理 `audit-probe-9.4.2.md`（F-007 实证）
4. 更新 `lesion-registry.md`：F-007（Engine 不按 model_name 路由）
5. self-review（凭据 / 一致性 / scope）
6. commit `capability_routing.rs` + commit `audit-probe-9.4.2.md` + commit `lesion-registry.md`（granular）
7. 回 9.4.3（Pool overflow 三策略完整覆盖）
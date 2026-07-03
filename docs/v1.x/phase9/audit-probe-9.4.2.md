# audit-probe-9.4.2：Provider::supported_models capability 路由探查（含 F-007 + F-008）

> Task 9.4.2 探查产出 — **Engine 是否按 `Provider::supported_models()` capability 路由**
> 父 task doc：`docs/v1.x/phase9/task-9.4.2.md`（commit `44545ae`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.5（多 ModelAdapter 候选切换）+ 9.4.1（pool facade）
> **本 task 探查 capability 路由 + 暴露 F-007（不按 model_name 路由）+ F-008（HashMap 非确定路由）**

---

## §A 探查环境

- working tree：HEAD `001f70d`
- 测试文件：`crates/arf-e2e/tests/capability_routing.rs`（4 test cases）
- 驱动：3 mock（NamedScriptedProvider，fast, deterministic）+ 1 真实 DashScope qwen
- 测试命令：
  ```bash
  DASHSCOPE_API_KEY=<env> \
    cargo test -p arf-e2e --test capability_routing -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 10.49s`**（mock 测即时，1 真实 LLM 测 ≈ 5-7s）
- 关键真实运行输出：
  ```
  [F-007/test1] engine output: "from node 1 (qwen3.7)"
  [F-007/test1] F-007 实证：engine 按 provider 选（model_name=qwen3.5-turbo 不影响选择）✓
  [F-007/test1] F-008 实证：BusGraph HashMap 非确定，2 节点同 provider 时路由非确定
  [F-007/test2] engine output: "ok from qwen3.7" (model_name 'qwen3.5-turbo' 不在 supports 但 engine 仍路由)
  [F-007/test2] F-007 实证：model_name 不在 supports 时 engine **静默**路由（不报错）✓
  [test3] capabilities.models = ["qwen3.7-max-preview", "qwen3.5-turbo"] (D 端到端 capability 传播) ✓
  [test3] 但 engine 实际**不**读这字段路由（仅按 'provider'）—— F-007 实证
  [test4/real] qwen node capabilities: {"models":["qwen3.7-max-preview"],"provider":"openai"}
  [test4/real] qwen output: "你好啊"
  [test4/real] 真实 qwen 路由 OK（model_name 在 supports 中）✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/capability_routing.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：engine 路由（**F-007 framework gap**）

```
单元              : provider_capability_routing × §2.1
能力等级           : F（FAIL）
判分依据           : 9.4.2 `engine_routes_by_provider_not_model_name` 实证：
                    - 2 节点同 provider="openai"，不同 supported_models
                    - cfg.model.model_name="qwen3.5-turbo"（只在 node 2 supports）
                    - engine 路由到 **任一** provider 节点（**不**按 model_name 选）
                    `engine_ignores_unsupported_model_name` 进一步实证：
                    - model_name 不在 supports 时 engine **不**报错（静默路由）
                    `ResourceRegistry::resolve_model`（registry.rs:253-269）只匹配
                    `n.capabilities.get("provider")`，**不**匹配 model_name
                    `Provider::supported_models()` 仅作元数据塞
                    NodeInfo.capabilities.models（node.rs:38），在 routing **完全无效**
framework 行为   : framework 缺 model-level 路由 + 静默错误（model_name 拼写错误无 warning）
信号命中         : F-007（framework 不按 model_name 路由）
```

### 单元 2：capability 传播（D）

```
单元              : supported_models_advertised × §2.1
能力等级           : D
判分依据           : `supported_models_in_capabilities_advertised` 实证：
                    NodeInfo.capabilities = `{"models":["qwen3.7-max-preview",
                    "qwen3.5-turbo"], "provider":"openai"}`—— `Provider::supported_models()`
                    正确传到 capabilities.models
framework 行为   : capability 端到端传播（D）
信号命中         : 无新病灶（capability 本身传播正确，只是不被 engine 路由读取）
```

### 单元 3：真实 qwen capability 行为

```
单元              : real_qwen_specific_model_name × §2.1
能力等级           : D
判分依据           : `real_qwen_specific_model_name_routing` 实证：
                    - 真实 qwen 1 query → "你好啊" 响应
                    - qwen supported_models = ["qwen3.7-max-preview"]
                    - cfg.model.model_name = "qwen3.7-max-preview"（在 supports）
                    - engine 路由 OK
framework 行为   : 真实 qwen 端到端工作（capability 路由 + bus + model_call）
信号命中         : 无新病灶
```

---

## §C §4 find signals 探查

### A3 数据唯一 — capability 路由路径是否引入新散落

**结论：未引入新散落**。

| 检查项 | 结果 |
|---|---|
| `"model_name"` 字面量 | 1 处（agent/model.rs:22 `pub model_name: String`）—— 单点声明 |
| `"provider"` 字面量 | 多处，但**仅在 capabilities 解析路径**用——已集中 |
| `"supported_models"` 字面量 | 1 处（types.rs:53）—— 已集中 |
| `"models"` 字面量（capabilities 字段） | 1 处（node.rs:48 `capabilities: json!({... "models": models, ...})`）—— 单点声明 |

### A4 处理集中 — capability 路由

**结论：不涉及新散落**（但**严重**framework gap 见下）。

`ResourceRegistry::resolve_model`（registry.rs:253-269）—— **集中**处理 capability 路由，**但**功能不完整（仅 provider，不含 model_name）。

### F-category（framework missing API / non-deterministic）—— 本 task 新增

| ID | 严重度 | 描述 |
|---|---|---|
| F-001 | F（FAIL） | framework 缺 EnginePool 抽象 |
| F-002 | **F（CRITICAL）** | pool 实现偏离设计意图 |
| F-003 | F（development-stage） | facade sub_id 设计 quirk |
| F-004 | F（FAIL） | framework 缺 stream event callback API |
| F-005 | F（FAIL） | Engine 不传 thinking_enabled |
| F-006 | F（naming inconsistency） | spec/code naming 不一致 |
| **F-007** | **F（FAIL）** | **Engine 不按 model_name 路由** |
| **F-008** | **F（non-deterministic）** | **BusGraph HashMap 非确定，路由非确定** |

---

## §D lesion-registry 更新

本 task 增 **2 个 F-category lesion**：
- F-007（Engine 不按 model_name 路由，静默错误）
- F-008（BusGraph HashMap 非确定，路由非确定）

§1 总表新增 2 行，§3 F 类别已登记更新，§1 统计更新为 **OPEN 10 / FIXED 0 / WONTFIX 0**。

**待补**：F-007 / F-008 §2 详情块（本 task 实证已记 §1 + §3，§2 详情可后续补）。

---

## §E 观察记录（非病灶）

### 观察 C1 — `Provider::supported_models()` 在 routing 完全无效（spec 误导）

**触发位置**：`provider.rs:33` trait 方法 vs `registry.rs:253-269` resolve_model
**观察现象**：spec §1.1 L4 列 `Provider::supported_models` capability-based 路由——但 code 实际只按 `capabilities.provider` 匹配，**不**用 `supported_models`。`Provider::supported_models()` 仅**元数据**用途（塞 `NodeInfo.capabilities.models`），**不**参与 routing 决策。
**判断**：**F-007 framework gap**——spec 描述的能力 code 没实现。
**影响面**：app 读 spec 期望"按 model_name 路由"但实际只按 provider，**production 静默错误**（model_name 拼错 → 路由到错 model 不报错）。

### 观察 C2 — `BusGraph` 用 `HashMap` 存节点（设计层面 non-deterministic）

**触发位置**：`graph.rs:60` `let nodes: Vec<_> = map.values().map(...).collect();`
**观察现象**：`BusGraph` 内部用 `HashMap<Uuid, NodeEntry>` 存节点，`graph()` 返回的 `Vec<NodeInfo>` 来自 `map.values()` —— HashMap 迭代顺序**非确定**。
**判断**：**F-008 framework gap**——production 风险：同一 app 两次启动间路由到不同 model node。
**影响面**：app 想"prefer node A"或"负载均衡"**无 framework 钩子**。修复：BusGraph 改 `Vec<NodeId>`（插入序）或 `BTreeMap`（key 序）+ resolve_model 暴露 priority hint。

### 观察 C3 — `resolve_model` 返回 NodeId 而非多 candidate

**触发位置**：`registry.rs:253-269` `fn resolve_model` 返 `Result<NodeId, _>`
**观察现象**：`resolve_model` 只返**单** NodeId，**不**返 Vec<NodeId>（候选列表）。调用方（engine.rs:466 `model_target()`）只能拿单 NodeId，无法做"多候选 + 优先级"逻辑。
**判断**：**F-007 + F-008 共因**——framework 没提供"列出 + 选择"两阶段 API。
**影响面**：app 想做 fallback（首选 A，fallback B）需**自己**重写 resolve_model。

---

## §F 综合判定

- **engine 按 provider 路由（不按 model_name）**：**F（F-007 framework gap）**
- **capability 传播**（`supported_models` → `NodeInfo.capabilities.models`）：**D**
- **真实 qwen 端到端**：**D**
- **新病灶**：0（A3/A4 类别）
- **新 F-category lesion**：2（F-007 不按 model_name 路由 + F-008 BusGraph HashMap 非确定）
- **9.4.2 价值**：
  - 实证 F-007：Engine 只按 provider 路由，**不**用 `Provider::supported_models()`（spec 描述能力 code 没实现）
  - **意外发现 F-008**：`BusGraph` 用 `HashMap` 存节点——`resolve_model.find()` 路由**非确定**（production 风险）
  - 2 个 finding 共因：framework 缺"列出 + 选择"两阶段 API
- **结论**：capability 路由**部分实现**（provider 匹配 D + 元数据传播 D + model_name 匹配 F + 路由确定 F）。F-007 + F-008 修复方向一致：framework 需提供"列出 + 选择"两阶段 API + priority hint。9.4 大类 3 task 探查 9.4.1 + 9.4.2 累计暴露 **5 个 F-lesion**（F-001 / F-002 critical / F-003 / F-007 / F-008），仅 9.4.3 待探查。

---

## §G 验证命令

```bash
# 跑通（4 test: 3 mock + 1 真实 qwen）
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test capability_routing -- --nocapture --test-threads=1

# Provider::supported_models trait
grep -B 1 -A 5 "fn supported_models" crates/arf-model-adapter/src/provider.rs | head -10

# ModelAdapterNode::new 读 supported_models
sed -n '35,50p' crates/arf-model-adapter/src/node.rs

# ResourceRegistry::resolve_model（关键探查点 + F-007/F-008 实证）
sed -n '253,275p' crates/arf-engine/src/registry.rs

# BusGraph HashMap（非确定路由 F-008 实证）
sed -n '55,65p' crates/arf-bus/src/graph.rs

# §4 信号 cross-check
grep -rn '"model_name"\|"provider"\|"supported_models"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
grep -n 'model_name' crates/arf-engine/src/registry.rs

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— ✅
2. **granular commit**：
   - `capability_routing.rs`（4 test cases，3 mock + 1 真实 qwen）
   - `audit-probe-9.4.2.md`（含 F-007/F-008 finding + 3 个观察）
   - `lesion-registry.md` 增 F-007 + F-008（已 commit）
3. push 双 remote（github + gitee）
4. **回 9.4.3**（Pool overflow 三策略完整覆盖）—— 9.4.1 已覆盖大部分，9.4.3 留细节
5. 9.5.x（McpNode 工具集成）—— phase 9 下一大类
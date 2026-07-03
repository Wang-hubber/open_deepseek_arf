# audit-probe-9.2.5：Engine + 多 ModelAdapter 候选切换探查（真实双 LLM）

> Task 9.2.5 探查产出 — **真实双 LLM 端到端**（DeepSeek V4-flash + DashScope qwen3.7-max-preview）
> 父 task doc：`docs/v1.x/phase9/task-9.2.5.md`（commit `84ebfec`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.1（Engine + 单 ModelAdapter mock）/ 9.2.2（真实 qwen ReAct loop）/ 9.2.3（5 Checkpoint）/ 9.2.4（cancel / replay）
> **本 task 探查 Engine 多 ModelAdapter 共存 + 按 `AgentConfig.model.provider` 解析 + auto-route model_call**

---

## §A 探查环境

- working tree：HEAD `84ebfec`
- 测试文件：`crates/arf-e2e/tests/multi_model.rs`（5 test cases）
- 探查基础设施：
  - harness 加 `with_extra_providers(Vec<Arc<dyn Provider>>)` + `model_provider(&str)` 方法
  - `common/provider.rs` 加 `live_deepseek()` factory（`DeepSeekProvider` + `deepseek-v4-flash`）
- 驱动：**真实 LLM**（DeepSeek V4-flash via DEEPSEEK_API_KEY + qwen3.7-max-preview via DASHSCOPE_API_KEY）
- 测试命令：
  ```bash
  DEEPSEEK_API_KEY=<env> DASHSCOPE_API_KEY=<env> \
    cargo test -p arf-e2e --test multi_model -- --nocapture --test-threads=1
  ```
- 结果：`5 passed; 0 failed; 4.80s`（真实 LLM 端到端）
- 关键真实运行输出：
  ```
  [multi_model] qwen_only_default      response: 我是通义千问，由阿里巴巴集团通义实验室自主研发的大语言模型。
  [multi_model] deepseek_only_default  response: 我是由深度求索公司（DeepSeek）创造的AI助手...
  [multi_model] both_picks_qwen        response: 我是通义千问，由阿里巴巴集团通义实验室自主研发的大语言模型。
  [multi_model] both_picks_qwen:       is_qwen=true, is_deepseek=false ✓
  [multi_model] both_picks_deepseek    response: 我是DeepSeek，由深度求索公司创造的AI助手...
  [multi_model] both_picks_deepseek:   is_deepseek=true, is_qwen=false ✓
  [multi_model] invalid_provider:      Strict route NodeId 不在 BusGraph 上: ["model: provider=\"nonexistent-provider\" model=\"scripted-v1\""]
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-ce9ddd\|sk-9943d' -- crates/ docs/   # 必须无输出
$ git grep -n 'ce9ddd9\|ab948' -- crates/ docs/         # 无 key 前缀/后缀
# 两次 git grep 均无输出 → 凭据未入库
```

---

## §B (capability, 情景) 单元判定

### 单元 1：model_switch × §2.1（D 路径多 model）

```
单元              : model_switch × §2.1 — Engine 多 ModelAdapter 候选切换
能力等级           : D
判分依据           : `ResourceRegistry::resolve_model`（registry.rs:253-269）
                    找 BusGraph 中首个 `node_type="model"` +
                    `capabilities.provider == decl.model.provider` 节点；
                    auto-derived `model_call` route 直接发到该 NodeId
                    （registry.rs:48-49 + 53）。
                    真实断言（multi_model.rs:130-156）：2 个真实 model 节点
                    （qwen + deepseek）共存于 bus，default provider=qwen
                    → qwen 响应 "通义千问..."；override provider=deepseek
                    → deepseek 响应 "DeepSeek..."。
framework 行为   : app 调 1 行 `with_extra_providers` + `model_provider("deepseek")`
                    即可在 qwen + deepseek 真实 LLM 间切换；engine
                    按 capabilities.provider 字符串匹配路由，**不依赖** BusGraph
                    节点插入顺序（test 4 验证：qwen 先接，deepseek 后接，
                    仍正确路由到 deepseek）。
信号命中         : A3 / A4 不加剧（详见 §C）
```

### 单元 2：model_discovery（部分 D）

```
单元              : model_discovery × §2.12（部分）
能力等级           : D（子集）
判分依据           : BusGraph 中 `node_type="model"` 节点自动被发现
                    （harness 加的额外 provider 自动作为 ModelAdapterNode
                    接到 bus + 声明 capabilities）。engine 不需 app
                    显式 list 候选——`resolve_model` 扫描 BusGraph。
                    完整 discovery（Provider::supported_models 列出
                    多 model 候选）留 9.4.1 PoolNode 探查。
framework 行为   : 真实 LLM 多 model 节点共存 + 自动注册 capabilities，
                    engine 解析时按 provider 匹配；D 子集。
信号命中         : 无新病灶
```

### 单元 3：resolve_model error path（D）

```
单元              : resolve_model_error × §2.1
能力等级           : D
判分依据           : provider 不匹配 → BuildError::MissingNodes
                    （registry.rs:266-269）。
                    真实断言（multi_model.rs:165-184）：override
                    "nonexistent-provider" → build 失败，error 包含
                    "Strict route NodeId 不在 BusGraph 上: [\"model: provider=\\\"nonexistent-provider\\\" model=\\\"scripted-v1\\\"]"。
                    不需 LLM 调用（error 在 build 阶段）。
framework 行为   : framework 不静默失效——engine 解析时无匹配节点
                    立即报错。错误信息明确指出缺失 provider + model_name。
信号命中         : 无新病灶
```

### 单元 4 / 5：基线

| 单元 | 等级 | 备注 |
|---|---|---|
| `multi_model_qwen_only_default` | D | baseline：单 qwen 节点 default provider 正确路由 |
| `multi_model_deepseek_only_default` | D | baseline：单 deepseek 节点 default provider 正确路由 |

---

## §C §4 find signals 探查

### A3 数据唯一 — multi-model 路径是否引入新散落

**结论：未引入新散落**（structured capability match，不是裸字面量）。

| 检查项 | 结果 |
|---|---|
| `"model"` 字面量（node_type） | 2 处（registry.rs:255 find 谓词 + 346 ResourceRegistry 默认 ResourceSpec），**2 处都用同一种约定（"model" = 模型节点类型）**——非散落，是结构性 key |
| `"provider"` 字面量（capabilities key） | 2 处（registry.rs:257 capabilities.get + 347 capabilities 声明），**2 处用同一种约定（capabilities.provider = 模型 provider 标识）**——非散落 |
| `"model_call"` 字面量（msg_type） | 1 处新增无关；既有 A3-001 已记 model_call 散落（9.2.1 探查），本 task **不加剧** |
| correlation_id 散落 | 0 处（registry.rs 无 correlation_id 引用） |

### A4 处理集中 — multi-model 路径

**结论：不涉及**。

`resolve_model`（registry.rs:253-269）返 typed `NodeId`（registry.rs:268），engine 内部
model_call 直接用该 NodeId dispatch（registry.rs:48 + engine.rs:466 model_call send），
**不**经过 `engine.routes` 查表（9.2.3 探查已确认 model_call auto-route）。multi-model
路径无 correlation_id 流程，**不加剧** A4-001。

### A1 / A2 — multi-model 抽象

- **A1 原子化**：`ResourceRegistry::resolve_model` 1 个职责（按 provider 匹配 model 节点）。`with_extra_providers` 1 个职责（添加候选）。`model_provider` 1 个职责（覆盖默认 provider）。无 `and / or` 多职责模式。
- **A2 正交性**：`Provider` trait + `ModelAdapterNode` 已存在；harness 的 `with_extra_providers` 不引入新依赖，**仅复用既有 `ModelAdapterNode::new`**（node.rs:283）。Provider 切换完全正交于其他 framework 抽象（无 cross-import 强依赖）。

**§4 新病灶：0**。**已登记病灶 multi-model 路径实证：0 加剧**（A3-001 既有 model_call 散落不因 multi-model 加剧，A4-001 不涉及 multi-model 路径）。

---

## §D lesion-registry 更新（无需新增）

本 task 不新登记。A3-001 / A4-001 在 multi-model 路径均**未加剧**。

§1 总表无需追加新行；§2 详情无需修改。

---

## §E 观察记录（非病灶）

### 观察 M1 — `with_extra_providers` 按数组顺序接 bus，`resolve_model` 按 BusGraph 顺序 find

**触发位置**：`harness.rs` (新增 extra ModelAdapterNode 接入) + `registry.rs:254 find()`
**观察现象**：`with_extra_providers(vec![qwen, deepseek])` 时，qwen 先接 bus（NodeId `model/e2e`），
deepseek 后接（NodeId `model/e2e/extra-0`）。BusGraph 中节点顺序 = 接 bus 顺序。
`resolve_model` 用 `find()`（registry.rs:254）找首个匹配 provider 的节点——**依赖 BusGraph 顺序**。

**实测**：
- `multi_model_both_picks_qwen`：primary=qwen, extra=deepseek, default provider=qwen
  → qwen 响应（**qwen 先接，find 找到 qwen 即可**——未实测"同 provider 多节点 first-match"边界）
- `multi_model_both_picks_deepseek`：primary=qwen, extra=deepseek, override=deepseek
  → deepseek 响应（**qwen 节点 capabilities.provider="openai" 不匹配 "deepseek"，跳过；deepseek 节点匹配**）

**判断**：**不构成病灶**——framework 按 BusGraph 顺序 find 是合理实现（"第一个"语义清晰），且 app 显式 `model_provider("...")` 时不依赖顺序。

**影响面**：若 2 节点同 provider（不常见，但可能——同一 provider 多 deployment），`find()` 返回 BusGraph 第一个——**未实测**，留 9.4.1 PoolNode 探查覆盖（pool 自身做选择逻辑，不依赖 BusGraph 顺序）。

### 观察 M2 — `live_deepseek()` factory 与 `live_qwen()` 命名风格一致，但 provider 标识不同

**触发位置**：`provider.rs:148-165`（live_minimax / live_qwen / live_deepseek）
**观察现象**：
- `live_minimax` → MiniMaxProvider，provider 标识 `"minimax"`
- `live_qwen` → OpenAIProvider + dashscope endpoint，provider 标识 `"openai"`（**不是 `"qwen"`**）
- `live_deepseek` → DeepSeekProvider，provider 标识 `"deepseek"`

`live_qwen` 的 provider 标识是 `"openai"`（OpenAIProvider.name() 默认值）——这导致
`multi_model_both_picks_qwen` 中 qwen 节点 capabilities.provider="openai"，与
default provider="openai" 匹配。如果用 `live_qwen` + `live_deepseek` 共存，
qwen 节点标识 "openai"，deepseek 节点标识 "deepseek"，两者**不冲突**。

**判断**：**不构成病灶**——但 `live_qwen` 命名为 qwen 却 provider 标识为 openai 是
**轻微 confusing**。app 实际用时应理解 OpenAIProvider 是 OpenAI 兼容 protocol，
provider 标识反映 protocol 而非具体模型。**留 fix phase 决策**：是否要 `live_qwen()`
覆盖 provider 标识为 "qwen" 或 "dashscope"。

**影响面**：app 写 AgentConfig.model.provider 时的认知成本——期望 "qwen" 实际要写 "openai"。

### 观察 M3 — DeepSeek 模型名 typo 容错（"deepseek-v4-flash" vs "DeepSeekV4-flsh"）

**触发位置**：user 初始消息 typo "DeepSeekV4-flsh"
**观察现象**：deepseek_live.rs:43 已实证 `deepseek-v4-flash` 是合法 model 名（live
integration test 实际跑过）。typo "DeepSeekV4-flsh" 不可能通过——但本探查使用 user
修正后的 `deepseek-v4-flash`（小写 + dash），实测工作。
**判断**：**不构成病灶**——framework 行为正确（拒绝无效 model 名），user typo 自行修正。
**影响面**：仅文档/沟通成本。

---

## §F 综合判定

- **model_switch**：D（qwen + deepseek 真实 LLM 共存 + 按 provider 路由工作）。
- **model_discovery**：D（子集，BusGraph 自动发现 `node_type="model"` 节点）。
- **resolve_model error path**：D（BuildError::MissingNodes 在 build 阶段触发，error 信息明确）。
- **新病灶**：0。
- **已登记病灶 multi-model 路径实证**：A3-001 **不加剧**（capability key 是结构性，非散落）/ A4-001 **不涉及**。
- **9.2.5 价值**：**首次用真实双 LLM 端到端验证 Engine 多 ModelAdapter 解析**。补足 9.2.2（单 LLM）和 9.2.1（mock 单 model）未覆盖的"多 LLM 共存 + 路由"维度。framework 端到端供 multi-model resolution，app 仅需 `.with_extra_providers + .model_provider("...")` 两行 API。
- **结论**：Engine 多 ModelAdapter 候选切换在真实双 LLM 端到端下功能达标（D × 3）；无新病灶。**9.2.5 是 9.4.1（ModelAdapterPoolNode facade）的前置 foundation**——engine 自身能处理多 raw 节点，pool 只是 facade 包装。进 9.4.1。

---

## §G 验证命令

```bash
# 跑通（真实双 LLM，key 经 env）
DEEPSEEK_API_KEY=<env> DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test multi_model -- --nocapture --test-threads=1

# 无 key 时 skip 不 fail
cargo test -p arf-e2e --test multi_model -- --test-threads=1
# 期望：5 tests skip（pass） + 打印 [skip] DEEPSEEK_API_KEY / DASHSCOPE_API_KEY not set

# resolve_model cross-check
sed -n '253,275p' crates/arf-engine/src/registry.rs

# harness changes
grep -n 'with_extra_providers\|model_provider\|_extra_model_nodes' \
  crates/arf-e2e/tests/common/harness.rs

# §4 信号 cross-check（multi-model 路径零新散落）
grep -rn '"model"\|"provider"' crates/arf-engine/src/registry.rs | grep -v test
grep -n 'correlation_id' crates/arf-engine/src/registry.rs

# 凭据安全（必跑）
git grep -n 'sk-' -- crates/ docs/
git grep -n 'ce9ddd9\|ab948' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— commit 前必跑 ✅
2. **granular commit**（per CLAUDE.md workflow）：
   - commit `harness.rs` 改动（with_extra_providers + model_provider builder 方法）
   - commit `provider.rs` 改动（live_deepseek factory）
   - commit `multi_model.rs`（probe，5 test cases 真实双 LLM）
   - commit `audit-probe-9.2.5.md`（结果）
3. push 双 remote（github + gitee）
4. 进 9.4.1（ModelAdapterPoolNode facade）

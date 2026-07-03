# audit-probe-9.6.2：skill_load_on_demand（`use_skill` 协议）端到端探查

> Task 9.6.2 探查产出 — **Framework 是否提供 `use_skill` 协议，端到端让 app 显式触发 L2 body+resources 加载？**
> 父 task doc：`docs/v1.x/phase9/task-9.6.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.1（L1 list 端到端 OK）
> **本 task 探查：McpNode::dispatch("use_skill") + bus send/receive + 未知 skill error path + L1→L2 progressive 边界**

---

## §A 探查环境

- working tree：HEAD `e9f0365`（task 9.6.1）+ uncommitted `crates/arf-e2e/tests/skill_load_on_demand.rs`
- 测试文件：`crates/arf-e2e/tests/skill_load_on_demand.rs`（4 test cases）
- 驱动：4 mock（tmpdir + SKILL.md + tools/references/assets，bus 注册 requester node）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test skill_load_on_demand -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed`**（bus send + dispatch + 收 skill_loaded/skill_error）
- 关键运行输出：
  ```
  test use_skill_protocol_round_trip ...
  [test1] response msg_type = skill_loaded
  [test1] payload keys = Some(["body", "description", "name", "namespace", "resources"])
  [test1] body 长度 = 65 bytes
  [test1] resources keys = Some(["assets", "references", "tools"])
  [test1] use_skill → skill_loaded 端到端 OK ✓
  test use_skill_includes_resources_manifest ...
  [test2] resources.tools = ["gen"]
  [test2] resources.references = ["api.md"]
  [test2] resources.assets = ["template.txt"]
  [test2] resources manifest 端到端 OK ✓
  test use_skill_unknown_returns_error ...
  [test3] response msg_type = skill_error
  [test3] error message = skill not found: nonexistent
  [test3] 未知 skill → skill_error（不 panic）端到端 OK ✓
  test use_skill_does_not_load_body_at_scan ...
  [test4] list 阶段: skill 'lazy' 在 entry（无 body 字段）
  [test4] load_skill_body 阶段: body 长度 = 94 bytes
  [test4] use_skill 协议: body 长度 = 94 bytes
  [test4] progressive L1 → L2 端到端 OK ✓

  test result: ok. 4 passed; 0 failed
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/skill_load_on_demand.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`use_skill` 协议 round-trip

```
单元              : skill_load_on_demand × §2.3
能力等级           : D（PASS）
判定依据          : tmpdir 写 SKILL.md + McpNode::local + connect(bus)
                   注册 requester node（filter=skill_loaded/skill_error）
                   bus.send("use_skill", payload={name}) → McpNode dispatch
                   → 回 skill_loaded payload={namespace, name, description, body, resources}
file:line         : crates/arf-mcp/src/node.rs:214-227 (use_skill dispatch)
                   crates/arf-bus/src/lib.rs:200 (bus.send) + 217 (subscribe)
                   crates/arf-bus/src/connection.rs:79 (handle.send)
                   crates/arf-core/src/lib.rs:94 (Message::new)
                   ✓ 端到端 work
```

### 单元 2：response 含 resources manifest

```
单元              : skill_load_on_demand × §2.3（resources boundary）
能力等级           : D（PASS）
判定依据          : 写 SKILL.md + tools/gen/main.py + references/api.md + assets/template.txt
                   response.resources = {tools: ["gen"], references: ["api.md"], assets: ["template.txt"]}
file:line         : crates/arf-mcp/src/node.rs:222 (resources 序列化)
                   crates/arf-mcp/src/skill.rs:138-145 (load_resources → SkillResources)
                   crates/arf-mcp/src/skill.rs:237-264 (list_files / list_dirs helpers)
                   ✓ resources manifest 端到端 work
```

### 单元 3：未知 skill → skill_error 响应

```
单元              : skill_load_on_demand × §2.3（error boundary）
能力等级           : D（PASS）
判定依据          : use_skill name="nonexistent" → response msg_type=skill_error
                   payload.error = "skill not found: nonexistent"
                   端到端不 panic，**不**扩散 exception 到 bus
file:line         : crates/arf-mcp/src/node.rs:225-226 (error path)
                   ✓ 错误处理集中（McpNode dispatch 内 match，**不** send_response 失败）
```

### 单元 4：L1 → L2 progressive 边界

```
单元              : skill_list_progressive + skill_load_on_demand × §2.3
能力等级           : D（PASS）
判定依据          : FsDiscovery::scan → list_skills() 阶段不读 body（SkillEntry 字段无 body）
                   use_skill 协议触发后才调 load_skill_body + load_skill_resources
                   同一 skill body 长度 94 bytes 在两个阶段（load_skill_body / use_skill 协议）一致
file:line         : crates/arf-mcp/src/skill.rs:128-130 (list_index: 不调 load_body)
                   crates/arf-mcp/src/skill.rs:132-136 (load_body: 独立方法)
                   crates/arf-mcp/src/node.rs:216 (dispatch 调用 load_skill_body + load_skill_resources)
                   ✓ progressive 边界端到端 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `use_skill round-trip × §2.3` | **D** | bus send → McpNode dispatch → skill_loaded 响应完整 |
| `resources manifest × §2.3` | **D** | response 含 tools / references / assets 清单 |
| `error boundary × §2.3` | **D** | 未知 skill → skill_error（**不** panic） |
| `progressive L1 → L2 × §2.3` | **D** | list 阶段**不**读 body；use_skill 触发后才读 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework use_skill 协议端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `McpNode::dispatch("use_skill")` —— **D 端到端**（node.rs:214-227）
- 协议 schema：
  - request: `Message{msg_type: "use_skill", payload: {name: "skill_name"}}`
  - response success: `Message{msg_type: "skill_loaded", payload: {namespace, name, description, body, resources}}`
  - response error: `Message{msg_type: "skill_error", payload: {namespace, name, error}}`
- `McpNode::dispatch("load_skill_resource")` —— **D**（独立协议，9.6.4 探查）
- L1 list（9.6.1）与 L2 use_skill 明确分层 —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`use_skill` 仅返 `name` + `description`，**不**返 `compatibility`**（node.rs:218-222）—— 9.6.1 `compatibility` 字段是 L1 元数据的一部分，但 use_skill response 缺它。**不算** lesion（response 精简设计），但 spec 应明确 use_skill 不带 compatibility，app 需另发 use_skill 前先查 L1。
2. **`use_skill` 协议无 correlation_id**（node.rs:214-227）—— 与 tool_result / model_response 模式不同，request 端**无** cid 关联。后果：app 端发 K 个并发 use_skill 时**无法**对 response 做 request-response 匹配（依赖 response `name` 字段 + 单 in-flight 顺序）。**潜在 issue**，但**不**算 F-lesion（spec 未明示需要 cid）。
3. **`McpNode::dispatch("use_skill")` 失败时**仍发** `skill_error`**（node.rs:225-226）—— body 加载失败 vs resources 加载失败**都**返回同一 error path，无法区分。**不算** lesion（合并 error 设计），但 app 端**无法**精确判断"body 缺" vs "skill 名错"。
4. **请求端必须注册为 Bus node**—— `McpNode` 的 reply 是 directed send 给 `msg.from`，**非**注册的发送者**无法**收 response。本 task 通过 `register_requester` helper 解决。**不算** lesion（CAN bus 模型固有），但 spec 应明确"app 端必须先 `bus.connect` 才能 use_skill"。

### 探查信号命中（§4 find signals）

跑 spec §4.2 find signals：

- **A1-S1**（trait 方法多职责）：`McpNode::dispatch` 一个 match 表达式处理 4 消息类型（tool_call_set / tool_exec / use_skill / load_skill_resource / run_skill_script），但每个分支**单一职责**。**未命中**。
- **A2-S1**（cross-import 强依赖）：`McpNode` 依赖 `DiscoveryBackend` trait，**不**依赖具体 `FsDiscovery`。**未命中**。
- **A4-S2**（validate 散落）：`parse_frontmatter` 在 SkillIndex 一处，**未**散落。**未命中**。
- **A4-S4**（convert 散落）：`use_skill` payload 字段 name / body / resources 直接走 serde_json::json!，**无** 强类型 envelope。**未命中**（JSON 字段名直接走 wire）。

---

## §E 探查回归

- 9.6.1 既有 4 test pass（L1 list 端到端）
- 9.6.2 新增 4 test pass（use_skill 协议端到端）
- 综合：9.6 = 8 test，**全 pass**，0 新 F-lesion
- 与既有 lesion（F-001~F-009）**无关**——本 task 探查 skill on-demand 加载协议

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| use_skill round-trip | ✓ test1 pass（payload 5 字段：namespace / name / description / body / resources） |
| resources manifest | ✓ test2 pass（tools / references / assets 各 ≥ 1） |
| 未知 skill error path | ✓ test3 pass（skill_error 响应，**不** panic） |
| L1 vs L2 progressive | ✓ test4 pass（list 阶段无 body 字段；use_skill 触发后才读 body） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.6.2 探查显示 framework **use_skill 协议** 端到端 work——app 通过 `bus.send` 发 use_skill 消息即可触发 L2 body+resources 加载，与 9.6.1 L1 list 明确分层，未知 skill 返回 skill_error 而非 panic。这是 phase 9 首次在 skill L2 protocol 探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/skill_load_on_demand.rs`（~310 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.6.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit

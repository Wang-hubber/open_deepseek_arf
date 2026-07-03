# audit-probe-9.6.5：Skill 全套联合（4 项联动）端到端探查

> Task 9.6.5 探查产出 — **Framework 是否能让 app 端到端走完"list → use_skill → run_skill_script → load_skill_resource"完整 progressive 链？**
> 父 task doc：`docs/v1.x/phase9/task-9.6.5.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.1（L1 list 端到端 OK）
> **本 task 探查：4 步链端到端 + 状态一致 + 4 协议并发 + 大 body scalability**

---

## §A 探查环境

- working tree：HEAD `07ba350`（task 9.6.4）+ uncommitted `crates/arf-e2e/tests/skill_full_progressive.rs`
- 测试文件：`crates/arf-e2e/tests/skill_full_progressive.rs`（4 test cases）
- 驱动：4 mock（tmpdir + 完整 skill：SKILL.md + tools + references + assets）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test skill_full_progressive -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed`**（4 步链 + 状态一致 + 4 协议并发 + 大 body）
- 关键运行输出：
  ```
  test concurrent_four_protocols_on_same_mcp ...
  [test3] 4 协议 results:
  [test3]   use_skill → skill_loaded
  [test3]   run_skill_script → skill_script_result
  [test3]   load_skill_resource → skill_resource_loaded
  [test3]   load_skill_resource → skill_resource_loaded
  [test3] 4 协议 round-trip 全 success OK ✓
  test full_progressive_chain_end_to_end ...
  [test1] L1 list: 1 skill, name=chain-skill, no body ✓
  [test1] L2 use_skill: body 705 bytes, resources.tools = ["echo"]
  [test1] L3 run_skill_script: status=success, name="chain-skill/echo"
  [test1] L4 load_skill_resource: content.len = 40 bytes ✓
  [test1] 完整 progressive 链端到端 OK ✓
  test large_skill_full_chain ...
  [test4] L2 大 body 长度 = 12775 bytes
  [test4] L4 tools/ 路径大文件 + tool metadata 端到端 OK ✓
  test progressive_state_consistency ...
  [test2] L1 list 与 advertised 名字/描述一致 ✓
  [test2] L2 use_skill 与 L1 / direct body 长度一致 ✓
  [test2] L3 scoped name = consist-skill/echo ✓

  test result: ok. 4 passed; 0 failed
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/skill_full_progressive.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：完整 progressive 链端到端

```
单元              : skill_list_progressive + skill_load_on_demand +
                   skill_tool_progressive_register + skill_resource_load × §2.3
能力等级           : D（PASS，4 capability 联动端到端）
判定依据          : 同一 McpNode + 同一 requester 走完 4 步链：
                   L1 FsDiscovery::list_skills() → 1 skill
                   L2 use_skill → body 705 bytes + resources.tools=["echo"]
                   L3 run_skill_script → status=success, name="chain-skill/echo"
                   L4 load_skill_resource references/api.md → content 40 bytes
file:line         : crates/arf-mcp/src/discovery.rs:153-170 (4 skill methods)
                   crates/arf-mcp/src/node.rs:214-259 (3 protocols)
                   ✓ 4 capability 联动端到端 work
```

### 单元 2：progressive 状态一致性

```
单元              : skill_list_progressive + skill_load_on_demand × §2.3
能力等级           : D（PASS）
判定依据          : 4 步中**同一 skill** 的 name / description 一致：
                   L1 list (SkillEntry.name/description) = advertised = use_skill response.name/description
                   use_skill response body.len == load_skill_body().len()（L2 ↔ direct 路径一致）
                   run_skill_script response.name = "consist-skill/echo"（scoped 命名空间一致）
file:line         : crates/arf-mcp/src/skill.rs:108-117 (SkillEntry 字段 = use_skill response 字段)
                   crates/arf-mcp/src/skill.rs:132-136 (load_body 复用)
                   ✓ 4 步状态一致
```

### 单元 3：4 协议 round-trip 全 success

```
单元              : 4 protocols × §2.3 (concurrent boundary)
能力等级           : D（PASS）
判定依据          : 同一 McpNode + 同一 requester 顺序发 4 协议：
                   use_skill → skill_loaded
                   run_skill_script → skill_script_result
                   load_skill_resource (references/api.md) → skill_resource_loaded
                   load_skill_resource (assets/template.txt) → skill_resource_loaded
                   4 协议全部 success，无 error 污染
file:line         : crates/arf-mcp/src/node.rs:131-263 (dispatch 4 分支)
                   crates/arf-bus/src/lib.rs:200-211 (bus.send 单线)
                   ✓ 4 协议并发 round-trip work
```

### 单元 4：大 body scalability

```
单元              : 4 protocols × §2.3 (scalability)
能力等级           : D（PASS）
判定依据          : 200 行 body (~12.7KB) + 完整 resources
                   L2 use_skill body 长度 12775 bytes — 大 body 端到端传输 OK
                   L4 load_skill_resource tools/echo/main.py → tool metadata 仍正确
file:line         : crates/arf-mcp/src/skill.rs:132-136 (load_body: 单 String，无分块)
                   crates/arf-bus broadcast channel capacity 128
                   ✓ 大 body 端到端 work（无 truncation / no error）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `4 capability 联动 × §2.3` | **D** | list → use_skill → run_skill_script → load_skill_resource 完整链端到端 |
| `状态一致 × §2.3` | **D** | 4 步中同一 skill 元数据一致 |
| `4 协议并发 × §2.3` | **D** | 4 协议顺序发全部 success |
| `大 body scalability × §2.3` | **D** | ~12.7KB body + 多 resources 端到端 work |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework 4 capability 联动端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- L1 list `FsDiscovery::list_skills()` —— **D**
- L2 use_skill 协议（`McpNode::dispatch("use_skill")`）—— **D**
- L3 run_skill_script 协议（`McpNode::dispatch("run_skill_script")`）—— **D**
- L4 load_skill_resource 协议（`McpNode::dispatch("load_skill_resource")`）—— **D**
- 4 步链同一 McpNode + 同一 SkillIndex 状态一致 —— **D**
- 大 body (~12.7KB) 端到端传输无 truncation —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **4 协议都无 correlation_id**（node.rs:214, 229, 243）—— 9.6.2/9.6.3/9.6.4 探查各自标记的"无 cid" 在 9.6.5 联动场景**放大**：app 端**实际**走完 4 步，**每一**步都用单 in-flight 顺序保证（requester register 一次 recv 后**唯一**匹配），**没有**跨协议**也**跨请求的并发模型。后果：app 端发"先 use_skill(A) 再 use_skill(B)"必须**等** A response 才能发 B，**无法** pipelining。**潜在 issue**（4 协议共享根因），但**不**算 F-lesion（spec 未明示需要 cid）；建议 fix phase 引入 typed correlation envelope（A4-001 修复方向涵盖此）。
2. **大 body 端到端 OK 但**无**分块流**（skill.rs:132-136）—— `load_skill_body` 直接 `fs::read_to_string` 全 body 一次读入 + 一次性走 bus。后果：10MB+ body 会 OOM bus broadcast channel。**不算** lesion（典型 skill body < 100KB），但 spec 应明示"skill body 推荐 < 1MB，> 1MB 需分块流"。
3. **4 协议 filter 必须**显式列出**所有期望类型**（test helper `register_multi_requester`）—— `MessageFilter.types: Some(vec![...])` 限定 5 个 response 类型。后果：app 端**新增** 4 capability 之外的协议时**必须**记得更新 filter，**否则**响应**收不到**（filter 不 match）。**不算** lesion（filter 设计选择），但 spec 应明示"app 端 filter 必含所有期望 msg_type"。
4. **L1 advertised 与 L2 use_skill 的 description 字段冗余**（discovery.rs + node.rs:222）—— advertised 仅 `{name, description}`，use_skill response 也含 `{name, description}`。后果：app 端从 advertised 拿到的 description 与 use_skill response.description **完全**一致（**不**带 compatibility 字段）。**不算** lesion（设计简化），但 spec 应明示"L1 advertised 与 L2 use_skill response 共享 description 字段，**不**含 compatibility"。

### 探查信号命中（§4 find signals）

跑 spec §4.2 find signals：

- **A1-S1**（trait 方法多职责）：`McpNode::dispatch` 一个 match 表达式处理 4 消息类型 + 2 tool 协议，**5+ 分支**。**潜在命中**（多职责），但每个分支**单一职责**（5 协议 + 1 错误路径）。**不**算 lesion（match 模式是 idiomatic Rust）。
- **A2-S1**（cross-import 强依赖）：`McpNode` 依赖 `DiscoveryBackend` trait，**不**依赖具体 `FsDiscovery` / `HttpDiscovery`。**未命中**。
- **A3-S3**（同名 struct 跨 crate）：`McpNode` 仅在 `arf-mcp::node` 一处定义，**未** 跨 crate。**未命中**。
- **A4-S4**（convert 散落）：4 协议各自序列化 payload 用 `serde_json::json!` 宏，**无** 强类型 envelope。**未命中**（JSON 字段名直接走 wire，与既有协议一致）。

---

## §E 探查回归

- 9.6.1-9.6.4 既有 16 test pass（4 capability 单元端到端）
- 9.6.5 新增 4 test pass（联动 + 状态一致 + 并发 + scalability）
- 综合：9.6 = 20 test，**全 pass**，0 新 F-lesion
- 与既有 lesion（F-001~F-009）**无关**——本 task 探查 4 capability 联动，无新 F-lesion
- **注**：9.6.2/9.6.3/9.6.4 各 task 标"无 correlation_id"潜在 issue，9.6.5 联动场景**放大**，建议 fix phase 集中处理（A4-001 修复方向涵盖此）

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 4 步链端到端 | ✓ test1 pass（L1 list → L2 use_skill → L3 run_skill_script → L4 load_skill_resource） |
| 4 步状态一致 | ✓ test2 pass（name / description / body.len / scoped name 一致） |
| 4 协议并发 | ✓ test3 pass（4 协议顺序发全 success） |
| 大 body scalability | ✓ test4 pass（~12.7KB body 端到端 OK） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.6.5 探查显示 framework **4 skill capability 联动** 端到端 work——app 通过 `FsDiscovery::scan` + 3 bus 协议（use_skill / run_skill_script / load_skill_resource）即可走完完整 progressive 链；4 步状态一致（name / description / body / resources）；大 body 端到端无 truncation。**这是 phase 9 首次在 skill 完整链 + 4 capability 联动场景无 F-lesion 的 task**。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/skill_full_progressive.rs`（~410 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.6.5.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 9.6 5 task 全部完成，待 commit

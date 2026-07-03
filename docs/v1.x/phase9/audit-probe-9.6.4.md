# audit-probe-9.6.4：skill_resource_load（`load_skill_resource` + `LoadedResource`）端到端探查

> Task 9.6.4 探查产出 — **Framework 是否提供 load_skill_resource 协议 + LoadedResource 类型，让 app 按需加载 skill 内部单文件？**
> 父 task doc：`docs/v1.x/phase9/task-9.6.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（FsDiscovery 端到端 OK）
> **本 task 探查：3 类 path prefix（tools/ / references/ / assets/）+ LoadedResource 自动附加 tool metadata + path traversal 拒绝 + 协议 round-trip**

---

## §A 探查环境

- working tree：HEAD `c557f28`（task 9.6.3）+ uncommitted `crates/arf-e2e/tests/skill_resource_load.rs`
- 测试文件：`crates/arf-e2e/tests/skill_resource_load.rs`（4 test cases）
- 驱动：4 mock（tmpdir + skills/{name}/references/api.md + assets/template.txt + tools/gen/main.py + tool.toml）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test skill_resource_load -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed`**（path validation + read + tool metadata + 协议）
- 关键运行输出：
  ```
  test load_resource_file_rejects_path_traversal ...
  [test3] references/../../etc/passwd = Some("path traversal rejected: '..' not allowed")
  [test3] /etc/passwd = Some("absolute path rejected")
  [test3] config.toml = Some("resource path must start with tools/, references/, or assets/. Got: config.toml")
  [test3] path traversal 全部拒绝 端到端 OK ✓
  test load_resource_file_three_path_prefixes ...
  [test1] references/api.md: content.len = 34, desc=None
  [test1] assets/template.txt: content="TEMPLATE CONTENT", desc=None
  [test1] tools/gen/main.py: content.len = 49, desc=Some("Generate output")
  [test1] 3 类 path prefix 端到端 OK ✓
  test load_skill_resource_protocol_end_to_end ...
  [test4] references/api.md → msg_type = skill_resource_loaded
  [test4] tools/gen/main.py → msg_type = skill_resource_loaded
  [test4] traversal → msg_type = skill_resource_error
  [test4] load_skill_resource 协议 round-trip 端到端 OK ✓
  test loaded_resource_attaches_tool_metadata_for_tools_path ...
  [test2] description = Some("Generate output")
  [test2] params_schema = {"properties":{"prompt":{"type":"string"}},"required":["prompt"],"type":"object"}
  [test2] tools/ 路径自动附加 tool metadata 端到端 OK ✓

  test result: ok. 4 passed; 0 failed
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/skill_resource_load.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：3 类 path prefix 加载

```
单元              : skill_resource_load × §2.3
能力等级           : D（PASS）
判定依据          : tmpdir + skill + references/api.md + assets/template.txt + tools/gen/main.py
                   load_resource_file 三类 path 各自返 LoadedResource
                   references / assets 的 description/params_schema 为 None
                   tools/ 路径附 description（来自 tool.toml）
file:line         : crates/arf-mcp/src/skill.rs:147-179 (load_resource_file)
                   crates/arf-mcp/src/skill.rs:300-327 (resolve_safe_path)
                   ✓ 3 类 path prefix 端到端 work
```

### 单元 2：tools/ 路径自动附加 tool metadata

```
单元              : skill_resource_load × §2.3（tool metadata boundary）
能力等级           : D（PASS）
判定依据          : tools/gen/main.py + tool.toml[params_schema] 完整
                   LoadedResource.description = "Generate output"
                   LoadedResource.params_schema = {"type":"object","required":["prompt"],"properties":{...}}
file:line         : crates/arf-mcp/src/skill.rs:158-172 (tools/ 路径下附加 description + params_schema)
                   crates/arf-mcp/src/skill.rs:267-271 (load_tool_config_from_dir)
                   ✓ tool metadata 自动附加 work
```

### 单元 3：path traversal 拒绝

```
单元              : skill_resource_load × §2.3（security boundary）
能力等级           : D（PASS）
判定依据          : 3 种非法 path 全部被拒绝：
                   1. "references/../../etc/passwd" → "path traversal rejected: '..' not allowed"
                   2. "/etc/passwd" → "absolute path rejected"
                   3. "config.toml" → "resource path must start with tools/, references/, or assets/"
file:line         : crates/arf-mcp/src/skill.rs:300-327 (resolve_safe_path)
                   crates/arf-mcp/src/skill.rs:301-306 (拒绝 `..` 和 `/` 开头)
                   crates/arf-mcp/src/skill.rs:308-316 (拒绝非 3 prefix 开头)
                   crates/arf-mcp/src/skill.rs:318-326 (canonicalize 后再次验证 in source_dir)
                   ✓ 三层 path validation 端到端 work
```

### 单元 4：`load_skill_resource` 协议 round-trip

```
单元              : skill_resource_load × §2.3（protocol boundary）
能力等级           : D（PASS）
判定依据          : bus + McpNode + register requester + send load_skill_resource
                   references/api.md → skill_resource_loaded（含 content, description, params_schema）
                   tools/gen/main.py → skill_resource_loaded（description 字段 = "Generate output"）
                   ../../etc/passwd → skill_resource_error（error 字段含 "traversal"）
file:line         : crates/arf-mcp/src/node.rs:229-241 (load_skill_resource dispatch)
                   crates/arf-bus/src/lib.rs:200 (bus.send)
                   ✓ 协议 round-trip 端到端 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `3 类 path prefix × §2.3` | **D** | references / assets / tools 各自端到端加载 |
| `tool metadata × §2.3` | **D** | tools/ 路径下 description + params_schema 自动附加 |
| `path traversal × §2.3` | **D** | `..` / `/` / 非允许 prefix 三层拒绝 |
| `load_skill_resource 协议 × §2.3` | **D** | success + error 双向 round-trip work |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework skill_resource_load 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `SkillIndex::load_resource_file(skill, path)` —— **D 端到端**（skill.rs:147-179）
- `LoadedResource { content, description, params_schema }` —— **D**（skill.rs:48-55）
- 3 类 path prefix 限制（`tools/`, `references/`, `assets/`）—— **D**
- path traversal 三层防护（`..` 拒绝 + `/` 拒绝 + 非 prefix 拒绝 + canonicalize 验证）—— **D**
- `McpNode::dispatch("load_skill_resource")` —— **D**（node.rs:229-241）

### 注意事项（潜在 issue，非 lesion）

1. **`LoadedResource.description` 在 non-tools/ 路径下为 None**（skill.rs:170-172）—— `references/`, `assets/` 文件**无**自动 description 字段。后果：app 端发 load_skill_resource(references/...) 拿不到 file-level 描述。**不算** lesion（设计：只有 tools/ 路径有 tool.toml 元数据来源），但 spec 应明确 references/assets 单文件**无** metadata 字段（仅 content）。
2. **Path validation 三层 + canonicalize 二次验证**（skill.rs:300-327）—— 安全设计正确，但**无** `McpNode::build_node_info` advertised 阶段**也**做 path 限制（仅 build_node_info 不含 path）。**不算** lesion（advertised 阶段无 path），但 app 端**直接**调 `SkillIndex::load_resource_file` 时需自己保证 path 来自 trusted source。
3. **`load_skill_resource` 协议无 correlation_id**（node.rs:229-241）—— 与 9.6.2 use_skill + 9.6.3 run_skill_script **同**类（**无** cid）。**潜在 issue**（3 个协议同 bug），但**不**算 F-lesion（spec 未明示需要 cid）。
4. **`error` 字段无统一 schema** —— 9.6.2 use_skill error 是 `"skill not found: {name}"`，9.6.3 run_skill_script error 是 `format!("{e}")`（来自 ScriptTool），9.6.4 load_skill_resource error 又是 path validation 错误。**3 协议 error 字符串 schema 不统一**。**不算** lesion（**无** 显式 schema 契约），但 spec 应明确 error 字段语义（如 `error_code: "SKILL_NOT_FOUND" | "TOOL_EXEC_ERROR" | "PATH_INVALID"`），app 端才能用结构化 error。
5. **Path traversal 拒绝对** `..` **string 检测**（skill.rs:301）—— `references/foo..bar/api.md`（含 `..` 但**不**是 path traversal）**也**会被拒。后果：合法文件名含 `..`（罕见但合法）**被误判**。**不算** lesion（保守安全策略），但 spec 应明示 "resource_path **不**允许含 `..` 子串"。

### 探查信号命中（§4 find signals）

跑 spec §4.2 find signals：

- **A1-S1**（trait 方法多职责）：`load_resource_file` 一个方法做（path validate + canonicalize + read + tool metadata attach），**4 步串联**。**潜在命中**（多职责），但每步是**线性数据流**（无控制分支），且**单测试任务**（"按 path 加载 skill 内部文件"）。**不**算 lesion（设计内聚），spec 应明示 `load_resource_file` 是"skill 内部单文件加载"高阶 API。
- **A2-S2**（字段 cross-reference）：`LoadedResource` 字段是纯 owned data（String + Option<String> + Option<Value>），**无** 跨 crate 类型引用。**未命中**。
- **A4-S1**（filter 散落）：`resolve_safe_path` 是单一接缝，**无** filter 动词散落。**未命中**。
- **A4-S2**（validate 散落）：path validation 全部在 `resolve_safe_path` 单一函数，**无** validate 散落。**未命中**。

---

## §E 探查回归

- 9.6.1-9.6.3 既有 12 test pass（L1 list + use_skill + skill tool execution）
- 9.6.4 新增 4 test pass（resource 单文件加载 + 协议）
- 综合：9.6 = 16 test，**全 pass**，0 新 F-lesion
- 与既有 lesion（F-001~F-009）**无关**——本 task 探查 skill 内部单文件加载 + 协议

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 3 类 path prefix 加载 | ✓ test1 pass（references / assets / tools 各自端到端） |
| tool metadata 自动附加 | ✓ test2 pass（tools/ 路径 description + params_schema 都有） |
| path traversal 拒绝 | ✓ test3 pass（3 类非法 path 全部 Err） |
| load_skill_resource 协议 | ✓ test4 pass（success + error 双向） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.6.4 探查显示 framework **skill_resource_load** 端到端 work——app 通过 `DiscoveryBackend::load_resource_file` 或 bus `load_skill_resource` 协议即可按需加载 skill 内部单文件（references / assets / tools），3 层 path validation 安全。这是 phase 9 首次在 skill 单文件加载 + 协议探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/skill_resource_load.rs`（~340 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.6.4.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit

# audit-probe-9.6.3：skill_tool_progressive_register（skill body → tool）端到端探查

> Task 9.6.3 探查产出 — **Framework 是否让 app 通过 skill 内部 tool（如 tools/gen/main.py）被调用并执行？**
> 父 task doc：`docs/v1.x/phase9/task-9.6.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.2（use_skill 协议端到端 OK）
> **本 task 探查：SkillIndex::run_tool + run_skill_script 协议 + scoped 命名空间 + auto-infer 缺 tool.toml**

---

## §A 探查环境

- working tree：HEAD `1eaa66f`（task 9.6.2）+ uncommitted `crates/arf-e2e/tests/skill_tool_progressive.rs`
- 测试文件：`crates/arf-e2e/tests/skill_tool_progressive.rs`（4 test cases）
- 驱动：4 mock（tmpdir + skills/{name}/tools/{tool}/main.py + 1 协议层 + 1 auto-infer 验证）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test skill_tool_progressive -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed`**（ScriptTool execute + 协议层 + infer）
- 关键运行输出：
  ```
  test run_skill_script_protocol_end_to_end ...
  [test4] response payload = {"call_id":"...","error":null,"name":"math/add","result":{"result":42},"session_id":"...","status":"success"}
  [test4] run_skill_script 协议端到端 OK ✓
  test skill_tool_auto_infer_without_tool_toml ...
  [test2] load_tool_config（无 tool.toml） = None
  [test2] run result = {"inferred":true,"input":{"x":7}}
  [test2] infer_tool_defaults 自动推断（仅 run_tool 路径）+ 实际执行 端到端 OK ✓
  test skill_tool_execute_via_run_skill_tool ...
  [test1] run_skill_tool result = {"doubled":42,"echoed":"hello"}
  [test1] SkillIndex::run_tool 调起 main.py 端到端 OK ✓
  test skill_tool_scoped_name ...
  [test3] raw tool_config.name = compute
  [test3] skill_script_result.name = scoped/compute
  [test3] skill tool scoped 命名空间隔离 端到端 OK ✓

  test result: ok. 4 passed; 0 failed; 0 ignored
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/skill_tool_progressive.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`SkillIndex::run_tool` 端到端执行

```
单元              : skill_tool_progressive_register × §2.3
能力等级           : D（PASS）
判定依据          : skill/tools/echo/main.py + tool.toml → run_skill_tool
                   实际 spawn python + 读 stdin + 写 stdout + JSON 解析
                   返 {"doubled":42, "echoed":"hello"} — 业务逻辑端到端 work
file:line         : crates/arf-mcp/src/skill.rs:188-217 (run_tool)
                   crates/arf-mcp/src/skill.rs:267-271 (load_tool_config_from_dir)
                   crates/arf-mcp/src/script.rs ScriptTool::new + execute
                   ✓ skill tool 端到端执行 OK
```

### 单元 2：`infer_tool_defaults` 自动推断（缺 tool.toml）

```
单元              : skill_tool_progressive_register × §2.3（auto-infer boundary）
能力等级           : D（PASS，**有 design observation**）
判定依据          : 缺 tool.toml 时：
                   - load_tool_config() 返 None（**不**推断）
                   - run_skill_tool() 调 infer_tool_defaults → 推断 runtime=python / entrypoint=main.py
                   - 实际 main.py 执行 OK
file:line         : crates/arf-mcp/src/skill.rs:275-298 (infer_tool_defaults)
                   crates/arf-mcp/src/skill.rs:204-205 (run_tool 调 infer as fallback)
                   ✓ 推断仅在 run_tool 路径；load_tool_config 是**显式** load 语义
```

### 单元 3：scoped 命名空间隔离

```
单元              : skill_tool_progressive_register × §2.3（naming boundary）
能力等级           : D（PASS）
判定依据          : tool.toml 写 name="compute"，load_tool_config 返 "compute"（**未**改写）
                   run_skill_script 协议 response name 字段 = "scoped/compute"（已改写）
                   与根 tools/*/tool.toml 命名空间隔离
file:line         : crates/arf-mcp/src/skill.rs:209 (config.name = format!("{skill_name}/{tool_name}"))
                   crates/arf-mcp/src/node.rs:257 (response name 字段)
                   ✓ scoped 命名空间端到端 work
```

### 单元 4：`run_skill_script` 协议 round-trip

```
单元              : skill_tool_progressive_register × §2.3（protocol boundary）
能力等级           : D（PASS）
判定依据          : bus + McpNode + register requester + send run_skill_script
                   收 skill_script_result：status=success, result={"result":42}, name=math/add
                   payload schema：{skill_name, tool_name, call_id, session_id, params}
                   response schema：{call_id, session_id, name, status, result, error}
file:line         : crates/arf-mcp/src/node.rs:243-259 (run_skill_script dispatch)
                   crates/arf-bus/src/lib.rs:200 (bus.send)
                   ✓ 协议端到端 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `SkillIndex::run_tool × §2.3` | **D** | skill main.py 端到端执行 OK |
| `infer_tool_defaults × §2.3` | **D** | 缺 tool.toml 时 auto-infer（仅 run_tool 路径） |
| `scoped name × §2.3` | **D** | `{skill_name}/{tool_name}` 命名空间隔离 |
| `run_skill_script 协议 × §2.3` | **D** | bus send → skill_script_result 端到端 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework skill tool execution 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `SkillIndex::run_tool(skill, tool, params)` —— **D 端到端**（skill.rs:188-217）
- `infer_tool_defaults` 自动推断 —— **D**（skill.rs:275-298，仅在 run_tool 路径）
- `McpNode::dispatch("run_skill_script")` —— **D**（node.rs:243-259）
- Scoped 命名空间 `{skill_name}/{tool_name}` —— **D**（skill.rs:209）

### 注意事项（潜在 issue，非 lesion）

1. **`load_tool_config` 在缺 tool.toml 时返 None，`run_skill_tool` 却能执行（通过 infer）—— 行为不对称**（skill.rs:182-185 vs 188-217）—— `load_tool_config` 是"显式 load 缺文件就 None"语义，`run_skill_tool` 是"尝试 load + fallback infer"语义。后果：app 端**用 load_tool_config 探测 tool 是否存在**会**误判**（无 tool.toml 但 main.py 在的 tool 实际可执行）。**不算** lesion（语义不同），但 spec 应明确两者语义差异，避免 app 误用。
2. **Scoped name 与 advertised 形态**：`run_skill_script` response `name` 字段是 `{skill_name}/{tool_name}`，但 `McpNode::build_node_info` advertised 的 skills 仅含 `{name, description}`（**无** scoped tool 列表）。后果：app 端从 `NodeInfo.capabilities.skills` **无法**知道 skill 内部有哪些 tool。**不算** lesion（设计选择，skill body 才是 tool 入口），但 spec 应明确 advertised 不暴露 skill 内部 tool 列表。
3. **Auto-infer 仅识别 `main.py` / `main.sh` / `main`**（skill.rs:276）—— 不识别 `run.py` / `index.js` 等命名约定。**不算** lesion（最简默认），但 spec 应明示"无 tool.toml 时只支持 main.{py,sh}"。
4. **`run_skill_script` 协议无 `correlation_id`**（node.rs:243-259）—— 与 9.6.2 use_skill 同样的**无** cid 模式，app 端并发 K 个 run_skill_script 时**无法**对 response 做 request-response 匹配。**潜在 issue**（与 9.6.2 关注点 2 同类），但**不**算 F-lesion（spec 未明示需要 cid）。

### 探查信号命中（§4 find signals）

跑 spec §4.2 find signals：

- **A1-S1**（trait 方法多职责）：`SkillIndex::run_tool` 一个方法做（load config / infer / rewrite name / execute / collect result），**5 步串联**。**潜在命中**（多职责），但每步是**线性数据流**（无控制分支），且**单测试任务**（"调起 skill 内部 tool"）。**不**算 lesion（设计内聚），spec 应明示 `run_tool` 是"skill tool 执行"高阶 API。
- **A2-S2**（字段 cross-reference）：`ScriptTool::new(config, tool_dir)` 把 `ToolConfig` + `PathBuf` 一并传入；`ScriptTool` 内部**无** 跨 crate 类型引用。**未命中**。
- **A3-S1**（同名字段跨 crate）：`ToolConfig.name` 跨 `arf-mcp::config` + `arf-mcp::skill` 一致使用。**未命中**。
- **A4-S1**（filter 散落）：`run_tool` 内部**无** filter 动词。**未命中**。

---

## §E 探查回归

- 9.6.1-9.6.2 既有 8 test pass（L1 list + use_skill 协议）
- 9.6.3 新增 4 test pass（skill tool execution）
- 综合：9.6 = 12 test，**全 pass**，0 新 F-lesion
- 与既有 lesion（F-001~F-009）**无关**——本 task 探查 skill 内部 tool 执行

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| SkillIndex::run_tool 端到端 | ✓ test1 pass（实际 main.py 执行，返 {doubled:42}） |
| Auto-infer 缺 tool.toml | ✓ test2 pass（load_tool_config=None, run_tool 推断 runtime=python） |
| Scoped 命名 | ✓ test3 pass（response name=scoped/compute） |
| run_skill_script 协议 | ✓ test4 pass（status=success, name=math/add, result=42） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.6.3 探查显示 framework **skill tool execution** 端到端 work——app 通过 `DiscoveryBackend::run_skill_tool` 或 bus `run_skill_script` 协议即可调起 skill 内部 tool，scoped 命名空间隔离与根 tools/* 不冲突，缺 tool.toml 时 auto-infer 推断。这是 phase 9 首次在 skill 内部 tool execution 探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/skill_tool_progressive.rs`（~340 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.6.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit

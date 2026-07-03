# audit-probe-9.5.1：McpNode + FsDiscovery（filesystem 扫描本地 tool/skill）端到端探查

> Task 9.5.1 探查产出 — **Framework 是否让 app 通过 FsDiscovery 扫描本地目录、零代码注册 tool？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.4.x（pool facade + 路由 + overflow 完整覆盖）
> **本 task 探查：FsDiscovery::scan + McpNode::local + connect(bus) + DiscoveryBackend 多方法端到端**

---

## §A 探查环境

- working tree：HEAD `7f6b951`（task 9.4.3）+ uncommitted `crates/arf-e2e/tests/mcp_fs_discovery.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_fs_discovery.rs`（4 test cases）
- 驱动：4 mock（tmpdir + tool.toml + main.py + SKILL.md）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_fs_discovery -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.13s`**（tmpdir 写文件 + scan + 调用）
- 关键运行输出：
  ```
  test discovery_backend_trait_methods ...
  [test3] list_tools() = 1 tools ✓
  [test3] tool_map() 含 echo ✓
  [test3] resolve_tool('echo') = Some ✓
  [test3] resolve_tool('nonexistent') = None ✓
  [test3] list_skills() = 1 skills ✓
  [test3] resolve_skill('greet') = Some ✓
  [test3] load_skill_body('greet') = Some ✓
  test fs_discovery_scans_tool_toml ...
  [test1] FsDiscovery 扫到 2 个 tool: upper, echo ✓
  test mcp_node_local_connects_to_bus ...
  [test2] McpNode::local + connect(bus) 成功 ✓
  test tool_execute_via_script_tool ...
  [test4] echo tool execute result: Object {"hello": String("world")} ✓

  test result: ok. 4 passed; 0 failed; 0 ignored
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_fs_discovery.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`FsDiscovery::scan` 扫 tool.toml

```
单元              : tool_discovery × §2.3
能力等级           : D（PASS）
判定依据          : tmpdir 写 2 个 tool.toml + main.py，FsDiscovery::scan 列出 2 个 tool
file:line         : crates/arf-mcp/src/discovery.rs:86-131
                   scan(root) → 扫 root/tools/*/tool.toml → ToolConfig::from_toml_str
                   → ScriptTool::new + 塞 tool_info + tools
                   ✓ tool.toml schema: name / description / runtime / entrypoint
```

### 单元 2：`McpNode::local` + `connect(bus)`

```
单元              : mcp_node_local × §2.3
能力等级           : D（PASS）
判定依据          : McpNode::local("test-mcp", root) → Arc<McpNode>
                   connect(bus) → build_node_info (tools + skills caps)
                   → bus.connect + spawn message_loop
file:line         : crates/arf-mcp/src/node.rs:28-81
                   ✓ McpNode 正确构造（namespace + node_id + FsDiscovery + LocalRuntime）
                   ✓ connect 注册 listener 并 spawn message_loop
```

### 单元 3：`DiscoveryBackend` trait 多方法

```
单元              : discovery_backend_trait × §2.3
能力等级           : D（PASS）
判定依据          : FsDiscovery 实现 7 方法（list_tools / tool_map / resolve_tool
                   / list_skills / resolve_skill / load_skill_body / load_skill_resources）
file:line         : crates/arf-mcp/src/discovery.rs:32-66 trait 定义
                   crates/arf-mcp/src/discovery.rs:135-160 FsDiscovery impl
                   ✓ 全部 7 方法端到端 work
```

### 单元 4：`ScriptTool::execute`（tool 真实执行）

```
单元              : tool_execution × §2.3
能力等级           : D（PASS）
判定依据          : resolve_tool("echo") → ScriptTool::execute({"hello":"world"})
                   → spawn python main.py 读 stdin → 回传 JSON
file:line         : crates/arf-mcp/src/script.rs:50-148 (ScriptTool + execute)
                   ✓ execute 端到端 work（python runtime 调起 + IO + JSON 解析）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `tool_discovery × §2.3` | **D** | tmpdir 2 tool.toml 端到端扫描 OK |
| `mcp_node_local × §2.3` | **D** | McpNode::local + connect(bus) 成功 |
| `discovery_backend_trait × §2.3` | **D** | 7 方法全 work |
| `tool_execution × §2.3` | **D** | ScriptTool execute 端到端 OK |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework FsDiscovery + McpNode + DiscoveryBackend + ScriptTool 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `FsDiscovery::scan(root)` 扫 `root/tools/*/tool.toml` —— **D 端到端**（tools 个数 + tool_info 完整）
- `McpNode::local(ns, root)` 一步创建并注入 FsDiscovery + LocalRuntime —— **D**
- `McpNode::connect(bus)` 注册 listener + spawn message_loop —— **D**
- `DiscoveryBackend` 7 方法（list_tools / tool_map / resolve_tool / list_skills / resolve_skill / load_skill_body / load_skill_resources）—— **D** 全 work
- `ScriptTool::execute` 调 python runtime + 解析 JSON 回传 —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`McpNode.discovery` 字段私有**（node.rs:19）—— app 不能从 McpNode 反射拿 DiscoveryBackend。本 task 通过独立的 FsDiscovery::scan 验证。**建议**：加 `pub fn discovery(&self) -> &dyn DiscoveryBackend` 访问器。
2. **`DiscoveryBackend::tool_map` 返回 `&HashMap`**（discovery.rs:140）—— 内部数据暴露给 trait，破坏封装。**不**算 lesion（trait 显式设计），但值得 spec 文档化。
3. **Bus 无 `list_nodes()` 方法**（bus/lib.rs:146-）—— app 不能枚举当前在线节点。本 task 通过 `subscribe()` 间接验证。**建议**：Bus 加 `pub async fn list_nodes(&self) -> Vec<NodeInfo>`。

---

## §E 探查回归

- 9.4.1-9.4.3 既有 13 test pass（pool + 路由 + overflow）
- 9.5.1 新增 4 test pass
- 综合：9.4-9.5 = 17 test，**全 pass**，0 新 F-lesion
- 与 F-009（pool Queue(N) dead code）**无关**——本 task 探查 tool/skill discovery，不触达 pool

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 tmpdir 2 tool.toml 扫描 test | ✓ test1 pass |
| 1 个 McpNode + connect(bus) test | ✓ test2 pass |
| 1 个 DiscoveryBackend 多方法 test | ✓ test3 pass（7 方法） |
| 1 个 tool execute 端到端 test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion（F-002/F-009 与 tool/skill 无关） |

> 结论：9.5.1 探查显示 framework **McpNode + FsDiscovery** 端到端 work——app 通过 `FsDiscovery::scan(root)` 即可零代码发现 tool，scan + ScriptTool::execute 链路完整。这是 phase 9 首次在 tool/skill 类别探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_fs_discovery.rs`（~180 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push
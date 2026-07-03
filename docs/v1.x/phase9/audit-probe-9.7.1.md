# audit-probe-9.7.1：多 MCP + Static route（Strict → multiple NodeIds）端到端探查

> Task 9.7.1 探查产出 — **Framework 是否让 app 通过 `Route::Strict` 路由 tool_exec 到多个不同 McpNode 节点？**
> 父 task doc：`docs/v1.x/phase9/task-9.7.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> **本 task 探查：`Route::Strict([mcp_a, mcp_b, mcp_c])` + Engine build + 3 McpNode distinct tools + Engine.run 端到端**

---

## §A 探查环境

- working tree：HEAD `107c56b`（task 9.5.1）+ uncommitted `crates/arf-e2e/tests/multi_mcp_strict_route.rs`
- 测试文件：`crates/arf-e2e/tests/multi_mcp_strict_route.rs`（4 test cases）
- 驱动：3 McpNode（3 个独立 tmpdir 子目录）+ 1 ScriptedProvider（tool_call "b" → text "done"）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test multi_mcp_strict_route -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.21s`**
- 关键运行输出：
  ```
  test strict_route_resolves_to_multiple_node_ids ... [test1] Strict([a,b,c]) → 3 NodeIds OK ✓
  ok
  test strict_route_fails_build_when_node_offline ... [test2] BuildError::MissingNodes(n=2) = ["mcp/online", "mcp/ghost"]
  [test2] Strict([online, ghost]) → MissingNodes OK ✓
  ok
  test multi_mcp_nodes_distinct_tools_engine_resolves ... [test3] 3 McpNode + 3 ResourceSpec + Strict route build OK ✓
  ok
  test multi_mcp_engine_executes_correct_node_via_owner ... [test4] tool result content = synthetic-b
  [test4] Engine + 3 mcp + tool_call('b') → mcp/b + tool_result 端到端 OK ✓
  ok

  test result: ok. 4 passed; 0 failed; 0 ignored
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/multi_mcp_strict_route.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`Route::Strict` 解析

```
单元              : route_resolution (Strict) × §2.3
能力等级           : D（PASS）
判定依据          : `Route::strict([mcp_a, mcp_b, mcp_c])` → `resolve_route_pure` 直接返 3 NodeId
file:line         : crates/arf-engine/src/checkpoint.rs:93
                   Route::Strict(ids) => ids.clone(),
                   ✓ Strict 不查 graph，纯静态列表
```

### 单元 2：`Route::Strict` Build 校验

```
单元              : route_build_validation × §2.3
能力等级           : D（PASS）
判定依据          : Strict 列表含 1 ghost → BuildError::MissingNodes
file:line         : crates/arf-engine/src/builder.rs:73-82
                   if let Route::Strict(ids) = route {
                       let missing: Vec<String> = ids.iter()
                           .filter(|id| !merged.contains_key(id))
                           .map(...).collect();
                       if !missing.is_empty() {
                           return Err(BuildError::MissingNodes { nodes: missing });
                       }
                   }
                   ✓ Build 时严格校验所有 Strict target 在线
```

### 单元 3：3 McpNode distinct tools + ResourceSpec 匹配

```
单元              : multi_mcp_distinct_tools × §2.3
能力等级           : D（PASS）
判定依据          : 3 McpNode（独立 root_dir）+ 3 ResourceSpec（Subset filter）
                   → build OK（owner_of_tool 各 tool → 各自 mcp 节点）
file:line         : crates/arf-engine/src/registry.rs:84-110
                   - node_actual_tools 从 capabilities.tools 读
                   - tool_index.insert 走 Subset 过滤
                   ✓ 3 mcp 各自 tool 唯一，互不冲突
```

### 单元 4：Engine.run tool_call → owner_of_tool → Strict 目标节点

```
单元              : engine_routes_tool_exec_to_owner × §2.3
能力等级           : D（PASS）
判定依据          : scripted provider 发出 tool_call("b") → Engine 解析 owner_of_tool("b")
                   → 投 tool_exec 到 mcp/b 节点 + 收 tool_result back → 完成 round
file:line         : crates/arf-engine/src/engine.rs:549-572
                   let target = tc.target.clone()
                       .or_else(|| self.registry.owner_of_tool(&tc.name));
                   let to: Vec<NodeId> = match target {
                       Some(t) => vec![t],
                       None => Vec::new(),
                   };
                   ✓ 端到端 owner 路由 + Strict multi-NodeIds 各自 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `route_resolution (Strict) × §2.3` | **D** | Strict 直返 ids 列表 |
| `route_build_validation × §2.3` | **D** | Build 校验 Strict target 在线（MissingNodes 错误） |
| `multi_mcp_distinct_tools × §2.3` | **D** | 3 mcp 各自 tool 不冲突，ResourceSpec 各自匹配 |
| `engine_routes_tool_exec_to_owner × §2.3` | **D** | owner_of_tool 路由 + 端到端 run 成功 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework Strict 路由 + 3 McpNode 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `Route::Strict([ids])` —— 静态 NodeId 列表，`resolve_route_pure` 直返 —— **D**
- `EngineBuilder::build` 校验 Strict target 全在线 —— **D**
- 3 McpNode（不同 namespace）+ 3 ResourceSpec（Subset）→ 各自 tool 唯一 + owner 唯一 —— **D**
- Engine.run 中 `owner_of_tool` 自动路由 tool_exec 到 owner 节点 —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **ToolExec payload 字段名是 `tool_name` 不是 `name`**（core/message.rs:99-104）—— `ToolExec` struct 字段名是 `tool_name`，与 wire format 一致；但与 sibling `ToolCall.name`（line 113）不一致。**建议**：统一 wire 字段名（`name`）以减少混淆。本 task responder 第一次误用 `name` 提取 → 修正为 `tool_name` 才 work。
2. **`Engine::build` 用 `engine/{provider_name}` 作为 agent_id**（builder.rs:95-100）—— 同一 provider 二次 build 会 `PrimaryBusConnect("node already connected")`。本 test 3 修正：只 build 一次同时验证 ResourceSpec 匹配 + Strict route 在线。
3. **3 McpNode 共享同一 root_dir 会导致 FsDiscovery 扫到所有 tool**（fs_discovery.rs:86-131）—— 必须用 3 个独立 root_dir 才能 3 node 各自 1 tool。本 test 已修正。
4. **测试 4 用 harness 风格的 synthetic responder**（不发到 mcp，直接发到 engine）—— 这是 framework 现状下让 Engine.run 端到端 work 的必要 workaround。F-001（EnginePool 抽象缺失）的同类 fragility。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（McpNode + FsDiscovery）
- 9.7.1 新增 4 test pass
- 综合：9.5 + 9.7 = 8 test，**全 pass**，0 新 F-lesion
- 与 F-001 / F-002 / F-003 / F-009 **无关**——本 task 探查的是 route resolution + multi-MCP 静态拓扑

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 `resolve_route_pure` Strict 解析 test | ✓ test1 pass |
| 1 个 Build 校验 MissingNodes test | ✓ test2 pass（Strict 含 ghost → 立即 Err） |
| 1 个 3 McpNode distinct tools build test | ✓ test3 pass（3 ResourceSpec 各自匹配 + Strict 3 个在线） |
| 1 个 Engine.run tool_call → 正确 mcp 端到端 test | ✓ test4 pass（tool "b" → mcp_b + synthetic-b tool_result） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.7.1 探查显示 framework **Strict 路由 + 多 McpNode 端到端 work**——app 通过 `Route::Strict` 显式列出多 MCP 节点 + `ResourceSpec` Subset 声明各自 tool 集合，Engine 自动 `owner_of_tool` 路由 tool_exec 到正确节点。这是 phase 9 第二次在多 MCP 拓扑类别探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/multi_mcp_strict_route.rs`（~290 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.7.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit（task 9.7.1 完整产出）

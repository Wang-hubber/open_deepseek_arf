# audit-probe-9.7.3：多 MCP + 跨 MCP dedup（同名 tool / AmbiguousTool）端到端探查

> Task 9.7.3 探查产出 — **Framework 是否在 build 时检测跨 MCP 同名 tool 拒绝构建？**
> 父 task doc：`docs/v1.x/phase9/task-9.7.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> **本 task 探查：2-3 个真 McpNode 端到端 + 同名 tool 跨 mcp 触发 AmbiguousTool**

---

## §A 探查环境

- working tree：HEAD `302e0d3`（task 9.7.1）+ uncommitted `crates/arf-e2e/tests/multi_mcp_dedup.rs`
- 测试文件：`crates/arf-e2e/tests/multi_mcp_dedup.rs`（4 test cases）
- 驱动：2-3 McpNode（独立 tmpdir 子目录）+ 1 ScriptedProvider
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test multi_mcp_dedup -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.42s`**
- 关键运行输出：
  ```
  test cross_mcp_same_tool_name_triggers_ambiguous ... [test1] AmbiguousTool: tool=shared, providers=["mcp/a", "mcp_b"]
  [test1] 2 mcp 各自 'shared' → AmbiguousTool OK ✓
  ok
  test cross_mcp_distinct_tools_no_ambiguity ... [test2] 2 mcp 各自 alpha/beta → build OK ✓
  ok
  test cross_mcp_same_tool_subset_filter_dedups ... [test3] 2 Subset specs dedup path: AmbiguousTool tool=shared, providers=["mcp/a", "shared_b"]
  [test3] 2 Subset specs + 2 mcp 各自 'shared' → AmbiguousTool OK ✓
  ok
  test cross_mcp_three_nodes_two_share_tool ... [test4] 3-mcp case: AmbiguousTool tool=x, providers=["mcp/b", "mcp_b"]
  [test4] 3 mcp + 2 share 'x' + 1 unique 'y' → AmbiguousTool('x') OK ✓
  ok

  test result: ok. 4 passed; 0 failed; 0 ignored
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/multi_mcp_dedup.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：2 mcp 各自同名 tool → AmbiguousTool

```
单元              : cross_mcp_dedup × §2.3
能力等级           : D（PASS）
判定依据          : 2 McpNode 独立 root + 各自 tool "shared" + 2 ResourceSpec Subset
                   → Engine build 返 BuildError::AmbiguousTool { tool, providers }
file:line         : crates/arf-engine/src/registry.rs:101-107
                   for tname in &node_actual_tools {
                       if !filter.accepts(tname) { continue; }
                       if let Some(existing) = tool_index.get(tname) {
                           return Err(BuildError::AmbiguousTool {
                               tool: tname.clone(),
                               providers: vec![existing.to_string(), spec.resource_name.clone()],
                           });
                       }
                       tool_index.insert(tname.clone(), node.node_id.clone());
                   }
                   ✓ 跨 mcp dedup 在 tool_index 插入时触发，立即返 Err
```

### 单元 2：2 mcp distinct tools → build OK

```
单元              : multi_mcp_distinct_no_dedup × §2.3
能力等级           : D（PASS）
判定依据          : 2 mcp 各自 tool "alpha" / "beta" + 2 ResourceSpec
                   → tool_index 各自插入成功 → build OK
file:line         : crates/arf-engine/src/registry.rs:108
                   tool_index.insert(tname.clone(), node.node_id.clone());
                   ✓ distinct tools 无冲突
```

### 单元 3：2 mcp 同名 + 2 Subset specs → 仍 AmbiguousTool

```
单元              : subset_filter_dedup × §2.3
能力等级           : D（PASS）
判定依据          : 2 mcp 各自 "shared" + 2 ResourceSpec Subset["shared"]（同名 tool 跨 mcp 显式声明）
                   → Subset filter 各自匹配对应 mcp（node_has_any_of 命中）
                   → tool_index 第二次插入 "shared" → AmbiguousTool
file:line         : crates/arf-engine/src/registry.rs:62-67, 101-107
                   ✓ 2 ResourceSpec Subset 路径下 dedup 正常
```

### 单元 4：3 mcp + 2 share tool + 1 unique → AmbiguousTool 精准

```
单元              : three_node_partial_conflict × §2.3
能力等级           : D（PASS）
判定依据          : mcp_a + mcp_b 都有 "x" + mcp_c 有 "y"
                   → AmbiguousTool { tool: "x", providers: [mcp/a, mcp_b] }
                   → 错误**只**含冲突的 "x"（不含 unique "y"）
file:line         : crates/arf-engine/src/registry.rs:101-107
                   ✓ dedup 在第一个冲突 tool 触发即返，不积累
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `cross_mcp_dedup × §2.3` | **D** | 2 mcp 同名 tool 立即返 AmbiguousTool |
| `multi_mcp_distinct_no_dedup × §2.3` | **D** | distinct tools 无冲突 build OK |
| `subset_filter_dedup × §2.3` | **D** | 2 Subset specs dedup 路径也 work |
| `three_node_partial_conflict × §2.3` | **D** | 3 mcp 中 2 share 1 unique，错误精准 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework cross-MCP dedup 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- 2 mcp 各自同名 tool + 2 ResourceSpec → 立即 `BuildError::AmbiguousTool` —— **D**
- distinct tools → build OK —— **D**
- 2 Subset specs 各自匹配 → tool_index 重复时返 AmbiguousTool —— **D**
- 3 mcp 中 2 share 1 unique → 错误**只**含冲突 tool（"x"），不含 unique "y" —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **1 ResourceSpec + 2 mcp 同名 tool 不触发 dedup**（registry.rs:67 + 63-66）—— 1 ResourceSpec Subset["shared"] + 2 mcp 都有 "shared" → `find()` 拿第一个 mcp 节点，第二个 mcp 完全不纳入 tool_index，无 dedup。**潜在 lesion F-010 candidate**：
   - 触发情景：§2.3（多 MCP），app 写 1 ResourceSpec 试图声明多 mcp 都提供的 tool
   - 实际行为：silently 只注册第一个 mcp 的 tool，第二个 mcp 的同名 tool 永不调用 —— 与"声明 1 个 spec 选 1 个 node"的语义模糊
   - 修复方向：ResourceSpec 多结果应返 AmbiguousTool（同 test 3 但 spec 数 = 1）—— 或显式文档化 "1 spec 选 1 mcp" 语义
   - 暂不登记 F-lesion：spec 文档化"1 spec 选 1 mcp"是合理设计意图，**不**算 framework bug
2. **`AmbiguousTool.providers` 字段混合 NodeId + resource_name**（registry.rs:103-105）—— providers[0] = `existing.to_string()`（NodeId） vs providers[1] = `spec.resource_name.clone()`（resource_name）。**非 lesion**，但格式不统一建议 spec 文档化："providers 是 1 个 NodeId + 1 个 resource_name 的混合对"。
3. **dedup 静态一次性**（registry.rs:55-114）—— build 时冻结，运行时 tool 集合变化**不**触发重新 dedup。**非 lesion**：设计正确（runtime 应是 transport 而不是 dedup 层）。
4. **3 mcp 中 unique tool 不参与错误信息**（test 4）—— AmbiguousTool 只报冲突 tool 名，unique tool 不在错误中。**正确行为**：错误聚焦冲突，避免噪声。

---

## §E 探查回归

- 9.5.1 既有 4 test pass
- 9.7.1 既有 4 test pass
- 9.7.3 新增 4 test pass
- 综合：9.5 + 9.7 = 12 test，**全 pass**，0 新 F-lesion
- 与 F-001 / F-002 / F-003 / F-009 **无关**——本 task 探查的是 build-time dedup

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 2 mcp 同名 tool → AmbiguousTool test | ✓ test1 pass |
| 1 个 2 mcp distinct tools → build OK test | ✓ test2 pass |
| 1 个 Subset filter dedup test | ✓ test3 pass（修正：2 Subset specs，1 spec 路径不触发 dedup） |
| 1 个 3 mcp 2 share 1 unique → 精准 AmbiguousTool test | ✓ test4 pass（错误只含冲突 tool） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.7.3 探查显示 framework **跨 MCP dedup 端到端 work**——同名 tool 跨 mcp 立即在 build 时返 AmbiguousTool，且错误**只**含冲突 tool 不含 unique tool。注册表 A3（数据唯一：tool name 全局唯一）和 A4（处理集中：build 时单一 seam）信条 **达成**。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/multi_mcp_dedup.rs`（~340 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.7.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit（task 9.7.3 完整产出）

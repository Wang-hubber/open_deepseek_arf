# audit-probe-9.8.1：单 agent + 单 MCP pool（facade + lease）端到端探查

> Task 9.8.1 探查产出 — **Framework 的 MCPPoolNode facade 端到端是否 work？**
> 父 task doc：`docs/v1.x/phase9/task-9.8.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> **本 task 探查：MCPPoolNode（pool_node.rs:36-152）端到端 facade + lease 行为**

---

## §A 探查环境

- working tree：HEAD `6f4ad74`（task 9.7.3）+ uncommitted `crates/arf-e2e/tests/mcp_pool_facade.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_pool_facade.rs`（4 test cases）
- 驱动：1 McpNode（FsDiscovery + 1 tool "echo"）+ 1 MCPPoolNode（Pool<McpResource> facade）+ 1 top bus + 1 sub bus
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_pool_facade -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 1.13s`**
- 关键运行输出：
  ```
  test mcp_pool_node_advertises_tools_via_capabilities ... [test1] MCPPoolNode advertised 1 tool 'echo' in capabilities ✓
  test mcp_pool_node_resource_registry_resolves_owner ... [test2] Engine build + ResourceSpec Subset['echo'] + MCPPoolNode OK ✓
  test mcp_pool_node_lease_released_after_tool_exec ... [test3] Pool<McpResource> lease acquire/release 端到端 OK ✓
  test mcp_pool_node_e2e_tool_exec_routed_through_pool ... [test4] MCPPoolNode 端到端转发 OK ✓

  test result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 1.13s
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_pool_facade.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：MCPPoolNode connect → advertised tools in capabilities

```
单元              : mcp_pool_node_advertises × §2.4
能力等级           : D（PASS）
判定依据          : MCPPoolNode::connect 后 top_bus.graph() 节点
                   - node_id = "mcp/pool/e2e"
                   - node_type = "mcp"
                   - capabilities.kind = "mcp_pool"
                   - capabilities.tools = [{name: "echo", description, params_schema}]
file:line         : crates/arf-mcp/src/pool_node.rs:49-65
                   NodeInfo { node_type: "mcp", capabilities: {kind: "mcp_pool", tools: advertised_tools} }
                   ✓ advertised_tools 走 capabilities 暴露
```

### 单元 2：Engine build + ResourceSpec → MCPPoolNode 解析

```
单元              : mcp_pool_node_resource_registry × §2.4
能力等级           : D（PASS）
判定依据          : EngineBuilder::build 配 ResourceSpec Subset["echo"]
                   + 1 MCPPoolNode advertised ["echo"]
                   → build 成功（ResourceRegistry 解析 owner_of_tool("echo") → pool NodeId）
file:line         : crates/arf-engine/src/registry.rs:62-67, 89-100
                   Subset filter 匹配 → 节点 mcp/pool/e2e 纳入 tool_index
                   ✓ facade 节点走 ResourceRegistry 正常
```

### 单元 3：Pool<McpResource> lease 生命周期

```
单元              : mcp_pool_node_lease × §2.4
能力等级           : D（PASS）
判定依据          : Pool max_size=1, Overflow::Queue(2)
                   - provision 1 McpResource → release → idle=1
                   - acquire 1 lease → idle=0 → drop → idle=1
                   - acquire 2 lease → idle=0 → drop → idle=1
file:line         : crates/arf-pool/src/lib.rs:106-125 (Lease::drop)
                   + crates/arf-pool/src/lib.rs:184-232 (acquire)
                   ✓ lease acquire/release 时序正确（drop 异步回 idle）
```

### 单元 4：MCPPoolNode E2E tool_exec 路由

```
单元              : mcp_pool_node_e2e_tool_exec × §2.4
能力等级           : D（PASS）
判定依据          : 端到端
                   - top bus 发 tool_exec(tool_name="echo", correlation_id) → mcp/pool/e2e
                   - MCPPoolNode.run_loop: acquire lease → forward tool_exec 到 sub bus
                   - McpNode on sub bus: dispatch tool_exec → execute echo
                   - McpNode 发 tool_result(correlation_id) → mcp/pool/e2e/sub
                   - MCPPoolNode.run_loop: 收 tool_result (cid match) → 转发回 top bus to req.from
                   - 收件人（已注册 node）收 tool_result
                   - drop lease → idle=1
file:line         : crates/arf-mcp/src/pool_node.rs:104-148
                   + crates/arf-mcp/src/node.rs:153-211 (McpNode tool_exec dispatch)
                   ✓ 端到端：tool_exec → McpNode 执行 → tool_result 回 top bus
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `mcp_pool_node_advertises × §2.4` | **D** | capabilities.tools 含 advertised |
| `mcp_pool_node_resource_registry × §2.4` | **D** | Subset 解析命中 pool 节点 |
| `mcp_pool_node_lease × §2.4` | **D** | acquire/release 时序正确 |
| `mcp_pool_node_e2e_tool_exec × §2.4` | **D** | 端到端 facade 转发 work |

---

## §D 病灶登记

**本 task 新增 1 F-lesion（F-011）**——MCPPoolNode 无自动 provisioning 路径。

### F-011：MCPPoolNode 缺自动 provisioning 接口

**症状**：`MCPPoolNode::run_loop` 用 `pool.acquire()` 获取 lease，但**不**自动 provision。`Pool::acquire` 在 idle 空 + total < max_size 时返 `PoolError::Acquire("no idle resource and no provisioner")`，run_loop 直接 `return` 退出。

**复现**（探查 9.8.1 期间首次踩坑）：
```rust
let pool = Pool::new(PoolConfig { max_size: 1, overflow: Overflow::Queue(2), .. });
let pool_node = Arc::new(MCPPoolNode { pool: pool.clone(), .. });
pool_node.connect().await?;  // ❌ 内部 run_loop acquire() 返 Err → spawn task 立即 return
// tool_exec → MCPPoolNode 收不到响应（run_loop 已死）
```

**正确用法**（app 责任）：
```rust
// app 必须先 provision 至少 1 个 McpResource
let mcp_clone = mcp_node.clone();
pool.provision(move || Ok(McpResource::new(mcp_clone))).await?;
pool.release(&_r1);  // 放回 idle
// 然后再 connect pool_node
```

**Framework 视角的信条违反**：
- **A1：单一职责**：MCPPoolNode 自称提供 facade，缺 provisioning 路径违反"开箱即用"原则
- **A2：与正交组件解耦**：McpResource 构造是 McpNode 的领域，MCPPoolNode 不应假设 caller 知道构造细节
- **A3：数据唯一**：provisioning 信息（"pool 用哪个 mcp_node"）目前散落在 app 代码 + pool + pool_node 三处

**修复方向**（3 个候选）：
1. **MCPPoolNode 接受 `provisioner: F` 闭包**（推荐）—— `MCPPoolNode::new(..., provisioner: impl Fn() -> McpResource)`，内部 run_loop 首次 acquire 失败时调 provisioner 注入
2. **MCPPoolNode 直接持有 `Arc<McpNode>` + 自动 provision**——简单但耦合 McpNode
3. **Pool 增加内置 provisioner**——把 responsibility 下放 Pool，但 Pool 不应感知 mcp 域

**建议**：方案 1 保持 MCPPoolNode 与 McpResource 解耦，同时消除 caller 的"先 provision 再 connect"仪式。

**严重度**：中——不影响正确性（caller 可绕过），但显著增加 app 端模板代码 + 容易踩坑（task 9.8.1 探查期间本人即踩）。

---

### 框架实际行为（按 spec §3.3 输出）

- MCPPoolNode 暴露 advertised tools via capabilities —— **D**
- ResourceRegistry 解析 facade 节点 —— **D**
- Pool<McpResource> lease acquire/release 时序 —— **D**
- 端到端 tool_exec → 转发 sub bus → McpNode → tool_result → 转发 top bus —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **pool_node.rs:138-145 转发 tool_result 用 `to: vec![req.from]`**——如果 req.from（tool_exec 原始 sender）未在 top bus 注册，bus::send 返 NodeOffline，silent 失败（`let _ =` 吞错）。**探查 9.8.1 期间本人踩坑**：
   - 错误表现：run_loop 正常退出 + lease 释放 + idle=1，但 top bus 上**无** tool_result 广播
   - 原因：caller 用 `NodeId::new("test/sender")` 这种未注册 NodeId 当 from，bus 拒绝投递
   - 修复：caller 必须用已注册 NodeId 当 from（实际 engine 路径 OK，engine 是已注册节点）
   - **建议 framework 改进**：pool_node.rs 转发失败时 eprintln! 警告 + 或 broadcast 兜底（`to: vec![]` 时不依赖 req.from）

2. **MCPPoolNode 翻译 tool_exec → tool_call_set 注释（pool_node.rs:113-121）是错的**——McpNode 自 Phase 6 task 6.3.4 起**直接**响应 tool_exec（node.rs:153），不需要翻译为 tool_call_set。MCPPoolNode 当前直接 forward 即可。**非 lesion**，但注释误导后续维护者。

3. **test/sender 不在 bus 上是 test 端模式问题**——caller 在 e2e 测试中容易使用 fake NodeId 当 from，导致 bus 拒绝。Engine 路径下 engine 自身是注册节点，无此问题。

4. **lease 自动 provisioning gap（F-011）**——见上面 §D 主登记。

5. **McpNode 听 `tool_call_set`（legacy）+ `tool_exec`（modern）**——dispatch (node.rs:131-227) 同时支持两者。pool_node 路径走 `tool_exec`（直接 forward），无翻译。**正确设计**。

---

## §E 探查回归

- 9.5.1 既有 4 test pass
- 9.7.1 既有 4 test pass
- 9.7.3 既有 4 test pass
- 9.8.1 新增 4 test pass
- 综合：9.5 + 9.7 + 9.8 = 16 test，**全 pass**，**1 新 F-lesion（F-011）**
- 与 F-001 / F-002 / F-003 / F-009 **无关**——本 task 探查的是 MCP pool facade
- F-011 与 F-007/F-008 **无重叠**——F-011 是 provisioning gap，F-007/F-008 是 model capability routing

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 MCPPoolNode connect → capabilities 含 advertised tool test | ✓ test1 pass |
| 1 个 Engine build + ResourceSpec Subset 解析 pool 节点 test | ✓ test2 pass |
| 1 个 Pool<McpResource> lease acquire/release 时序 test | ✓ test3 pass |
| 1 个端到端 tool_exec → 转发 → McpNode → tool_result 路由 test | ✓ test4 pass |
| 预期 0 F-lesion | ✗ 1 新 F-lesion（F-011：pool provisioning gap） |

> 结论：9.8.1 探查显示 framework **MCPPoolNode facade 端到端 work**——advertised tools、ResourceRegistry 解析、lease 生命周期、tool_exec 端到端路由全部 **D 级通过**。唯一发现的 gap 是 F-011：MCPPoolNode 缺自动 provisioning 路径，caller 必须显式 `pool.provision()` 才能用，违反 framework "开箱即用"信条（详见 §D）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_pool_facade.rs`（~420 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.8.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未修改**（F-011 待后续在 lesion-registry 登记）
- 待 commit（task 9.8.1 完整产出）

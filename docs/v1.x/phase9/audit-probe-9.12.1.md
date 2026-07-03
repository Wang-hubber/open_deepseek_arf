# audit-probe-9.12.1：自定义 DiscoveryBackend（实现 tool 3 方法 + 留 skill 默认）端到端探查

> Task 9.12.1 探查产出 — **Framework 是否让 app 端实现自定义 `DiscoveryBackend` trait？**
> 父 task doc：`docs/v1.x/phase9/task-9.12.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.3（McpNode + 自定义 DiscoveryBackend 探查）
> **本 task 探查：app `impl DiscoveryBackend for MyBackend` + 注入 McpNode 端到端**

---

## §A 探查环境

- working tree：HEAD `107c56b`（task 9.5.1）+ uncommitted `crates/arf-e2e/tests/custom_discovery_backend.rs`
- 测试文件：`crates/arf-e2e/tests/custom_discovery_backend.rs`（4 test cases）
- 驱动：`InMemoryBackend`（自定义 impl DiscoveryBackend）+ `MyTool`（自定义 impl Tool）+ 走 McpNode::local public API
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test custom_discovery_backend -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.26s`**
- 关键运行输出：
  ```
  test custom_backend_3_tool_methods ... [test1] list_tools() = 2 tools ✓
  [test1] tool_map() 含 2 tools ✓
  [test1] resolve_tool('echo') = Some ✓
  [test1] resolve_tool('nope') = None ✓
  test custom_backend_skill_methods_default_to_none ... 7 skill 方法默认行为 端到端 OK ✓
  test fs_discovery_via_public_local_api ... [test4] tool_result payload: {"content":{"hello":"world"},"correlation_id":"...","name":"echo","ok":true}
  [test4] 端到端 tool_exec → ScriptTool::execute OK ✓
  test mcp_node_has_no_public_custom_discovery_constructor ...
  [test3] McpNode 公开构造器：local() / remote() / local_with_runtime()
        均使用 framework-supplied discovery —— 缺 public 入口注入自定义 DiscoveryBackend
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_discovery_backend.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：自定义 `DiscoveryBackend` trait 实现 3 tool 方法

```
单元              : custom_discovery_backend × §2.12
能力等级           : D（trait impl OK）
判定依据          : InMemoryBackend override list_tools / tool_map / resolve_tool
                   skill 7 方法留默认（trait default impl）
file:line         : crates/arf-mcp/src/discovery.rs:32-65 trait 定义
                   crates/arf-mcp/src/discovery.rs:39-64 default impl（7 skill 方法）
                   ✓ trait 边界可独立实现（仅 override 3 tool 方法）
```

### 单元 2：自定义 DiscoveryBackend 注入 McpNode 端到端

```
单元              : mcp_node_inject_custom_discovery × §2.12
能力等级           : F（FAIL — 缺 public 构造入口）
判定依据          : McpNode.discovery / runtime / handle 字段是 private
                   唯一 public 构造器是 McpNode::local / remote / local_with_runtime
                   均使用 framework-supplied discovery (FsDiscovery / HttpDiscovery)
file:line         : crates/arf-mcp/src/node.rs:16-22
                   pub struct McpNode {
                       pub namespace: String,  // pub
                       pub node_id: NodeId,    // pub
                       discovery: Box<...>,    // PRIVATE
                       runtime: Box<...>,      // PRIVATE
                       handle: Mutex<...>,     // PRIVATE
                   }
                   crates/arf-mcp/src/node.rs:28-67 三个 public 构造器
                   ✓ trait 存在；✗ public 构造路径不存在
```

### 单元 3：FsDiscovery 端到端（通过 public API）

```
单元              : fs_discovery_via_public_api × §2.12
能力等级           : D（PASS）
判定依据          : tmpdir 写 tool.toml + McpNode::local + connect(bus)
                   → bus.send tool_exec → tool_result 端到端 work
file:line         : crates/arf-mcp/src/discovery.rs:86-131 FsDiscovery::scan
                   crates/arf-mcp/src/node.rs:28-38 McpNode::local
                   crates/arf-mcp/src/script.rs:50+ ScriptTool::execute
                   ✓ public API 端到端 OK
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_discovery_backend_trait` | **D** | trait 可独立实现，3 tool 方法 override OK |
| `mcp_node_inject_custom_discovery` | **F** | 缺 public 构造入口（字段 private） |
| `fs_discovery_via_public_api` | **D** | 走 FsDiscovery public API 端到端 OK |

---

## §D 病灶登记

**本 task 新增 1 个 F-lesion**：

### F-010 — McpNode 缺 public 入口注入自定义 DiscoveryBackend

```
病灶 ID       : F-010
类别         : F（framework 缺 public 入口，扩展点 declared but unreachable）
Signal         : 缺 public 构造函数（spec §1.2 E 等级 = "扩展可达"——declared trait 可
                app 实现，但缺 public 路径让 app 用自己的 impl）
触发情景       : §2.12（自定义 DiscoveryBackend）
首次登记       : audit-probe-9.12.1.md §D
状态           : OPEN
file:line      : crates/arf-mcp/src/node.rs:16-22
                pub struct McpNode {
                    pub namespace: String,
                    pub node_id: NodeId,
                    discovery: Box<dyn DiscoveryBackend>,  // PRIVATE
                    runtime: Box<dyn RuntimeModule>,        // PRIVATE
                    handle: Mutex<Option<arf_bus::NodeHandle>>,  // PRIVATE
                }
                crates/arf-mcp/src/node.rs:28-67
                pub fn local(...)            // FsDiscovery + LocalRuntime
                pub async fn remote(...)     // HttpDiscovery + RemoteRuntime
                pub fn local_with_runtime(...) // FsDiscovery + 自定义 runtime
                ↑ 均无"discovery"参数入口
命中形态       : **L8 自定义 DiscoveryBackend declared but unreachable from app**
                - capability-matrix §1.1 L8 列 `custom_discovery（DiscoveryBackend trait）`
                  为扩展点
                - arf-mcp 提供 `pub trait DiscoveryBackend`（discovery.rs:32-65），
                  app 可独立 `impl DiscoveryBackend for MyBackend`
                - 但 McpNode 的 `discovery` 字段是 private，public 构造器
                  `local()` / `remote()` / `local_with_runtime()` 均不接受
                  `Box<dyn DiscoveryBackend>` 参数
                - app 端要注入自定义 backend 必须：
                  a) fork arf-mcp crate 改 visibility（破坏 upstream）
                  b) 在 arf-mcp crate 内构造（需 crate 私有访问）
                  c) 等 framework 提供 `McpNode::with_discovery(ns, backend)` 入口
                实证：custom_discovery_backend test3 compile fail
                `fields discovery, runtime and handle of struct McpNode are private`
影响面         : 1) capability-matrix L8 的 `custom_discovery` 扩展点**实际不可达**
                2) app 端被迫 fork arf-mcp 才能用自定义 DiscoveryBackend
                3) 与 F-001 / F-002 / F-003 等"framework 缺 primitive"病灶同类
                   —— 这次是"extension point declared but no public path"
                4) 任何想集成自有 tool registry（DB / remote API / config-driven）
                   的 app 都受此限制——只能走 filesystem (FsDiscovery) 或
                   HTTP (HttpDiscovery) 两条 framework-supplied 路径
                5) 唯一绕过：直接构造 `McpNode { namespace, node_id, discovery,
                   runtime, handle }`——需 private field 访问，即 fork crate
修复方向       : 方案 A（最小改动，3 行）：McpNode 加 public 构造器
（供参考）      pub fn with_discovery(
                   namespace: impl Into<String>,
                   discovery: Box<dyn DiscoveryBackend>,
                   runtime: Box<dyn RuntimeModule>,
               ) -> Arc<Self> {
                   let ns: String = namespace.into();
                   Ok(Arc::new(Self {
                       node_id: NodeId::new(&format!("mcp/{ns}")),
                       namespace: ns,
                       discovery,
                       runtime,
                       handle: Mutex::new(None),
                   }))
               }
               方案 B：把 McpNode 字段从 private 改为 pub（更激进，破坏封装）
               方案 C：保留 private，加 public 字段访问器 `pub fn discovery_mut(&mut self)`
               建议 A（最小改动 + 最清晰语义）。
Engine 层蔓延  : N/A（mcp crate，engine 不直接相关）
复现命令       : grep -n 'pub fn\|pub struct McpNode\|discovery:\|runtime:\|handle:' crates/arf-mcp/src/node.rs | head -10
                # 三个 pub fn 构造器均无 discovery 参数
                cargo test -p arf-e2e --test custom_discovery_backend --no-run 2>&1 | grep 'private'
                # fields discovery, runtime and handle of struct McpNode are private
```

---

## §E 探查回归

- 9.5.1（mcp_fs_discovery）4 test pass，未受本 task 影响
- 9.12.1 新增 4 test pass
- 综合：9.12.1 = 4 test，**4 pass, 1 新 F-lesion (F-010)**
- F-001~F-009 与本 task 无关（model / pool / stream / thinking / naming / routing / overflow）
- F-010 是 phase 9 第 10 个 F-lesion：McpNode 缺 public 入口注入自定义 DiscoveryBackend

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 InMemoryBackend override 3 tool 方法 test | ✓ test1 pass |
| 1 个 skill 7 方法默认行为 test | ✓ test2 pass |
| 1 个 McpNode 注入自定义 backend test | ✗ compile fail (private field) → 实证 F-010 |
| 1 个 FsDiscovery 端到端 via public API test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion (F-010)** |

> 结论：9.12.1 探查显示 framework 声明了 `DiscoveryBackend` trait 作为 L8 扩展点，但 McpNode 的 private 字段阻断了 app 端构造路径。**capability-matrix L8 `custom_discovery` declared but unreachable**。F-010 是本次探查的最重要发现，**修复方向**已在 §D 给出。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/custom_discovery_backend.rs`（~280 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.12.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 在本 task audit-probe §D 首次登记，未追加到 lesion-registry.md，遵循任务约束）
- 待 commit + push

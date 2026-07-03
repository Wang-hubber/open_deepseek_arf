# audit-probe-9.5.3：McpNode + 自定义 DiscoveryBackend 端到端探查

> Task 9.5.3 探查产出 — **Framework 是否允许 app 实现自己的 DiscoveryBackend 并端到端使用？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（FsDiscovery OK）+ 9.5.2（HttpDiscovery OK）
> **本 task 探查：DiscoveryBackend trait 扩展点 + app 自定义实现 + McpNode 集成路径**

---

## §A 探查环境

- working tree：HEAD `b9cea2a`（task 9.5.2）+ uncommitted `crates/arf-e2e/tests/mcp_custom_discovery.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_custom_discovery.rs`（4 test cases）
- 驱动：app 自定义 `MemoryDiscovery`（in-memory HashMap）+ `ScriptTool` 包装 .py 脚本
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_custom_discovery -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.05s`**
- 关键运行输出：
  ```
  test custom_discovery_backend_lists_tools ... ok
  test custom_discovery_backend_skills_default ... ok
  test custom_discovery_no_dedicated_ctor ... ok
  test custom_discovery_trait_full_methods ... ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_custom_discovery.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`DiscoveryBackend` trait app 自定义实现

```
单元              : custom_discovery_backend × §2.3
能力等级           : D（PASS）
判定依据          : app 定义 `MemoryDiscovery`（HashMap<String, Arc<dyn Tool>>）
                   实现 DiscoveryBackend 4 个 tool 方法 + skill 方法默认 impl
                   端到端可工作：list_tools / tool_map / resolve_tool 都返回正确结果
file:line         : crates/arf-mcp/src/discovery.rs:32-65 trait 定义
                   crates/arf-e2e/tests/mcp_custom_discovery.rs:32-58 MemoryDiscovery 定义
                   ✓ trait async_trait + Send + Sync，可 app 实现
                   ✓ skill 方法 default impl 覆盖（None / 空 vec / Err）
```

### 单元 2：skill 方法默认 impl 行为正确

```
单元              : discovery_backend_skill_defaults × §2.3
能力等级           : D（PASS）
判定依据          : MemoryDiscovery（无 skill）— 7 skill 方法全部默认 impl
                   返回 None / 空 vec / Err（"skills not supported"）
file:line         : crates/arf-mcp/src/discovery.rs:39-64 default impl
                   ✓ 7 skill 方法都正确返回"空"
                   ✓ load_resource_file + run_skill_tool 返回 Err 而非 panic
```

### 单元 3：自定义 backend tool 实际可执行

```
单元              : custom_discovery_tool_execution × §2.3
能力等级           : D（PASS）
判定依据          : 注册 2 个 tool (echo / reverse) → resolve_tool 拿到 ScriptTool
                   → execute() 端到端 spawn python 跑 stdin/stdout JSON
file:line         : crates/arf-e2e/tests/mcp_custom_discovery.rs:145-167
                   ✓ echo tool 返回 {"x": 42}
                   ✓ reverse tool 返回 {"rev": "olleh"}
                   说明 ScriptTool + DiscoveryBackend trait 解耦正确
```

### 单元 4：McpNode 与自定义 backend 集成（**F-010**）

```
单元              : custom_discovery_in_mcp_node × §2.3
能力等级           : **F（FAIL — 缺 primitive）**
判定依据          : McpNode 当前 3 个 public constructor：
                   - local(ns, root)            → FsDiscovery + LocalRuntime
                   - remote(ns, config)         → HttpDiscovery + RemoteRuntime
                   - local_with_runtime(ns, root, rt) → FsDiscovery + custom Runtime
                   **均无 `with_discovery(Box<dyn DiscoveryBackend>)` 构造器**
file:line         : crates/arf-mcp/src/node.rs:24-68 三个 constructor
                   ✓ DiscoveryBackend trait 公开（discovery.rs:32）
                   ✗ 无 with_discovery / with_boxed_discovery 直接构造入口
                   → app 想要"自定义 backend + 选 Runtime + 无 filesystem/HTTP"
                   必须强行走 FsDiscovery::scan 一个空 tmpdir + 不实用
                   或 HTTP（更不实用）
                   影响面：所有想注册内存 / SQL / 注册中心 / 配置中心的 backend 都需绕远路
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_discovery_backend × §2.3` | **D** | MemoryDiscovery 实现 trait 4 方法端到端 |
| `discovery_backend_skill_defaults × §2.3` | **D** | 7 skill 方法默认 impl 行为正确 |
| `custom_discovery_tool_execution × §2.3` | **D** | 自定义 backend tool 真能执行 |
| `custom_discovery_in_mcp_node × §2.3` | **F** | 缺 McpNode::with_discovery 构造器（F-010） |

---

## §D 病灶登记

### F-010：McpNode 缺 `with_discovery` 构造器

```
病灶 ID       : F-010（新增）
信条           : A2 正交性
Signal         : A2-S1（cross-import 强依赖：自定义 backend 与 McpNode 集成需要走 Fs/Http 中介）
触发情景       : §2.3（单 MCP 集成）
file:line      : crates/arf-mcp/src/node.rs:24-68
命中形态       : McpNode 三个 public constructor（local / remote / local_with_runtime）
                均内置具体 DiscoveryBackend 实现（FsDiscovery / HttpDiscovery），
                无 Box<dyn DiscoveryBackend> 通用构造器
                app 自定义 backend 只能借助 trait 直接用，但无法 drop-in 装入 McpNode
影响面         : app 想"纯内存 backend + LocalRuntime + 无 filesystem" 必须伪造 tmpdir
                → 绕远路、违规 framework 设计意图（DiscoveryBackend 本就是正交扩展点）
                → 期望 framework 暴露 pub fn with_discovery(
                      namespace: impl Into<String>,
                      discovery: Box<dyn DiscoveryBackend>,
                      runtime: Box<dyn RuntimeModule>,
                  ) -> Result<Arc<Self>, McpError>
                类似 local_with_runtime 形态但 discovery 也参数化
```

### 框架实际行为（按 spec §3.3 输出）

- `DiscoveryBackend` trait 全公开、Send + Sync、async_trait —— **D**（discovery.rs:32-65）
- skill 方法默认 impl 行为合理 —— **D**
- 自定义 backend 的 tool 真能跑（端到端 execute）—— **D**
- **McpNode 与自定义 backend 集成缺直接构造器** —— **F-010**

### 其他观察（不构成 lesion）

1. **`DiscoveryBackend` 11 方法数量大**（4 tool + 7 skill）—— 对实现者负担重。**建议**：考虑拆为两个 trait `ToolBackend` + `SkillBackend`，但当前形态也 work。
2. **`run_skill_tool` 与 `run_tool`（隐含）概念重叠**（discovery.rs:57-64）—— skill_tool 走 SkillIndex.run_tool。**不**算 lesion（语义不同）。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery）
- 9.5.2 既有 4 test pass（HttpDiscovery）
- 9.5.3 新增 4 test pass（custom backend trait）
- 综合：9.5.1-9.5.3 = 12 test，**全 pass**，1 新 F-lesion（F-010）
- 与 F-002（pool facade 不写 slot）、F-009（Queue(N) dead code）属于不同抽象层，互不影响

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个自定义 backend list_tools test | ✓ test1 pass |
| 1 个 skill 默认 impl test | ✓ test2 pass |
| 1 个自定义 backend 端到端 execute test | ✓ test3 pass |
| 1 个 McpNode 集成路径探查 test | ✓ test4 pass + 暴露 F-010 |
| 预期可能 0 新 F-lesion | ✗ **1 新 F-lesion（F-010）** |

> 结论：9.5.3 探查显示 framework `DiscoveryBackend` trait 本身端到端可工作，但 **McpNode 与自定义 backend 的正交集成缺直接构造器**（F-010）。建议后续 phase 加 `McpNode::with_discovery(ns, Box<dyn DiscoveryBackend>, Box<dyn RuntimeModule>)` 入口。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_custom_discovery.rs`（~180 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**1 新 F-lesion（F-010）**，按 prompt 要求**不修改** lesion-registry.md，仅在本 §D 登记
- 待 commit + push
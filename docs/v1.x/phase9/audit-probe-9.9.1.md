# audit-probe-9.9.1：双 agent 独立（无连接）端到端探查

> Task 9.9.1 探查产出 — **Framework 是否能让 2 个 Engine 在同一 Bus 上独立运行而不互相干扰？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.x（engine + 单/多 model）
> **本 task 探查：2 engine 同 bus 独立运行 + 同 provider 名下的 agent_id 碰撞**

---

## §A 探查环境

- working tree：HEAD `ccbbbcd`（task 9.4.3）+ uncommitted `crates/arf-e2e/tests/dual_agent_independent.rs`
- 测试文件：`crates/arf-e2e/tests/dual_agent_independent.rs`（4 test cases）
- 驱动：`TaggedMock`（name + text 字段，2 个实例不同 provider 名）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test dual_agent_independent -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.72s`**
- 关键运行输出：
  ```
  test two_engines_coexist_on_bus ... [test1] engine_node_ids=["engine/beta", "engine/alpha"]
  test two_engines_run_parallel_independent ... [test2] out_a="alpha-reply" out_b="beta-reply"
  test two_engines_no_cross_talk ... [test3] A round_count=1 B round_count=1
  test same_provider_engines_node_id_collision ... PrimaryBusConnect("node already connected: engine/alpha")
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/dual_agent_independent.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：2 engine 同 bus 共存

```
单元              : multi_engine_bus × §2.6
能力等级           : D（PASS）
判定依据          : EngineBuilder::new(vec![bus]).build(cfg) 调两次
                   → 各 Engine::new 内部 primary.connect(info, filter) 
                   → bus.graph() 见 2 engine node（agent_id 不同）
file:line         : crates/arf-engine/src/engine.rs:58-79
                   node_id = NodeId::new(format!("engine/{}", config.model.provider))
                   primary.connect(info, filter)  OK
                   ✓ 2 engine 共存 OK（用不同 provider 名）
```

### 单元 2：2 engine 各跑 chat

```
单元              : engine_run_parallel × §2.6
能力等级           : D（PASS）
判定依据          : engine_a.run / engine_b.run 顺序调
                   → 各 state.messages 独立累加
                   → session_id = info.node_id.to_string()（line 99）= "engine/{provider}"
                   → session 互不干扰
file:line         : crates/arf-engine/src/engine.rs:226-280 (run)
                   ✓ 2 engine 各自 round_count = 1，output 正确
```

### 单元 3：cross-talk 隔离

```
单元              : response_isolation × §2.6
能力等级           : D（PASS）
判定依据          : engine A 跑 model_call，response 带 correlation_id
                   engine B 的 filter 只收自己的 correlation_id 响应
                   bus graph 4 节点（2 engine + 2 model）无 cross-pollution
file:line         : crates/arf-bus/src/connection.rs:96-109 (send_response stamps cid)
                   crates/arf-engine/src/engine.rs:69-74 (filter = response types)
                   ✓ filter 隔离 + correlation_id 匹配，无 cross-talk
```

### 单元 4：同 provider agent_id 碰撞

```
单元              : agent_id_collision × §2.6
能力等级           : **F（FAIL：F-010）**
判定依据          : 2 个 EngineBuilder.build(make_cfg()) 都设 provider="alpha"
                   → Engine::new line 59 算 node_id = "engine/alpha"
                   → Bus::connect 报 AlreadyConnected("engine/alpha")
                   → 包成 BuildError::PrimaryBusConnect
                   → 第二个 engine 永远 build 不出来
file:line         : crates/arf-engine/src/engine.rs:59
                   crates/arf-engine/src/engine.rs:77-79 (PrimaryBusConnect)
                   ✗ framework 硬编码 node_id = "engine/{provider}"
                     不能在同 bus 上跑 2 个同 provider 的 agent
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `multi_engine_bus × §2.6` | **D** | 2 engine 各自独立 build OK |
| `engine_run_parallel × §2.6` | **D** | 2 engine 各自 round 跑 OK |
| `response_isolation × §2.6` | **D** | filter + cid 隔离，无 cross-talk |
| `agent_id_collision × §2.6` | **F** | 同 provider 第二 build 必报 PrimaryBusConnect |

---

## §D 病灶登记

### 新增 F-lesion：F-010 — Engine agent_id 硬编码 "engine/{provider}"，同 bus 无法跑 2 个同 provider 的 agent

**病灶 ID       : F-010**
**触发 task    : 9.9.1**
**触发探查    : test4 `same_provider_engines_node_id_collision`**

**症状**：
2 个 `EngineBuilder::new(vec![bus]).build(cfg)` 调 cfg.model.provider 相同（如都用 "alpha"）时，第二次 `build` 必报 `BuildError::PrimaryBusConnect("node already connected: engine/alpha")`。**同 provider 名 = 同 agent_id = bus 必拒**。

**file:line 锚点**：
- `crates/arf-engine/src/engine.rs:59` — `node_id: NodeId::new(format!("engine/{}", config.model.provider))`
- `crates/arf-engine/src/engine.rs:77-79` — `primary.connect(info, filter)` 报 `AlreadyConnected`
- `crates/arf-engine/src/engine.rs:79` — 错误包成 `BuildError::PrimaryBusConnect`

**实际影响**：
1. **多 agent 部署受限**：app 想在同 bus 上跑 2 个 deepseek agent（甚至只是同 provider 不同 persona），framework 拒绝
2. **测试隔离差**：同一 provider 重复用必须换 bus（multi-bus attach 是 workaround 但非根治）
3. **冲突失败模式 silent**：第一个 build 静默占 node_id，第二个 build 才报错，app 必须 try/catch 才知道有冲突

**根因**：
Engine 默认 `agent_id = "engine/{provider}"` 是 namespace 模式设计——把 provider 当唯一 namespace。但 **provider 是 model 维度（"alpha"/"beta"），不是 agent 维度**。一个 provider 下可有多 agent（不同 system_prompt / tools / subagents），它们目前都挤同一 node_id。

**修复建议**（不入本 task 范围）：
- `EngineBuilder` 加 `with_agent_id(NodeId)` 显式指定（app 提供 unique id）
- 或 `Engine::new` 加随机 suffix（"engine/{provider}-{uuid_prefix}"）
- 或 `AgentConfig` 加 `agent_id: Option<String>` 字段

**A4 信条命中**：engine_id 生成是 framework 单点决定，app 不可覆盖。违反"app 提供声明，framework 解决路径"。

### 注意事项（潜在 issue，非 lesion）

1. **`session_id = info.node_id.to_string()`**（engine.rs:99）—— 同样撞 F-010 风险。F-010 修后 session_id 也应自动分散。
2. **EngineBuilder 缺 `with_session_id()`**（搜无）—— `Engine::install_session_store` 私有。app 不能自己设 session_id。

---

## §E 探查回归

- 9.1-9.5.x 既有 test 全 pass（前序 task 未触及 engine_id 命名）
- 9.9.1 新增 4 test pass
- 综合：4 新 test，1 新 F-lesion（F-010）

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 2 engine 同 bus graph 看到 2 engine node | ✓ test1 pass（"engine/alpha" + "engine/beta"） |
| 2 engine 各自跑 user_input 不干扰 | ✓ test2 pass（独立 session + state） |
| 2 engine 各自 round_count=1 + state 不污染 | ✓ test3 pass |
| 同 provider 撞 agent_id 暴露 lesion | ✓ F-010（test4 暴露 `PrimaryBusConnect` 错误） |

> 结论：9.9.1 探查显示 framework **支持多 engine 独立运行**（D 级），但 **agent_id 命名策略是 F 级病灶**（F-010）。后者直接影响 9.9.2-9.9.7 的多 agent 拓扑——本 task 的 lesion 必须**前置修复**或**显式绕过**才能让后续 task 跑。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/dual_agent_independent.rs`（~320 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 待 task 9.9.x 跑完统一登记）
- 待 commit + push

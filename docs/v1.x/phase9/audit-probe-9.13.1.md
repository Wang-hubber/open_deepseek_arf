# audit-probe-9.13.1：Node 掉线（OnMemberFailedAction::FailSession）端到端探查

> Task 9.13.1 探查产出 — **Framework node_offline 时是否真正调用 `OnMemberFailedHandler::handle()`？**
> 父 task doc：`docs/v1.x/phase9/task-9.13.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.1.5（Bus 异常）
> **本 task 探查：Engine 端 node_offline → handler 真实调用路径**

---

## §A 探查环境

- working tree：HEAD `f4e53fd`（task 9.12.5）+ uncommitted `crates/arf-e2e/tests/node_offline_fail_session.rs`
- 测试文件：`crates/arf-e2e/tests/node_offline_fail_session.rs`（4 test cases）
- 驱动：`FailSessionHandler` (FailSession) / `CountingHandler` (count) / `Bus node_offline` baseline
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test node_offline_fail_session -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 2.46s`**
- 关键运行输出：
  ```
  test bus_only_node_offline_baseline ... [test3] saw node_offline from ghost ✓
  test handler_invocation_count_after_offline ... [test2] handler 多次 invoke count=3 OK ✓
  test node_offline_triggers_handler_fail_session ... [test1] handler invocations: [] (F-011 病灶证据)
  test engine_node_offline_does_not_call_handler_finding_f011 ...
    [test4] handler 实际被调次数 = 0 (F-011 finding: 未实现)
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/node_offline_fail_session.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Bus node_offline 基础（baseline）

```
单元              : bus_node_offline_baseline × §2.0
能力等级           : D（PASS）
判定依据          : drop handle → heartbeat timeout → node_offline broadcast
                   observer subscribe 收到 node_offline from ghost ✓
file:line         : crates/arf-bus/src/heartbeat.rs:19-55 heartbeat + node_offline
                   crates/arf-bus/src/connection.rs:694-710 disconnect broadcast
                   ✓ Bus 单独 node_offline 端到端 work
```

### 单元 2：handler 直接 invoke (trait 边界)

```
单元              : handler_direct_invoke × §2.0
能力等级           : D（PASS）
判定依据          : handler.handle() 调 3 次 → count=3 → 返回 FailSession
file:line         : crates/arf-engine/src/config.rs:57-59 trait
                   ✓ trait 边界端到端 work
```

### 单元 3：Engine + node_offline + handler 实证

```
单元              : engine_node_offline_handler × §2.0
能力等级           : F（FAIL — handler 真实调用未实现）
判定依据          : Engine with on_member_failed=Some(handler) + run
                   + drop model node handle → 等 heartbeat timeout → node_offline
                   → handler invocations = [] (空)
                   **handler 未被调**——framework 缺真实调用路径
file:line         : crates/arf-engine/src/engine.rs:81-92
                   // 6.7: Spawn lifecycle listener that invalidates the DiscoveryCache
                   // when nodes come online or go offline.
                   let mut lifecycle_rx = primary.subscribe();
                   tokio::spawn(async move {
                       while let Ok(m) = lifecycle_rx.recv().await {
                           if m.msg_type == "node_online" || m.msg_type == "node_offline" {
                               cache_for_listener.invalidate();
                               // ← 仅 invalidate cache, **未**调 on_member_failed.handle()
                           }
                       }
                   });
                   ✓ node_offline → cache invalidate
                   ✗ on_member_failed.handle() **未**被调 (F-011 病灶)
```

### 单元 4：handler FailSession action 验证

```
单元              : handler_fail_session_action × §2.0
能力等级           : C（partial — trait 边界 OK, 真实调用缺失）
判定依据          : FailSessionHandler::handle() 返回 FailSession OK（unit-level）
                   但 Engine 真实路径不调它 (见单元 3)
file:line         : crates/arf-engine/src/config.rs:48 FailSession 变体
                   ✓ trait 返回 FailSession OK
                   ✗ Engine 端无真实调用点
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `bus_node_offline_baseline` | **D** | Bus 单独 node_offline 端到端 OK |
| `handler_direct_invoke` | **D** | handler trait 边界 + 3 actions 直接 invoke OK |
| `engine_node_offline_handler` | **F** | Engine 真实调用路径缺失 (F-011 病灶) |
| `handler_fail_session_action` | **C** | trait 返回 FailSession OK，但 Engine 路径缺失 |

---

## §D 病灶登记

**本 task 新增 1 个 F-lesion**：

### F-011 — Engine 不在 node_offline 时调用 `OnMemberFailedHandler`

```
病灶 ID       : F-011
类别         : F（framework 缺真实调用路径，扩展点 declared but unwired）
Signal         : 缺 handler invocation（spec §1.2 E 等级 = "扩展可达"——
                handler trait 存在 + EngineConfig 接受，但缺真实触发路径）
触发情景       : §2.0（异常 / 掉线 / 容错）
首次登记       : audit-probe-9.13.1.md §D
状态           : OPEN
file:line      : crates/arf-engine/src/engine.rs:81-92
                // 6.7: Spawn lifecycle listener that invalidates the DiscoveryCache
                // when nodes come online or go offline.
                let mut lifecycle_rx = primary.subscribe();
                tokio::spawn(async move {
                    while let Ok(m) = lifecycle_rx.recv().await {
                        if m.msg_type == "node_online" || m.msg_type == "node_offline" {
                            cache_for_listener.invalidate();
                            // ↑ 仅 invalidate cache，未调 self.config.on_member_failed
                        }
                    }
                });
                实证测试: node_offline_triggers_handler_fail_session
                - Engine with on_member_failed = Some(handler)
                - drop model node handle → heartbeat timeout → node_offline broadcast
                - handler invocations = []  ← **handler 未被调**
                实证测试: engine_node_offline_does_not_call_handler_finding_f011
                - 同上，handler 实际被调次数 = 0
                源注释: crates/arf-engine/src/tests.rs:2239
                // 6.8 简化：lifecycle listener 只 invalidate cache；handler invocation 留 6.x
                ↑ framework 内部已承认此缺口
命中形态       : **L8 `custom_member_failed_handler` declared but unwired**
                - capability-matrix §1.1 L8 列 `OnMemberFailedHandler` 为扩展点
                - arf-engine 提供 trait (config.rs:57) + EngineConfig field (config.rs:25)
                - Engine build 接受 cfg.on_member_failed (config.rs:25, 实证 test4)
                - **但 Engine 运行时不调 handler.handle()** — handler 配置被 ignore
                - 后果：
                  1) app 端写 `cfg.engine.on_member_failed = Some(my_handler)` 无效
                  2) 任何 node_offline 场景（如 9.1.5 bus_exceptions）silent
                  3) app 端必须自订阅 bus 自己实现 lifecycle 监听
                  4) 与 6.8 task 注释一致——"handler invocation 留 6.x"，
                     但 6.x 至今未实现
影响面         : 1) 9.1.5 探查的 node_offline 场景 app 端**没有 framework 提供
                   的处理路径**——必须绕过 framework
                2) capability-matrix L8 的 `OnMemberFailedHandler` 扩展点
                   **当前不可用** (handler 注册被 ignore)
                3) 与 9.12.1 F-010 (DiscoveryBackend 缺 public 入口) 同类——
                   这次是"extension point declared + builder accepts but no invoke"
                4) 6.x task 留的"handler invocation" 实现缺口——
                   framework 未完成自己的 task 承诺
修复方向       : 方案 A（最小改动，5-10 行）：lifecycle listener 在 node_offline
（供参考）      分支额外调 self.config.on_member_failed.handle()
                ```rust
                if m.msg_type == "node_offline" {
                    cache_for_listener.invalidate();
                    if let Some(handler) = &self.config.engine.on_member_failed {
                        let action = handler.handle(&self.agent_id, &m.from, "node_offline");
                        match action {
                            MemberFailedAction::FailSession => { /* emit fail signal */ }
                            MemberFailedAction::Retry { .. } => { /* wait + retry */ }
                            MemberFailedAction::SwitchTo { .. } => { /* update route */ }
                        }
                    }
                }
                ```
                方案 B（重构）：把 handler invocation 提取到独立方法
                `on_member_failed(member, reason)`，lifecycle listener 调用
                方案 C：保留当前 cache invalidate 路径，handler invocation
                由 ReAct loop 在每 model_call 前主动 check
                建议 A（最少改动 + 最直接）。
Engine 层蔓延  : N/A（engine 自身就是病灶所在层）
复现命令       : grep -n 'on_member_failed' crates/arf-engine/src/engine.rs
                # 无引用（除 config field 读）
                cargo test -p arf-e2e --test node_offline_fail_session -- --nocapture --test-threads=1 2>&1 | grep 'handler'
                # [test1] handler invocations: []
                # [test4] handler 实际被调次数 = 0
```

---

## §E 探查回归

- 9.1.5（bus_exceptions）3 test pass，未受本 task 影响
- 9.12.5（custom_member_failed_handler）4 test pass，未受本 task 影响
- 9.13.1 新增 4 test pass（探查性，记录现状）
- 综合：9.13.1 = 4 test，**4 pass, 1 新 F-lesion (F-011)**
- F-001~F-010 + F-011 与本 task 关联
- **F-011 是 phase 9 第 11 个 F-lesion**：
  Engine 缺 `OnMemberFailedHandler::handle()` 真实调用路径

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 node_offline 触发 handler FailSession test | ✓ test1 pass (handler 未被调 = F-011 证据) |
| 1 个 default None + node_offline test | △ test3 baseline OK（不依赖 handler） |
| 1 个 Bus node_offline baseline test | ✓ test3 pass |
| 1 个 handler invoke count test | ✓ test2 pass (trait 边界 OK) |
| 1 个真实 Engine node_offline 实证 test | ✓ test4 pass (F-011 病灶记录) |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion (F-011)** |

> 结论：9.13.1 探查发现 framework 缺 `OnMemberFailedHandler::handle()` 真实调用路径——L8 扩展点 `custom_member_failed_handler` declared but unwired。F-011 病灶证据：test1 / test4 实证 handler 在 node_offline 后**未被调**，invocations 始终为 `[]`。framework 内部注释（tests.rs:2239）已承认此缺口："handler invocation 留 6.x"。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/node_offline_fail_session.rs`（~290 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.13.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-011 在本 task audit-probe §D 首次登记，未追加到 lesion-registry.md，遵循任务约束）
- 待 commit + push

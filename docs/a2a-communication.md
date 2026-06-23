# A2A Communication — Subagents & Teammates

ARF 提供两种 Agent-to-Agent 通讯机制：**Subagents**（一次性临时子代理）和 **Teammates**（持久化的对等队友）。二者互补，覆盖不同的协作场景。

## 机制对比

| | a2a_subagents | a2a_teammates |
|---|---|---|
| **关系** | 父子 (hierarchical) | 对等 (peer-to-peer) |
| **生命周期** | 一次性，完成即销毁 | 整个 session，park/wake 循环 |
| **创建方式** | `delegate_task(agent="name", task="...")` | `send_peer_message(to="session_id", message="...")` |
| **寻址** | agent name → YAML 配置 | session_id (精确寻址) |
| **结果回传** | delegator.complete → before_model 注入 | task_complete → forward_reply → bus.send → inbox 注入 |
| **并发** | 最多 max_concurrent 个子 agent 并行 | N 个 agent 各自独立 harness |
| **park** | 父等子 (delegator dispatch + before_model park) | 所有 agent 在 after_round park，peer/user 唤醒 |
| **工具** | delegate_task, queue_status, cancel_task, cancel_held, resolve_conflict | send_peer_message, cancel_peer_task |
| **状态** | child_tasks 记录在 parent state store | SessionIndex 管理 group 级别的 session 索引 |

## 互斥规则

```
delegate_task  = 临时一次性 worker（生完即焚）
send_peer_message = 持久队友（整个 session 存活）

如果你有 teammates → 用 send_peer_message
如果你需要临时帮手 → 用 delegate_task
不要同时用两个工具做同一件事
```

## 案例 1: subagents — 代码审查

**场景**: 用户让主 agent 审查代码库，主 agent 派发 3 个子 agent 并行审查。

```
用户: "审查整个项目，找出所有安全漏洞"

主 agent:
  → delegate_task(agent="security_reviewer", task="审查 auth/ 目录")
  → delegate_task(agent="security_reviewer", task="审查 api/ 目录")
  → delegate_task(agent="code_reviewer", task="审查 src/ 目录")

子 agent 1 (security_reviewer / auth/):
  session: parent_sid--task_1
  → read auth/login.py → grep → 发现 XSS
  → task_complete(result="auth/login.py: XSS on line 42")

子 agent 2 (security_reviewer / api/):
  session: parent_sid--task_2
  → read api/handler.py → 发现 SQLi
  → task_complete(result="api/handler.py: SQLi on line 108")

子 agent 3 (code_reviewer / src/):
  session: parent_sid--task_3
  → read src/* → 无问题
  → task_complete(result="src/: no issues found")

主 agent (等所有子 agent 完成):
  before_model → delegator.get_pending()
  → 注入 3 条结果:
    [A2A] Task task_1 completed: auth/login.py: XSS on line 42
    [A2A] Task task_2 completed: api/handler.py: SQLi on line 108
    [A2A] Task task_3 completed: src/: no issues found
  → 汇总报告给用户

数据流:
  parent ──dispatch──→ delegator ──runner──→ harness(child_sid)
  child task_complete → runner detects → delegator.complete()
  parent before_model → delegator.get_pending() → ctx.agent.input()
```

## 案例 2: teammates — 团队协作

**场景**: PM、Dev、Data 三人团队协作做一个数据分析仪表板。

```
配置:
  members:
    - {role: pm, agent_name: pm_agent, entry_point: true}
    - {role: dev, agent_name: dev_agent}
    - {role: data, agent_name: data_agent}

初始化:
  pm.init  → SessionIndex 创建 group → bus_registry[pm_sid] = pm_bus
  dev.init → bus_registry[dev_sid] = dev_bus → roster 注入 → park
  data.init → bus_registry[data_sid] = data_bus → roster 注入 → park

               ┌──────────────────────────────────────┐
               │           Bus Registry               │
               │  pm_sid   → pm_bus   ([pm inbox])    │
               │  dev_sid  → dev_bus  ([dev inbox])   │
               │  data_sid → data_bus ([data inbox])  │
               └──────────────────────────────────────┘
                 get_bus(to) ↑          ↓ own bus

用户: "分析销售数据，做一个仪表板"

PM (wakes from park via user input):
  → send_peer_message(to="team__dev", type="task", message="写一个 dashboard.html")
    → get_bus("team__dev") → dev_bus.send() → dev's inbox + _pending_replies 注册
  → send_peer_message(to="team__data", type="task", message="扫描 test_data/ 统计销量")
    → get_bus("team__data") → data_bus.send() → data's inbox + _pending_replies 注册
  → park_after_send → park (等回复)

Dev (parked, woken by bg task on own bus):
  _peer_wait_loop → dev_bus.wait_for_message("team__dev") → 收到 PM 的 task
  → resolve_wait → inject → model: "收到任务，开始写仪表板"
  → write_file("dashboard.html", "<!DOCTYPE html>...")
  → task_complete(result="dashboard.html 已完成")
  → forward_reply → write_peer_result() → get_bus(pm_sid).send(reply)
  → park (idle)

Data (parked, woken by bg task on own bus):
  _peer_wait_loop → data_bus.wait_for_message("team__data") → 收到 PM 的 task
  → resolve_wait → inject → model: "开始扫描数据"
  → read test_data/* → 统计销量
  → task_complete(result="华东 490, 华北 932, 华南 845")
  → forward_reply → get_bus(pm_sid).send(reply)
  → park (idle)

PM (own bus wakes, resolves park):
  → pm_bus.receive() → 收到 Dev 的 reply: "dashboard.html 已完成"
  → pm_bus.receive() → 收到 Data 的 reply: "华东 490, 华北 932, 华南 845"
  → resolve_wait → inject → model 汇总 → 报告给用户

数据流:
  PM ──get_bus(dev)──→ dev_bus ──→ Dev inbox → bg task → resolve_wait
  Dev task_complete → forward_reply ──get_bus(pm)──→ pm_bus ──→ PM inbox → bg task → resolve_wait
  PM park ←→ user interrupt via resolve_wait (随时可打断, 取消任务, 做别的事)
```

## 案例 3: teammates 中的中断与取消

```
PM park 中 (等 Dev + Data 回复)...

用户: "Data 不用做了，Dev 的仪表板改一下颜色"
CLI → pm_harness.resolve_wait(pm_wait_id, inject_message={role:"user", content:"..."})

PM wakes:
  → cancel_peer_task(correlation_id=data_task)
    → _pending_replies.pop → get_bus(data_sid).send(cancel) → data_harness.resolve_wait
  → send_peer_message(to="team__dev", message="仪表板用蓝色主题")
    → get_bus("team__dev") → dev_bus.send()
  → park (继续等 Dev)

Data wakes (被 cancel 唤醒):
  → inject: "[Peer cancel from team__pm] ..."
  → model: "任务被取消，停止工作"
  → after_round → peer_park (idle)
```

## 案例 4: 混合使用 — 队友派发子代理

```
PM → Dev: "审查 auth/ 的安全性"
Dev wakes:
  → 需要并行扫描多个文件 → delegate_task(agent="security_reviewer", task="扫描 auth/login.py")
  → delegate_task(agent="security_reviewer", task="扫描 auth/middleware.py")
  → 子 agent 完成 → Dev 收到结果 → 汇总
  → task_complete(result="auth/: 发现 3 个问题")
  → forward_reply → bus.send(to=pm_sid)
  → peer_park
```

每个 teammate 可以再派发 subagents，形成层级。

## 选择指南

```
                      ┌─────────────────────────────────────┐
                      │        需要做什么？                 │
                      └─────────────────────────────────────┘
                          │                    │
                    一次性任务            持久协作
                    (生完即焚)          (整个 session)
                          │                    │
                    delegate_task      send_peer_message
                          │                    │
              ┌──────────┼──────────┐   ┌──────┼──────┐
              │          │          │   │      │      │
           并行审查   代码生成   数据分析  PM↔Dev  Dev↔Data  PM↔Data
```

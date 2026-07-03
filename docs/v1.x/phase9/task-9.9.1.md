# 任务 9.9.1：双 agent 独立（无连接）

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 1 task（依赖 9.2.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.x（engine + 单 model + multi model）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.x 探查了 single-engine 场景。9.9.x 进入 **multi-engine 拓扑**——一个 bus 上跑 2 个 engine（每个 engine = 1 个 agent）。

**9.9.1 是 baseline**——两个 engine 各自独立运行，**无任何跨 agent 通信**。本 task 探查：
- 2 个 engine 在同一 bus 上能各自跑通？
- engine_id / session_id 是否冲突？
- bus graph 能否看到 2 个 engine node？
- 同一 bus 两条 engine 流的 model_call / model_response 互不干扰？

**Framework 现状**（待探查确认）：
- `EngineBuilder::new(vec![bus.clone()])` —— 同 bus 多个 engine
- 每个 engine 的 `agent_id = "engine/{provider}"` —— **可能相同**（隐患）
- engine filter 只看 response types，不收 peer_message

**关键探查问题**（不预设答案）：
1. 两个 engine 同一 bus，**agent_id 是否冲突**？（同一 provider 名会撞 id）
2. 两个 engine 跑 user_input，bus graph 看到几个 engine node？
3. 两个 engine 各跑自己的 round，session_id 是否独立？
4. 同一 bus 同 provider 的 model_call，**engine A 的 response 会否被 engine B 误收**？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-engine/src/tests.rs`：单 engine 单 bus 的 1-on-1 tests
- `crates/arf-e2e/tests/multi_model.rs`：单 engine + 多 model adapter
- **本 task 不重复**：单 engine 行为
- **本 task 聚焦**：多 engine 同 bus 独立运行，无 cross-talk

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`dual_agent_independent.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `two_engines_coexist_on_bus` | 1 bus + 2 EngineBuilder.build()，bus.graph() 看到 2 个 engine node，agent_id 各异 |
| 2 | `two_engines_run_parallel_independent` | 2 engine 各跑自己的 user_input，session_id / state.messages 独立，输出正确 |
| 3 | `two_engines_no_cross_talk` | engine A 跑 model_call，response 不会被 engine B 误收（filter + correlation_id 隔离） |
| 4 | `same_provider_engines_collision_check` | 同 provider 名 2 engine，agent_id 是否真的撞（验证"engine/{provider}"格式下冲突） |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub.*fn build\|pub.*fn new\|agent_id" crates/arf-engine/src/engine.rs
grep -n "format!" crates/arf-engine/src/engine.rs | grep -i engine
```

逐行解释：
- `EngineBuilder::build()` → 调 `Engine::new(buses, config, registry)`
- `Engine::new` line 59：`node_id = NodeId::new(format!("engine/{}", config.model.provider))`
- 同一 provider 名 → 同 node_id → bus connect 报 AlreadyConnected

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test dual_agent_independent -- --nocapture --test-threads=1 2>&1 | tee /tmp/dual_agent_independent_run.log
```

逐行解释：
- 4 test 应跑通（mock + tmpdir）
- test4 同 provider 冲突可能暴露 lesion —— 记 §D

**Read `/tmp/dual_agent_independent_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：engine_id 命名是否 atomic？（"engine/{provider}" 含硬编码前缀）
- A2：两个 engine 的 model_call 路由是否隔离？
- A3：session_id 是否唯一？（"engine/{provider}" → 同 provider 冲突）
- A4：bus 资源是否复用一处？

**C. 输出**：`audit-probe-9.9.1.md`。

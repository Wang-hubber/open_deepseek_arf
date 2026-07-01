# 任务 6.7：Discovery 路由缓存

> Phase 6 — Engine 核心实现（§9.B）第七项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.3.1 / §2.P3 / §2.G11
> 前置：`task-6.5-checkpoint-system` ✅ / `task-6.6-wait-event-park-resume` ✅

## 设计思路

6.5 实测每次 Checkpoint 评估都调 `bus.graph().nodes` 查 Capability 匹配——每次 ReAct 步都全量扫描节点表。在 App 节点数 > 20 或 Checkpoint 规则 > 5 时是性能瓶颈。6.7 在 Engine 侧加 `DiscoveryCache`：Capability → Vec<NodeId> 映射，命中直接返回；缓存生命周期绑定 Bus lifecycle signal（node_online / node_offline）。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Cache 位置 | `Engine` 字段 `discovery_cache: DiscoveryCache`（独立 struct） | 与 State 解耦（cache 是 Engine 运行时优化，不是用户可见状态）；不在 Serialize 范围 |
| Cache key | `Capability`（含 requirements Vec<(String,String)>） | 同 Capability 不区分 Strict/Dynamic——确定性 |
| Cache value | `Vec<NodeId>`（snapshot 时复制） | 防止后续 graph 变更影响已派生的 recipient list |
| 失效时机 | 收到 Bus `node_online` / `node_offline` 信号时清空整表 | 简单且正确（细粒度失效需要 capability 反向索引，得不偿失）；6.x 可优化为按 Capability 失效 |
| Strict route 不缓存 | `resolve_route` Strict 分支直接 `route.ids.clone()` | Strict 显式指定 NodeId，不需要 graph 查询 |
| 并发安全 | `parking_lot::Mutex` 或 `std::sync::Mutex`（单线程访问足够） | Engine 主循环单线程，无需 async 锁 |
| 测试隔离 | 每个测试 new cache，不跨测试共享 | 避免状态泄漏 |
| Subscribe lifecycle | Engine 启动时 `bus.subscribe()` 过滤 `node_online`/`node_offline` 后台 task 调 `cache.invalidate()` | 复用 broadcast channel |

### 不在 6.7 范围

- 细粒度按 Capability 失效（保留整表清空）
- TTL 过期机制（依赖 Bus signal 已足够）
- 缓存命中率 metrics

### 关键既有材料（6.5/6.6 已实现）

- `cp_eval::resolve_route(route, graph_nodes) -> Vec<NodeId>`（`crates/arf-engine/src/checkpoint.rs`）
- `cp_eval::evaluate(state, trigger, rules, routes, graph_nodes) -> Vec<CheckpointMsg>` 接收 `&[NodeInfo]`
- `Engine.primary_bus.graph()` 在 `evaluate_and_dispatch` 中调用
- `Bus.subscribe()` 返回 `broadcast::Receiver<Message>`（含 lifecycle signal）

## 代码实现

### `crates/arf-engine/src/checkpoint.rs` 改动

把 `resolve_route` 拆成两层：底层纯函数 `resolve_route_pure`（已被测）+ 上层缓存感知 `resolve_route_cached`。把 `evaluate` 接受 `&DiscoveryCache` 参数。

```rust
//! Checkpoint evaluation + Route resolution (Phase 6 §2.P3 / task-6.5/6.7).

use std::collections::HashMap;
use std::sync::Mutex;

use arf_core::{
    ActionMessage, Capability, Checkpoint, CheckpointRule, NodeId, NodeInfo, Route,
};

use crate::config::AgentConfig;

/// A built Checkpoint message + its resolved recipients.
pub struct CheckpointMsg {
    pub msg: Box<dyn ActionMessage>,
    pub recipients: Vec<NodeId>,
    pub rule_name: String,
}

/// Capability → Vec<NodeId> cache. Phase 6 task 6.7.
///
/// Invariants:
/// - Strict routes bypass this cache (see resolve_route).
/// - On `node_online`/`node_offline` signal, caller invokes `invalidate()`.
/// - Cache is internal to Engine — not serialized as part of State.
pub struct DiscoveryCache {
    inner: Mutex<HashMap<Vec<(String, String)>, Vec<NodeId>>>,
}

impl DiscoveryCache {
    pub fn new() -> Self {
        Self { inner: Mutex::new(HashMap::new()) }
    }

    /// Look up recipients for a Capability; populate cache on miss.
    pub fn get_or_compute(&self, cap: &Capability, graph_nodes: &[NodeInfo]) -> Vec<NodeId> {
        let key = cap.requirements.clone();
        let mut guard = self.inner.lock().expect("DiscoveryCache mutex poisoned");
        if let Some(cached) = guard.get(&key) {
            return cached.clone();
        }
        let resolved: Vec<NodeId> = graph_nodes
            .iter()
            .filter(|n| capability_matches(n, cap))
            .map(|n| n.node_id.clone())
            .collect();
        guard.insert(key, resolved.clone());
        resolved
    }

    /// Clear all cached entries (called on node_online / node_offline).
    pub fn invalidate(&self) {
        self.inner.lock().expect("DiscoveryCache mutex poisoned").clear();
    }

    /// Number of cached entries (test hook).
    pub fn len(&self) -> usize {
        self.inner.lock().expect("DiscoveryCache mutex poisoned").len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for DiscoveryCache {
    fn default() -> Self { Self::new() }
}

/// Resolve a `Route` to a list of recipient NodeIds. Phase 6 task 6.5/6.7.
///
/// - Strict: returns the explicit NodeIds as-is (no cache lookup).
/// - Discovery: consults `cache`; on miss, queries `graph_nodes` and caches result.
pub fn resolve_route(route: &Route, graph_nodes: &[NodeInfo], cache: &DiscoveryCache) -> Vec<NodeId> {
    match route {
        Route::Strict(ids) => ids.clone(),
        Route::Discovery(cap) => cache.get_or_compute(cap, graph_nodes),
    }
}

/// Pure (uncached) resolution — kept for unit testing the matching predicate.
pub fn resolve_route_pure(route: &Route, graph_nodes: &[NodeInfo]) -> Vec<NodeId> {
    match route {
        Route::Strict(ids) => ids.clone(),
        Route::Discovery(cap) => graph_nodes
            .iter()
            .filter(|n| capability_matches(n, cap))
            .map(|n| n.node_id.clone())
            .collect(),
    }
}

fn capability_matches(node: &NodeInfo, cap: &Capability) -> bool {
    cap.requirements.iter().all(|(k, v)| {
        node.capabilities
            .get(k)
            .and_then(|x| x.as_str())
            == Some(v.as_str())
    })
}

/// Evaluate a Checkpoint position. Phase 6 task 6.5/6.7.
pub fn evaluate(
    state: &arf_core::State,
    trigger: Checkpoint,
    rules: &[CheckpointRule],
    routes: &HashMap<String, Route>,
    graph_nodes: &[NodeInfo],
    cache: &DiscoveryCache,
) -> Result<Vec<CheckpointMsg>, crate::error::RunError> {
    use crate::error::RunError;

    let mut out = Vec::new();
    for rule in rules {
        if rule.trigger != trigger {
            continue;
        }
        if !rule.fires(state) {
            continue;
        }
        let msg = rule.build_msg(state);
        let msg_type = msg.msg_type();
        let route = routes.get(msg_type).ok_or_else(|| {
            RunError::UndeclaredMsgType { msg_type: msg_type.to_string() }
        })?;
        let recipients = resolve_route(route, graph_nodes, cache);
        out.push(CheckpointMsg {
            msg,
            recipients,
            rule_name: rule.name.clone(),
        });
    }
    Ok(out)
}
```

### `crates/arf-engine/src/engine.rs` 改动

1. `Engine` 新增 `discovery_cache: DiscoveryCache` 字段。
2. `evaluate_and_dispatch` 传 `&self.discovery_cache` 给 `cp_eval::evaluate`。
3. 后台 task 订阅 bus 的 `node_online`/`node_offline`，调 `cache.invalidate()`。

```rust
use crate::checkpoint::{self as cp_eval, DiscoveryCache};

pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    handle: NodeHandle,
    primary_bus: Arc<arf_bus::Bus>,
    discovery_cache: DiscoveryCache,  // 6.7 新增
    system_prompt: String,
}

impl Engine {
    pub(crate) async fn new(buses: Vec<arf_bus::Bus>, config: AgentConfig, system_prompt: String) -> Result<Self, BuildError> {
        // ... (existing setup)
        let discovery_cache = DiscoveryCache::new();

        // Spawn lifecycle listener that invalidates cache on node_online/offline.
        let cache_for_listener = ???;  // Arc<DiscoveryCache>? — see impl notes
        let mut lifecycle_rx = primary.subscribe();
        tokio::spawn(async move {
            while let Ok(m) = lifecycle_rx.recv().await {
                if m.msg_type == "node_online" || m.msg_type == "node_offline" {
                    cache_for_listener.invalidate();
                }
            }
        });

        Ok(Self { /* ..., */ discovery_cache, /* ... */ })
    }
}
```

**实现注意**：`DiscoveryCache` 当前用 `std::sync::Mutex`，所有权移入 listener task 用 `Arc<DiscoveryCache>`。

## 测试

`crates/arf-engine/src/tests.rs` 加 6.7 章节：

```rust
// ── Phase 6 task 6.7 — Discovery Cache (7 tests) ────────────────

// [构造] DiscoveryCache::new 初始为空
#[test]
fn cache_new_is_empty() { ... }

// [方法] get_or_compute miss → 计算 + 缓存；hit → 直接返回
#[test]
fn cache_miss_then_hit() { ... }

// [方法] 多次不同 Capability → 多个 cache entry
#[test]
fn cache_multiple_capabilities() { ... }

// [方法] invalidate 清空所有 entry
#[test]
fn cache_invalidate_clears_all() { ... }

// [路径] Strict route 不读 cache（直接返回 ids）
#[test]
fn strict_route_bypasses_cache() { ... }

// [性能] graph 节点变化后再 hit → 仍是旧结果（cache 不重新查）
#[test]
fn cache_returns_stale_after_graph_mutation_until_invalidate() { ... }

// [集成] node_offline signal 触发 cache invalidation
#[tokio::test]
async fn node_offline_signal_invalidates_cache() { ... }
```

7 个测试；与 6.6 的 9 个 + 6.5 的 16 个 + 6.4 的 4 个 + 6.3 的 11 个 + 6.2 的 4 个 + 6.1 的核心类型测试一起，6.7 后 engine 总计 ~48 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| arf-engine DiscoveryCache + resolve_route | 7 |
| arf-engine WaitEvent / WaitStrategy（已存在） | 9 |
| **合计** | **16**（其中 7 个新增） |

---

## 实现后实际发现

### 与初稿的差异

1. **`DiscoveryCache` 用 `std::sync::Mutex` 而非 `parking_lot`**：避免新增依赖；Engine 主循环单线程访问，std::Mutex 性能足够。
2. **`Arc<DiscoveryCache>` 共享给 lifecycle listener**：listener task 在 `Engine::new` 启动，spawn 时 clone Arc；Engine 字段也持 Arc。
3. **Cache key = `Vec<(String, String)>`**：Capability.requirements 的 clone 即 key；HashMap 默认 hasher 对 Vec OK（Vec 实现 Hash）。
4. **`resolve_route_pure` 保留作为底层 helper**：测试和未来无需 cache 的场景（如一次性 CLI 查询）可用。
5. **lifecycle listener 通过 `bus.subscribe()` 过滤 `node_online`/`node_offline`**：复用现有 broadcast channel，不引入新连接。

### 实现期间 bug

1. **旧 API 调用方未更新**：tests.rs 中 4 处调用旧签名 `resolve_route(2 args)` / `evaluate(5 args)`，编译报错。修复：全部更新为新签名（加 `&DiscoveryCache` 参数）。

### 实际测试结果

```
cargo test --workspace
test result: ok. 52 passed; 0 failed
test result: ok. 91 passed; 0 failed
test result: ok. 14 passed; 0 failed
test result: ok. 161 passed; 0 failed
test result: ok. 48 passed; 0 failed  (arf-engine: 6.7 新增 7 个测试 → 累计 48)
test result: ok. 204 passed; 0 failed
test result: ok. 12 passed; 0 failed
test result: ok. 19 passed; 0 failed
test result: ok. 70 passed; 0 failed
合计 671 passed; 0 failed
```

### 6.7 输出

- `crates/arf-engine/src/checkpoint.rs` 扩展：
  - 新增 `DiscoveryCache` struct
  - `resolve_route` 接受 `&DiscoveryCache` 参数
  - 新增 `resolve_route_pure`（纯函数，供测试和 future use）
  - `evaluate` 接受 `&DiscoveryCache` 参数
- `crates/arf-engine/src/engine.rs`：
  - `Engine.discovery_cache: DiscoveryCache` 字段
  - `Engine::new` 启动 lifecycle listener task
  - `evaluate_and_dispatch` 传 cache 到 `cp_eval::evaluate`

### 下一步：6.8

**6.8 EngineBuilder API**：标准 CheckpointRule 构造器（every_n_rounds / when_context_over）、OnMemberFailedHandler 完整实现、build() 校验、ResponseProcessor 表默认 dispatch。
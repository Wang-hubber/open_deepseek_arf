# 任务 1.7：健康图

> Phase 1 — Bus 消息总线第七项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.3 nodes map 就绪

## 设计思路

`Bus::graph()` 返回 `BusGraph` 快照——节点列表 + 消息计数 + 运行时间。外部监控和调试用。

一行实现：读 nodes map → 收集 NodeInfo → 组装 BusGraph。

---

## 代码实现

### `crates/arf-bus/src/graph.rs`（新文件）

```rust
//! Bus health graph — snapshot of online nodes and bus metrics.

use arf_core::{BusGraph, NodeId};
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use crate::{Bus, NodeEntry};

impl Bus {
    /// Snapshot of the bus health at query time.
    ///
    /// Returns a `BusGraph` with the list of online nodes,
    /// total message count, and uptime in milliseconds.
    pub fn graph(&self) -> BusGraph {
        let map = self.nodes.read().unwrap();
        let nodes: Vec<_> = map.values().map(|entry| entry.info.clone()).collect();

        BusGraph {
            nodes,
            message_count: self.message_count(),
            uptime_ms: self.uptime_ms(),
        }
    }
}
```

### `crates/arf-bus/src/lib.rs` — 注册模块

```rust
mod graph;
```

---

## 单元测试

```rust
// [构造] 空 Bus → graph 返回空节点列表
// [构造] 有节点 → graph 包含所有 NodeInfo
// [数据] message_count 和 uptime_ms 正确
// [快照] graph 是快照，connect 后旧 graph 不变（不自动更新）
```

4 个测试。

---

## 小结

- **`Bus::graph()`** — 读 nodes map，返回不可变快照
- **消除** `nodes` 字段的 dead_code warning
- **4 个新测试**

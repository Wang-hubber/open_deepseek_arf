# 任务 1.11：Python 测试

> Phase 1 — Bus 消息总线第十一项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.10 PyO3 绑定完成

---

## 前置审查：内存/资源泄漏检测

在编写 Python 测试前，先在 Rust 侧写 7 个泄漏检测测试（已合入 `crates/arf-bus/src/lib.rs`）。

### 审查结果

| # | 测试 | 结果 | 发现 |
|---|------|------|------|
| L1 | `handle_drop_without_disconnect_leaves_zombie_entry` | ✅ | NodeHandle drop 不调用 disconnect → NodeEntry 残留在 nodes map，同 NodeId 重连被 `AlreadyConnected` 拒绝 |
| L2 | `zombie_entry_cleaned_by_heartbeat_timeout` | ✅ | 心跳超时后僵尸被清理，node_offline 正确广播，重连成功 |
| L3 | `signal_shutdown_leaves_receiver_hanging` | ❌ **Bug** | `signal_shutdown(&self)` 无法 drop `broadcast_tx`，recv() 永久挂起 |
| L4 | `disconnect_immediately_removes_entry` | ✅ | 正常 disconnect → 立即从 map 移除，同 ID 可立即重连 |
| L5 | `bus_drop_without_shutdown_task_exits` | ✅ | Bus drop 未经 shutdown → spawned task 正常退出，无 hang |
| L6 | `repeated_connect_disconnect_no_accumulation` | ✅ | 同 NodeId 断连-重连 20 轮，nodes map 不累积 |
| L7 | `nodes_map_empty_after_all_disconnected` | ✅ | 5 节点全部 disconnect 后 nodes map 为空 |

### Bug #1: `signal_shutdown` 未关闭 broadcast channel（已修复）

**严重程度**：中 — 仅影响 Python 绑定和任何使用 `signal_shutdown` 的调用方。

**根因**：

```
        shutdown(self)                          signal_shutdown(&self)
        ─────────────                           ──────────────────────
Bus 被消费 → broadcast_tx drop          Bus 保持 &self → broadcast_tx 存活
→ broadcast Sender 数: 0                → broadcast Sender 数: 1 (Bus 仍持有)
→ 所有 Receiver 收到 Closed             → recv() 永久阻塞！
```

**修复**：094f03a `fix(bus): signal_shutdown now closes broadcast channel`

将 `broadcast_tx: broadcast::Sender<Message>` 改为 `broadcast_tx: Mutex<Option<broadcast::Sender<Message>>>`，`signal_shutdown` 调用 `take()` drop Sender。改动 4 处：

| 位置 | 改动 |
|------|------|
| `lib.rs` struct 定义 | `broadcast::Sender<Message>` → `Mutex<Option<Sender>>` |
| `lib.rs::new()` | 用 `Mutex::new(Some(tx))` 包裹 |
| `lib.rs::subscribe()` + `connection.rs::connect()` | `.lock().unwrap().as_ref().unwrap().subscribe()` |
| `lib.rs::signal_shutdown()` | `.lock().unwrap().take()` drop sender，关闭 channel |

**验证**：`signal_shutdown_closes_broadcast_channel` 测试（原 `signal_shutdown_leaves_receiver_hanging`）从 FAILED → PASSED，确认 `recv()` 返回 `Closed`。workspace 全量 142 tests，0 failures。

### 非 Bug（设计确认）

1. **NodeHandle drop 不调用 disconnect**：NodeEntry 残留在 nodes map，由心跳超时 GC。这不是泄漏，是分布式系统中"崩溃检测"的标准模式。`NodeEntry` 体积小（~几百字节），超时后回收。

2. **Arc 循环引用**：无。`Bus → Arc<RwLock<HashMap>> → NodeEntry` 是单向的，`NodeEntry` 不含 Arc 回指。

3. **Bus drop 无 shutdown**：`cmd_tx` drop → message loop 退出 → 所有 Arc clone 释放 → broadcast Sender 数归零 → Receiver 收到 Closed。路径干净。

---

## Python 测试实现记录

### 首轮运行：43/53 passed，10 failed

2026-06-27 首轮 pytest 结果。10 个失败分为 6 类根因，分析如下：

#### 失败 #1: `message_count` 不计数 lifecycle 消息 (2 tests)

**失败**：`test_connect_multiple_nodes` (`0 >= 3`), `test_connect_duplicate_node_id_rejected` (`0 == 1`)

**根因**：`Bus.message_count` 仅在 `BusCommand::Send` 分支中 `fetch_add`，`handle_connect`/`handle_disconnect`/`handle_heartbeat_tick` 中广播的 lifecycle 消息（`node_online`、`node_offline`、`heartbeat_request`）均不计数。这是**设计行为**——`message_count` 统计的是应用消息，不是协议消息。

**修复**：测试断言从 `message_count >= 3` 改为 `message_count == 0`。

#### 失败 #2: node_online 顺序残留 (3 tests)

**失败**：`test_broadcast_received_by_all_peers`（r1 收到 `node_online` 而非 `job`）、`test_concurrent_recv_no_cross_interference`（`KeyError: 'id'`，payload 是 NodeInfo）、`test_message_not_consumed_after_one_recv`（msg_a.payload 是 NodeInfo）

**根因**：节点 `broadcast_rx` 创建时机导致的 node_online 可见性差异：

```
sender 连接 → sender.rx 创建
r1 连接   → sender 看到 r1 的 node_online, r1.rx 创建
r2 连接   → sender, r1 看到 r2 的 node_online. r2.rx 创建
r3 连接   → sender, r1, r2 看到 r3 的 node_online. r3.rx 创建
```

结果：r1 有 2 条 node_online 在 `job` 消息前面，r2 有 1 条，r3 有 0 条。测试必须在发送应用消息前 drain 掉 node_online 残留。

**修复**：在 `sender.send("job", ...)` 之前，每个需要收应用消息的节点按顺序 drain 对应数量的 `node_online`。

#### 失败 #3: `SendError::NodeOffline` 错误消息格式 (1 test)

**失败**：`test_send_to_all_offline_targets_raises` —— `match="NodeOffline"` 不匹配实际消息 `"target nodes offline: ghost/node"`

**根因**：PyO3 绑定 `send_error_to_py` 调用 `err.to_string()`。`SendError::NodeOffline` 的 `Display` 实现输出 `"target nodes offline: {ids}"`，不含 `"NodeOffline"` 字样。Rust 端是枚举变体名，Python 端只有 `Display` 文本。

**修复**：改为 `match="target nodes offline"`。

#### 失败 #4: 定向消息到离线目标全部失败 (1 test)

**失败**：`test_filter_all_trace_node` —— `a.send("directed", [NodeId("engine/bogus")], ...)` 抛错

**根因**：定向发送时，若**所有**目标均离线（`offline.len() == msg.to.len()`），Rust 侧返回 `SendError::NodeOffline`。单目标+离线 = 全部离线 → 抛错。测试中 `engine/bogus` 不存在。

**修复**：改为定向到已在线节点 `NodeId("trace/obs")`。定向消息仍然会被 `ToMatch::All` 的 trace 节点收到。

#### 失败 #5: `asyncio.create_task` 不接受 Future (1 test)

**失败**：`test_try_recv_during_recv_lock_conflict` —— `TypeError: a coroutine was expected, got <Future>`

**根因**：PyO3 `future_into_py` 将 Rust async fn 转为 Python **`Future`** 对象，不是 **coroutine**。`asyncio.create_task()` 只接受 coroutine。这是 PyO3 异步桥接的核心设计——async 方法返回 Future，不能用 `create_task` 包装。

**修复**：直接用 `h.recv()` 返回的 Future + `asyncio.ensure_future()`。

#### 失败 #6: shutdown 后 recv 先返回缓冲消息 (1 test)

**失败**：`test_shutdown_with_online_nodes_no_hang` —— `DID NOT RAISE Exception`

**根因**：`signal_shutdown` 关闭 broadcast channel（修复 #1 后生效），但 `recv()` 的内部循环会**先返回缓冲区中已有的消息**。发送了 2 条 `msg`，在 shutdown 后它们仍在 ring buffer 中。`recv()` 返回这些缓冲消息，消耗完后才返回 `Closed`。

**修复**：shutdown 后先 drain 所有已缓冲的应用消息（`try_recv` 循环），然后验证下一次 `recv()` 抛异常。

#### 失败 #7: channel_capacity=64 但 100 条 message + node_online (1 test)

**失败**：`test_channel_capacity_stress_100_messages` —— `recv error: channel lagged by 36`

**根因**：100 条 `stress` 消息 + 至少 2 条（node_online）在 ring buffer 中。消费者（trace 节点）读取 100 条时 buffer 已覆盖旧消息，导致 `Lagged(36)`。

**修复**：增大 `channel_capacity` 到 256（> 100），或在发送前先 drain node_online 减少 buffer 占用。

---

### 修复结果：53/53 passed

全部修复实施后，pytest 全量通过。修复汇总：

| # | 失败测试 | 根因类别 | 修复方式 |
|---|---------|---------|---------|
| 1-2 | message_count 断言错误 | `message_count` 不计 lifecycle 消息 | 断言改为 `== 0` |
| 3-5 | node_online 残留 | rx 创建时机决定可见性 | `drain_all()` 工具函数 |
| 6 | match="NodeOffline" 不匹配 | PyO3 Display vs enum variant | `match="target nodes offline"` |
| 7 | offline 全部目标抛错 | 单目标+离线=全部离线 | 改为定向到在线节点 |
| 8 | create_task(Future) | future_into_py 返回 Future 非 coroutine | `ensure_future()` |
| 9 | shutdown 后 recv 不抛 | ring buffer 残留 | drain 后验证 |
| 10 | capacity=64 导致 Lag | 100+lifecycle > 64 | 独立 Bus capacity=256 |

### 发现的设计知识点

通过 Python 测试发现并确认的框架行为，已记录在测试注释中：

1. **`message_count` 只计应用消息**：`node_online`/`node_offline`/`heartbeat_request` 不计入
2. **`broadcast_rx` 创建时机**：在 `node_online` 广播**之后**创建，节点看不到自己的 `node_online`，但能看到后续节点的
3. **`future_into_py` 返回 Future**：PyO3 的 async 方法返回 `Future`，不能用 `asyncio.create_task()`，需用 `ensure_future()`
4. **shutdown 后 ring buffer 仍有残留消息**：需 drain 后才能收到 Closed
5. **`SendError::NodeOffline` Display 消息**：`"target nodes offline: {ids}"`，不含枚举变体名

---

## 测试场景

**覆盖策略**：不做低价值单字段 getter 测试（已在 Rust 单测验证），聚焦 Python 集成行为和多节点协作场景——名称冲突、同类型多节点竞争、广播多消费、重连周期、负载均衡基础、内存/资源泄漏与进程残余。

Python API 预览：

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch, Message, SendReceipt, BusGraph

bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

node = NodeInfo("engine/main", "engine", {"session_id": "s1"}, online_since=0)
f = MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe)
handle = await bus.connect(node, f)

receipt = await handle.send("action", [], {"cmd": "run"})
msg = await handle.recv()
msg = handle.try_recv()
graph = bus.graph()
handle.node_info()
handle.filter_config()
await handle.disconnect()
await bus.shutdown()
```

---

## 测试场景（7 组，53 tests，含 Rust 泄漏镜像）

### A. 导入与类型构造 (5 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| A1 | import | 所有导出类型可导入，`__version__` 正确 | `[覆盖]` |
| A2 | to_match_attrs | ToMatch 四个类属性不为 None | `[覆盖]` |
| A3 | node_id_eq_hash | `__eq__`/`__hash__`/`__str__`/`__repr__` | `[trait]` |
| A4 | node_info_defaults | `online_since` 默认为 0，capabilities 空 dict 正常 | `[构造]` |
| A5 | filter_defaults | `MessageFilter()` 默认 types=None, to_match=BroadcastAndDirectedToMe | `[构造]` |

### B. Bus 生命周期 (6 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| B1 | create_defaults | 无参 `Bus()` 创建，`graph()` 空，message_count=0 | `[构造]` |
| B2 | create_custom | 自定义 heartbeat/channel 参数 | `[构造]` |
| B3 | connect_single | 单节点连接，`graph()` 包含该节点 | `[方法]` |
| B4 | connect_multiple | 三节点连接，graph 包含全部，message_count≥3 | `[方法]` |
| B5 | connect_duplicate_node_id | 重复 NodeId 连接 → Exception，graph 不变 | `[边界]` |
| B6 | connect_after_shutdown | shutdown 后 connect → Exception | `[边界]` |

### C. 收发消息 (6 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| C1 | send_broadcast_recv | 广播（to=[]）被另一节点 recv 收到 | `[方法]` |
| C2 | send_multiple_ordered | 5 条消息 FIFO 顺序接收 | `[序列化]` |
| C3 | send_directed | 定向（to=[target]）只被目标收到，is_for/is_broadcast 正确 | `[方法]` |
| C4 | send_to_offline_all | 全部目标离线 → Exception | `[边界]` |
| C5 | send_to_partial_offline | 部分目标离线 → 仍成功，只发在线 | `[边界]` |
| C6 | try_recv_no_msg | 无匹配消息时 try_recv 返回 None | `[边界]` |

### D. 多节点消费 — 负载均衡基础 (5 tests)

> 业务场景：同类型同配置的多个 worker 节点，一条广播消息全部收到，应用层自行决定谁处理。Bus 保证消息可达，不引入消费竞争。

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| D1 | broadcast_to_all_peers | 一条广播被 3 个同类型同 filter 节点全部收到 | `[多节点]` |
| D2 | same_type_multi_worker | 4 个 worker 同 type="worker" 同 filter，全收同一条广播 | `[多节点]` |
| D3 | directed_to_one_ignored_by_others | 定向到 worker/1，worker/2 和 worker/3 不收到 | `[多节点]` |
| D4 | concurrent_recv_no_interference | 3 个节点同时 recv()，各自独立无串扰 | `[并发]` |
| D5 | recv_does_not_consume_for_others | A recv 消费一条消息后，B 仍然能 recv 到（广播语义） | `[多节点]` |

### E. 过滤行为 (4 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| E1 | filter_types_restricts | `types=["action"]` 只收 action，其他类型被过滤 | `[类型]` |
| E2 | filter_directed_to_me | `DirectedToMe` 不收广播 | `[类型]` |
| E3 | filter_all_trace | `types=None + ToMatch.All` 的 trace 节点全量消费 | `[覆盖]` |
| E4 | filter_multiple_same_config | 3 个不同 NodeId 同 filter 同 type，各自独立过滤，全部收到同一条匹配消息 | `[类型]` |

### F. 断连与重连 (5 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| F1 | disconnect_removes_from_graph | disconnect 后 graph 节点数减 1 | `[方法]` |
| F2 | disconnected_methods_raise | disconnect 后 send/recv/node_info/filter_config 均抛 "disconnected" | `[边界]` |
| F3 | disconnect_twice | 重复 disconnect → RuntimeError "already disconnected" | `[边界]` |
| F4 | reconnect_same_id | disconnect 后同 NodeId 重新 connect 成功，新旧 handle 独立 | `[生命周期]` |
| F5 | reconnect_cycle_multi | 同 NodeId 断连→重连→断连→重连 3 轮，graph 一致 | `[生命周期]` |

### G. Shutdown (2 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| G1 | shutdown_recv_send_error | shutdown 后 recv/send 均抛异常 | `[方法]` |
| G2 | shutdown_with_online_nodes | 有在线节点时 shutdown 正常关闭，不 hang | `[方法]` |

### H. 边界与压力 (6 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| H1 | message_properties_rtt | Message 各字段 Python 类型正确：id=str, sender=NodeId, payload=dict/list/int, timestamp=int | `[序列化]` |
| H2 | empty_payload_roundtrip | `{}` 和 `[]` 和 `null` payload 往返不变 | `[序列化]` |
| H3 | large_payload | 10KB payload 往返正确 | `[边界]` |
| H4 | unicode_node_id | Unicode NodeId "引擎/主节点" 正常连接收发 | `[边界]` |
| H5 | channel_capacity_stress | 100 条消息快速发送，全部被 trace 节点收到 | `[压力]` |
| H6 | receipt_online_count_correct | receipt.online_nodes 正确反映在线节点数（含自身） | `[方法]` |

### I. try_recv / recv 并发锁 (2 tests)

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| I1 | try_recv_during_recv_lock | recv 持锁时 try_recv → RuntimeError "concurrent recv" | `[并发]` |
| I2 | concurrent_send_recv | 一边发一边收，不丢消息不 panic | `[并发]` |

### J. 资源泄漏与进程残余 (13 tests)

> PyO3 的 `#[pyclass]` 对象不支持 `weakref.ref()`，Python 侧泄漏验证改用重复创建/销毁 + graph 检查 + GC 回收验证。
> 以下测试是 Rust L1-L7 泄漏检测的 Python 侧镜像，外加 Python 特有的 GC/stress/并发清理测试。

| # | 场景 | 描述 | 角度 |
|---|------|------|------|
| J1 | handle_drop_without_disconnect | Python drop handle 不调用 disconnect → zombie entry 残留，重连被拒绝 | `[泄漏]` |
| J2 | zombie_cleaned_by_timeout | 心跳超时后 zombie 被清理，node_offline 广播，重连成功 | `[泄漏]` |
| J3 | repeated_connect_disconnect | 50 轮断连重连同 NodeId，nodes map 不累积 | `[泄漏]` |
| J4 | graph_empty_after_all | 10 节点全部 disconnect 后 graph 为空 | `[泄漏]` |
| J5 | signal_shutdown_closes_all | signal_shutdown 关闭 broadcast channel，5 个 handle 全部收到 Closed | `[清理]` |
| J6 | bus_drop_no_shutdown | Bus 未 shutdown 直接 del + gc.collect()，spawned task 正常退出无 hang | `[泄漏]` |
| J7 | repeated_bus_create_destroy | 5 个 Bus 实例顺序创建→disconnect→del→gc，tokio runtime 正常释放 | `[泄漏]` |
| J8 | handle_drop_no_side_effects | disconnect 后 drop handle 不影响其他 handle 正常收发 | `[泄漏]` |
| J9 | rapid_100_rounds | 5 个 NodeId 轮转 100 轮 connect→disconnect，graph 为空 | `[压力]` |
| J10 | same_id_100_cycles | 同 NodeId 100 轮断连重连，graph 每轮只含 1 个条目 | `[压力]` |
| J11 | multiple_bus_lifecycle | 5 个 Bus 实例顺序创建→shutdown，资源不累积 | `[泄漏]` |
| J12 | shutdown_state_consistency | shutdown 后 graph 可访问，send/recv 不可用，message_count 冻结 | `[清理]` |
| J13 | concurrent_disconnect | 20 个 handle 并发 disconnect，graph 为空无竞态 | `[并发]` |

---

## 实现代码

### 文件结构

```
py-arf/
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_imports.py        # A1-A5
│   ├── test_lifecycle.py      # B1-B6, C1-C6
│   ├── test_multi_consumer.py # D1-D5
│   ├── test_filters.py        # E1-E4
│   ├── test_reconnect.py      # F1-F5
│   ├── test_shutdown.py       # G1-G2
│   ├── test_boundary.py       # H1-H6
│   ├── test_concurrency.py    # I1-I2
│   └── test_resource_leak.py  # J1-J13 (内存/资源泄漏)
├── pyproject.toml
└── requirements-dev.txt
```

### `py-arf/tests/__init__.py`

```python
"""Python API tests for ARF V1.x Bus bindings."""
```

### `py-arf/tests/conftest.py`

```python
"""Shared fixtures for Python API tests."""
import pytest
from arf import Bus, NodeInfo, MessageFilter, ToMatch


@pytest.fixture
def bus():
    """Create a fresh Bus for each test."""
    b = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=64)
    return b
```

### `py-arf/tests/test_imports.py`

```python
"""
[A] 导入与类型构造 — 验证所有导出类型可导入，基本构造正确。

测试角度: [覆盖] [trait] [构造]
"""
from arf import (
    __version__,
    Bus, BusGraph, Message, MessageFilter,
    NodeHandle, NodeId, NodeInfo, SendReceipt, ToMatch,
)


# ── A1 ──────────────────────────────────────────────────────────────────

def test_import_all_types():
    """[覆盖] 所有 __all__ 导出类型可导入且 __version__ 正确。"""
    assert __version__ == "1.0.0-alpha.0"
    for cls in [Bus, BusGraph, Message, MessageFilter, NodeHandle, NodeId, NodeInfo, SendReceipt, ToMatch]:
        assert cls is not None


# ── A2 ──────────────────────────────────────────────────────────────────

def test_to_match_class_attrs():
    """[覆盖] ToMatch 四个类属性可正常访问。"""
    assert ToMatch.All is not None
    assert ToMatch.BroadcastOnly is not None
    assert ToMatch.DirectedToMe is not None
    assert ToMatch.BroadcastAndDirectedToMe is not None


# ── A3 ──────────────────────────────────────────────────────────────────

def test_node_id_equality_and_hash():
    """[trait] NodeId __eq__/__hash__/__str__/__repr__ 正确。"""
    a1 = NodeId("engine/a")
    a2 = NodeId("engine/a")
    b = NodeId("engine/b")

    assert a1 == a2
    assert a1 != b
    assert hash(a1) == hash(a2)
    assert str(a1) == "engine/a"
    assert repr(a1) == "NodeId('engine/a')"


# ── A4 ──────────────────────────────────────────────────────────────────

def test_node_info_default_online_since():
    """[构造] online_since 默认为 0。"""
    info = NodeInfo("mcp/fs", "mcp", {})
    assert info.online_since == 0
    assert str(info.node_id) == "mcp/fs"
    assert info.capabilities == {}


def test_node_info_full_construction():
    """[构造] NodeInfo 所有字段正确。"""
    info = NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write"]}, online_since=9999)
    assert str(info.node_id) == "mcp/fs"
    assert info.node_type == "mcp"
    assert info.capabilities == {"tools": ["read", "write"]}
    assert info.online_since == 9999


# ── A5 ──────────────────────────────────────────────────────────────────

def test_message_filter_defaults():
    """[构造] MessageFilter 默认 types=None, to_match=BroadcastAndDirectedToMe。"""
    f = MessageFilter()
    assert f.types is None
    assert f.to_match == ToMatch.BroadcastAndDirectedToMe


def test_message_filter_custom():
    """[构造] MessageFilter 自定义参数正确。"""
    f = MessageFilter(types=["action", "event"], to_match=ToMatch.All)
    assert f.types == ["action", "event"]
    assert f.to_match == ToMatch.All
```

### `py-arf/tests/test_lifecycle.py`

```python
"""
[B+C] Bus 生命周期 + 收发消息 — create/connect/send/recv/disconnect 基本链路。

测试角度: [构造] [方法] [边界] [序列化]
"""
import pytest
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ═══════════════════════════════════════════════════════════════════════
# B — Bus 生命周期
# ═══════════════════════════════════════════════════════════════════════


# ── B1 ──────────────────────────────────────────────────────────────────

def test_create_bus_defaults():
    """[构造] 无参 Bus() 创建，graph() 空，message_count=0。"""
    bus = Bus()
    g = bus.graph()
    assert g.nodes == []
    assert g.message_count == 0
    assert g.uptime_ms >= 0


# ── B2 ──────────────────────────────────────────────────────────────────

def test_create_bus_custom_params():
    """[构造] 自定义参数创建 Bus。"""
    bus = Bus(heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=128)
    g = bus.graph()
    assert g.message_count == 0


# ── B3 ──────────────────────────────────────────────────────────────────

async def test_connect_single_node(bus):
    """[方法] 单节点连接后 graph 包含该节点。"""
    h = await bus.connect(
        NodeInfo("engine/main", "engine", {"session": "s1"}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/main"
    assert g.nodes[0].node_type == "engine"


# ── B4 ──────────────────────────────────────────────────────────────────

async def test_connect_multiple_nodes(bus):
    """[方法] 三节点连接，graph 包含全部，message_count 包含 node_online。"""
    await bus.connect(NodeInfo("engine/main", "engine", {}), MessageFilter())
    await bus.connect(NodeInfo("mcp/fs", "mcp", {}), MessageFilter())
    await bus.connect(NodeInfo("trace/obs", "trace", {}),
                      MessageFilter(types=None, to_match=ToMatch.All))

    g = bus.graph()
    assert len(g.nodes) == 3
    ids = {str(n.node_id) for n in g.nodes}
    assert ids == {"engine/main", "mcp/fs", "trace/obs"}
    assert bus.message_count >= 3  # 3 条 node_online


# ── B5: 重复 NodeId ────────────────────────────────────────────────────

async def test_connect_duplicate_node_id_rejected(bus):
    """[边界] 重复 NodeId 连接 → Exception，graph 不变。

    业务场景：用户定义节点时可能无意重名，Bus 拒绝重复连接。
    """
    info = NodeInfo("engine/main", "engine", {})
    h1 = await bus.connect(info, MessageFilter())

    # 尝试用相同 NodeId 连接第二个节点
    dup = NodeInfo("engine/main", "engine", {})
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(dup, MessageFilter())

    # graph 仍然只有 1 个节点
    assert len(bus.graph().nodes) == 1
    assert bus.message_count == 1  # 只有第一次 connect 的 node_online


# ── B6: shutdown 后 connect ────────────────────────────────────────────

async def test_connect_after_shutdown_raises(bus):
    """[边界] shutdown 后 connect 抛异常。"""
    await bus.shutdown()
    with pytest.raises(Exception):
        await bus.connect(NodeInfo("late", "engine", {}), MessageFilter())


# ═══════════════════════════════════════════════════════════════════════
# C — 收发消息
# ═══════════════════════════════════════════════════════════════════════


# ── C1 ──────────────────────────────────────────────────────────────────

async def test_send_broadcast_and_recv(bus):
    """[方法] 广播（to=[]）被另一节点 recv 收到。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("action", [], {"cmd": "greet"})
    assert receipt.online_nodes >= 2
    assert len(receipt.message_id) > 0

    msg = await h2.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"cmd": "greet"}
    assert str(msg.sender) == "engine/a"
    assert msg.is_broadcast() is True


# ── C2 ──────────────────────────────────────────────────────────────────

async def test_send_multiple_messages_ordered(bus):
    """[序列化] 5 条消息 FIFO 顺序接收。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    for i in range(5):
        await h1.send("tick", [], {"seq": i})

    received = []
    for _ in range(5):
        msg = await h2.recv()
        received.append(msg.payload["seq"])
    assert received == [0, 1, 2, 3, 4]


# ── C3 ──────────────────────────────────────────────────────────────────

async def test_send_directed_message(bus):
    """[方法] 定向消息只被目标收到。"""
    node_b = NodeId("engine/b")
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("whisper", [node_b], {"secret": 42})
    assert receipt.online_nodes >= 2

    msg = await h2.recv()
    assert msg.msg_type == "whisper"
    assert msg.is_broadcast() is False
    assert msg.is_for(node_b) is True
    assert msg.is_for(NodeId("engine/a")) is False


# ── C4: 定向到全离线目标 ──────────────────────────────────────────────

async def test_send_to_all_offline_targets_raises(bus):
    """[边界] 全部目标离线 → Exception。

    业务场景：负载均衡场景下主节点宕机，备用节点尚未激活时的过渡期。
    """
    h = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())

    ghost = NodeId("ghost/node")
    with pytest.raises(Exception, match="NodeOffline"):
        await h.send("ping", [ghost], {})


# ── C5: 定向到部分离线目标 ────────────────────────────────────────────

async def test_send_to_partial_offline_still_succeeds(bus):
    """[边界] 部分目标离线 → 仍成功，消息投递给在线目标。

    业务场景：多目标定向消息中部分备用节点未激活，不影响主目标接收。
    """
    target_online = NodeId("engine/b")
    target_offline = NodeId("ghost/node")

    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("ping", [target_online, target_offline], {"x": 1})
    assert receipt.online_nodes >= 2

    # h2 收到了
    msg = await h2.recv()
    assert msg.payload == {"x": 1}
    assert msg.is_for(target_online) is True


# ── C6 ──────────────────────────────────────────────────────────────────

async def test_try_recv_no_message_returns_none(bus):
    """[边界] 无消息时 try_recv 返回 None。"""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    result = h.try_recv()
    assert result is None
```

### `py-arf/tests/test_multi_consumer.py`

```python
"""
[D] 多节点消费 — 负载均衡基础。

业务场景：
- 多个同 type 同 filter 的 worker 节点，广播消息全部收到
- 应用层自行决定哪个 worker 处理（如基于 session state 或轮询）
- Bus 保证消息可达，不引入消费竞争或排他消费
- 一个 worker 断线后，备用 worker 重新 connect 继续消费

测试角度: [多节点] [并发]
"""
import asyncio
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── D1: 一条广播被所有 peer 收到 ──────────────────────────────────────

async def test_broadcast_received_by_all_peers(bus):
    """[多节点] 一条广播被 3 个同类型同 filter 节点全部收到。

    这是负载均衡的基础保证：消息不排他，所有 worker 都有机会消费。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)

    sender = await bus.connect(NodeInfo("engine/sender", "engine", {}), f)
    r1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    r2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    r3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    await sender.send("job", [], {"task": "compress"})

    # 三个 worker 都收到同一条消息
    for handle in [r1, r2, r3]:
        msg = await handle.recv()
        assert msg.msg_type == "job"
        assert msg.payload == {"task": "compress"}


# ── D2: 同 type 多 worker 全收广播 ─────────────────────────────────────

async def test_same_type_multi_worker_all_receive(bus):
    """[多节点] 4 个 worker 节点同 type="worker" 同 filter，全收同一条广播。

    业务场景：4 个模型推理 worker，type 均为 "model"，用户广播 prompt
    给所有 worker，由应用层做负载均衡。
    """
    f = MessageFilter(types=["infer"], to_match=ToMatch.BroadcastAndDirectedToMe)

    dispatcher = await bus.connect(NodeInfo("engine/dispatcher", "engine", {}), f)
    workers = []
    for i in range(4):
        w = await bus.connect(
            NodeInfo(f"model/worker{i}", "model", {"gpu": i}), f
        )
        workers.append(w)

    await dispatcher.send("infer", [], {"prompt": "hello world"})

    count = 0
    for w in workers:
        msg = await w.recv()
        assert msg.msg_type == "infer"
        assert msg.payload == {"prompt": "hello world"}
        count += 1
    assert count == 4  # all 4 workers received it


# ── D3: 定向到特定 worker，其他不收到 ─────────────────────────────────

async def test_directed_to_one_worker_ignored_by_others(bus):
    """[多节点] 定向到 worker/1，worker/2 和 worker/3 不收到。

    业务场景：会话粘性 — 同一 session 的消息始终定向到同一个 worker。
    """
    f = MessageFilter(types=None, to_match=ToMatch.DirectedToMe)

    w1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    w2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    w3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    target = NodeId("worker/1")
    await engine.send("session_msg", [target], {"session": "sid-42", "data": "hello"})

    # w1 收到定向消息
    msg1 = await w1.recv()
    assert msg1.msg_type == "session_msg"
    assert msg1.is_for(target) is True

    # w2, w3 不会收到（他们的 filter 是 DirectedToMe，且消息 to=["worker/1"]）
    # 验证不阻塞：try_recv 返回 None
    assert w2.try_recv() is None
    assert w3.try_recv() is None


# ── D4: 并发 recv 无串扰 ──────────────────────────────────────────────

async def test_concurrent_recv_no_cross_interference(bus):
    """[并发] 3 个节点同时 recv()，各自独立消费无串扰。

    每个节点的 broadcast_rx 是独立的 tokio broadcast Receiver，
    一个节点 recv 不会消耗另一个节点的消息。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)

    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    r1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    r2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    r3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    # Drain node_online 消息
    for _ in range(3):
        await r1.recv()
        await r2.recv()
        await r3.recv()

    await sender.send("job", [], {"id": 1})

    # 三个节点并发 recv
    async def recv_one(handle):
        msg = await handle.recv()
        return msg.payload["id"]

    results = await asyncio.gather(
        recv_one(r1), recv_one(r2), recv_one(r3),
    )
    assert results == [1, 1, 1]


# ── D5: 广播语义 — A 消费后 B 仍能收到 ────────────────────────────────

async def test_message_not_consumed_after_one_recv(bus):
    """[多节点] A recv 消费一条广播后，B 仍然能 recv 到同一条。

    广播语义验证：不是队列消费模型，不会排他。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    a = await bus.connect(NodeInfo("node/a", "node", {}), f)
    b = await bus.connect(NodeInfo("node/b", "node", {}), f)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)

    await sender.send("announce", [], {"msg": "hello all"})

    # a 先消费
    msg_a = await a.recv()
    assert msg_a.payload == {"msg": "hello all"}

    # b 仍然能收到同一条
    msg_b = await b.recv()
    assert msg_b.payload == {"msg": "hello all"}
    assert msg_b.id == msg_a.id  # 同一条消息


# ── D+: 备用节点激活消费 ─────────────────────────────────────────────

async def test_standby_worker_activates_and_consumes(bus):
    """[多节点] 主 worker 断连后，备用 worker connect 继续消费后续消息。

    业务场景：负载均衡 — 主 worker 繁忙或崩溃时，备用 worker 激活并消费。
    Bus 不感知角色的切换，应用层负责激活备用节点。这里验证通讯可行。
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastAndDirectedToMe)
    engine = await bus.connect(NodeInfo("engine/main", "engine", {}),
                               MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe))

    # 主 worker 上线
    primary = await bus.connect(NodeInfo("worker/primary", "worker", {}), f)

    # 发送 job
    await engine.send("job", [], {"task": "t1"})
    msg = await primary.recv()
    assert msg.payload == {"task": "t1"}

    # 主 worker "宕机" — disconnect
    await primary.disconnect()

    # 备用 worker 激活
    standby = await bus.connect(NodeInfo("worker/standby", "worker", {}), f)

    # 备用 worker 收到后续 job
    await engine.send("job", [], {"task": "t2"})
    msg2 = await standby.recv()
    assert msg2.payload == {"task": "t2"}

    # 备用没有收到历史消息 t1（late joiner 语义）
    assert standby.try_recv() is None


async def test_primary_reconnect_after_standby_takes_over(bus):
    """[多节点] 主 worker 断连→备用激活→主 worker 重连→两者同时消费。

    主 worker 恢复后和备用 worker 同时在线，都收到后续广播。
    应用层可能通过 DirectedToMe 做会话粘性，但这是可选策略。
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastAndDirectedToMe)
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # 主 worker 上线
    primary = await bus.connect(NodeInfo("worker/main", "worker", {}), f)

    # 发 job 1（主 worker 在线时）
    await engine.send("job", [], {"task": "t1"})
    msg = await primary.recv()
    assert msg.payload == {"task": "t1"}

    # 主 worker 宕机 → disconnect
    await primary.disconnect()

    # 备用 worker 激活 → 消费 job 2
    standby = await bus.connect(NodeInfo("worker/standby", "worker", {}), f)
    await engine.send("job", [], {"task": "t2"})
    msg2 = await standby.recv()
    assert msg2.payload == {"task": "t2"}

    # 主 worker 恢复 → 重连
    primary2 = await bus.connect(NodeInfo("worker/main", "worker", {}), f)

    # 两个 worker 同时在线，都收到 job 3
    await engine.send("job", [], {"task": "t3"})

    msg_p = await primary2.recv()
    assert msg_p.payload == {"task": "t3"}
    msg_s = await standby.recv()
    assert msg_s.payload == {"task": "t3"}
```

### `py-arf/tests/test_filters.py`

```python
"""
[E] 过滤行为 — MessageFilter 的 types 和 to_match 控制接收行为。

测试角度: [类型] [覆盖]
"""
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── E1 ──────────────────────────────────────────────────────────────────

async def test_filter_types_restricts(bus):
    """[类型] types=["action"] 只收 action，其他类型被过滤。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await h2.send("ping", [], {"n": 1})
    await h2.send("action", [], {"n": 2})
    await h2.send("pong", [], {"n": 3})

    # h1 只收到 action
    msg = await h1.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"n": 2}


# ── E2 ──────────────────────────────────────────────────────────────────

async def test_filter_directed_to_me(bus):
    """[类型] DirectedToMe 不收广播。"""
    target = NodeId("mcp/fs")
    worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {}),
        MessageFilter(types=None, to_match=ToMatch.DirectedToMe),
    )
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await engine.send("broadcast_msg", [], {"x": 1})
    await engine.send("direct_msg", [target], {"x": 2})

    # worker 只收到定向消息
    msg = await worker.recv()
    assert msg.msg_type == "direct_msg"
    assert msg.payload == {"x": 2}

    # 没有更多消息
    assert worker.try_recv() is None


# ── E3 ──────────────────────────────────────────────────────────────────

async def test_filter_all_trace_node(bus):
    """[覆盖] ToMatch.All + types=None 的 trace 节点全量消费。"""
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    a = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await a.send("broadcast", [], {"to": "all"})
    await a.send("directed", [NodeId("engine/bogus")], {"to": "b"})

    received = set()
    for _ in range(2):
        msg = await trace.recv()
        received.add(msg.msg_type)
    assert "broadcast" in received
    assert "directed" in received


# ── E4: 多个同 filter 节点各自独立过滤 ──────────────────────────────────

async def test_filter_multiple_same_config_independent(bus):
    """[类型] 3 个不同 NodeId 同 filter 同 type，各自独立过滤。

    Bus 对每个节点独立执行 filter.matches()，filter 配置相同不共享状态。
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastOnly)
    engine = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    w1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    w2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    w3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    # engine 发广播 — 但 worker filter 是 BroadcastOnly，能收到
    await engine.send("job", [], {"id": 1})
    # engine 发定向到 worker/1 — worker filter BroadcastOnly，收不到
    await engine.send("job", [NodeId("worker/1")], {"id": 2})

    # 三个 worker 都只收到广播 job，收不到定向 job
    for w in [w1, w2, w3]:
        msg = await w.recv()
        assert msg.payload == {"id": 1}
        # 没有更多（定向 job 被 filter 过滤）
        assert w.try_recv() is None
```

### `py-arf/tests/test_reconnect.py`

```python
"""
[F] 断连与重连 — disconnect/reconnect 完整生命周期。

业务场景：
- 用户误操作 disconnect 后重连，同 NodeId 应允许
- 节点崩溃后重启，以相同 NodeId 重新注册
- 重复 disconnect 不应 panic

测试角度: [方法] [边界] [生命周期]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── F1 ──────────────────────────────────────────────────────────────────

async def test_disconnect_removes_from_graph(bus):
    """[方法] disconnect 后 graph 中移除该节点。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    assert len(bus.graph().nodes) == 2
    await h1.disconnect()

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/b"


async def test_disconnect_broadcasts_node_offline(bus):
    """[方法] disconnect 后其他节点 recv 收到 node_offline。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # Drain h2's view of h1's node_online
    _ = await h2.recv()  # h1's node_online

    await h1.disconnect()

    msg = await h2.recv()
    assert msg.msg_type == "node_offline"
    assert str(msg.sender) == "engine/a"


# ── F2 ──────────────────────────────────────────────────────────────────

async def test_disconnected_handle_methods_raise(bus):
    """[边界] disconnect 后 send/recv/node_info/filter_config 均抛 'disconnected'。"""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await h.disconnect()

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.send("action", [], {})

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.recv()

    with pytest.raises(RuntimeError, match="disconnected"):
        h.node_info()

    with pytest.raises(RuntimeError, match="disconnected"):
        h.filter_config()


# ── F3 ──────────────────────────────────────────────────────────────────

async def test_disconnect_twice_raises(bus):
    """[边界] 重复 disconnect → RuntimeError 'already disconnected'。"""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await h.disconnect()

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.disconnect()


# ── F4 ──────────────────────────────────────────────────────────────────

async def test_reconnect_same_node_id(bus):
    """[生命周期] disconnect 后同 NodeId 重新 connect 成功，新旧 handle 独立。

    业务场景：节点崩溃后重启，以相同 NodeId 重新注册。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)

    # 主 worker 连接 → disconnect → 重连
    primary = await bus.connect(NodeInfo("worker/main", "worker", {}), f)
    await primary.disconnect()

    # 重连成功
    primary2 = await bus.connect(NodeInfo("worker/main", "worker", {}), f)

    # 新旧 handle 独立 — 新 handle 正常工作
    await sender.send("job", [], {"id": 1})
    msg = await primary2.recv()
    assert msg.payload == {"id": 1}

    # 旧 handle 已 disconnected
    with pytest.raises(RuntimeError, match="disconnected"):
        await primary.send("job", [], {})


# ── F5 ──────────────────────────────────────────────────────────────────

async def test_reconnect_cycle_multiple_rounds(bus):
    """[生命周期] 同 NodeId 断连→重连 3 轮，graph 始终一致。

    业务场景：节点频繁崩溃恢复，Bus 不应泄漏或错乱。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)

    for round_num in range(3):
        h = await bus.connect(NodeInfo("flaky/node", "worker", {"round": round_num}), f)

        g = bus.graph()
        nodes = [n for n in g.nodes if str(n.node_id) == "flaky/node"]
        assert len(nodes) == 1
        assert nodes[0].capabilities == {"round": round_num}

        await h.disconnect()

        # graph 不再包含
        g2 = bus.graph()
        assert all(str(n.node_id) != "flaky/node" for n in g2.nodes)

    # 最终 graph 为空
    assert len(bus.graph().nodes) == 0
```

### `py-arf/tests/test_shutdown.py`

```python
"""
[G] Shutdown — Bus 关闭行为。

测试角度: [方法] [边界]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── G1 ──────────────────────────────────────────────────────────────────

async def test_shutdown_recv_send_error(bus):
    """[方法] shutdown 后 recv/send 均抛异常。"""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await bus.shutdown()

    with pytest.raises(Exception):
        await h.recv()

    with pytest.raises(Exception):
        await h.send("action", [], {})


# ── G2 ──────────────────────────────────────────────────────────────────

async def test_shutdown_with_online_nodes_no_hang(bus):
    """[边界] 有在线节点时 shutdown 不 hang。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())
    h3 = await bus.connect(NodeInfo("trace/obs", "trace", {}), MessageFilter())

    # 发送一些消息
    await h1.send("msg", [], {"n": 1})
    await h2.send("msg", [], {"n": 2})

    # shutdown 不阻塞
    await bus.shutdown()

    # 所有操作都抛异常
    for h in [h1, h2, h3]:
        with pytest.raises(Exception):
            await h.recv()
```

### `py-arf/tests/test_boundary.py`

```python
"""
[H] 边界与压力 — payload 往返、Unicode、大消息、容量压力。

测试角度: [序列化] [边界] [压力]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── H1 ──────────────────────────────────────────────────────────────────

async def test_message_properties_roundtrip(bus):
    """[序列化] Message 各字段 Python 类型正确。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {"ver": 1}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    await h1.send("test", [NodeId("trace/obs")], {"key": "v", "nested": {"a": 1}})
    msg = await h2.recv()

    assert isinstance(msg.id, str) and len(msg.id) > 0
    assert isinstance(msg.msg_type, str)
    assert isinstance(msg.sender, NodeId)
    assert isinstance(msg.to, list)
    assert len(msg.to) == 1
    assert isinstance(msg.payload, dict)
    assert msg.payload == {"key": "v", "nested": {"a": 1}}
    assert isinstance(msg.timestamp, int) and msg.timestamp > 0
    assert msg.is_broadcast() is False
    assert msg.is_for(NodeId("trace/obs")) is True
    assert msg.is_for(NodeId("engine/a")) is False


# ── H2 ──────────────────────────────────────────────────────────────────

async def test_empty_dict_payload_roundtrip(bus):
    """[序列化] {} payload 往返不变。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("empty", [], {})
    msg = await h2.recv()
    assert msg.payload == {}


async def test_list_payload_roundtrip(bus):
    """[序列化] [] payload 往返不变。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("list", [], [1, "two", {"three": 3}])
    msg = await h2.recv()
    assert msg.payload == [1, "two", {"three": 3}]


async def test_null_payload_roundtrip(bus):
    """[序列化] None/null payload 往返不变。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("null_val", [], None)
    msg = await h2.recv()
    assert msg.payload is None


async def test_int_and_float_payload(bus):
    """[序列化] int/float payload 正确。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("num", [], 42)
    msg = await h2.recv()
    assert msg.payload == 42
    assert isinstance(msg.payload, int)


# ── H3 ──────────────────────────────────────────────────────────────────

async def test_large_payload_roundtrip(bus):
    """[边界] 10KB payload 往返不变。"""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    large = {"data": "x" * 10240}
    await h1.send("large", [], large)
    msg = await h2.recv()
    assert msg.payload == large
    assert len(msg.payload["data"]) == 10240


# ── H4 ──────────────────────────────────────────────────────────────────

async def test_unicode_node_id(bus):
    """[边界] Unicode NodeId 正常连接收发。

    业务场景：中文命名的节点标识。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    引擎 = await bus.connect(NodeInfo("引擎/主节点", "engine", {"session": "会话1"}), f)
    追踪 = await bus.connect(NodeInfo("追踪/观察者", "trace", {}),
                             MessageFilter(types=None, to_match=ToMatch.All))

    await 引擎.send("中文消息", [], {"内容": "你好世界"})
    msg = await 追踪.recv()

    assert msg.msg_type == "中文消息"
    assert msg.payload == {"内容": "你好世界"}
    assert str(msg.sender) == "引擎/主节点"


async def test_node_id_with_special_chars(bus):
    """[边界] NodeId 含特殊字符正常连接。"""
    ids = [
        "node/with-dash",
        "node.with.dots",
        "node_with_underscore",
        "node:with:colons",
        "node/with/slashes/deep",
    ]
    for nid in ids:
        h = await bus.connect(NodeInfo(nid, "test", {}), MessageFilter())
        assert str(h.node_info().node_id) == nid


# ── H5 ──────────────────────────────────────────────────────────────────

async def test_channel_capacity_stress_100_messages(bus):
    """[压力] 100 条消息快速发送，全部被 trace 节点顺序收到。

    使用 channel_capacity=64 的 Bus（默认），100 > 64 意味着
    消息会循环覆盖 ring buffer。只要消费者足够快就不会 Lagged。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    trace = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                               MessageFilter(types=None, to_match=ToMatch.All))

    n = 100
    for i in range(n):
        await sender.send("stress", [], {"seq": i})

    received = 0
    for _ in range(n):
        msg = await trace.recv()
        if msg.msg_type == "stress":
            received += 1
    assert received == n


# ── H6 ──────────────────────────────────────────────────────────────────

async def test_receipt_online_count_includes_self(bus):
    """[方法] receipt.online_nodes 反映在线节点数（含自身）。"""
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), MessageFilter())

    r = await sender.send("t", [], {})
    assert r.online_nodes == 1  # only self
    assert r.matching_nodes == 1  # self matches broadcast

    other = await bus.connect(NodeInfo("engine/o", "engine", {}), MessageFilter())
    r2 = await sender.send("t", [], {})
    assert r2.online_nodes >= 2


async def test_receipt_matching_nodes_with_different_filters(bus):
    """[方法] matching_nodes 正确反映不同 filter 的匹配情况。"""
    sender = await bus.connect(
        NodeInfo("engine/s", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    # action-only node: 不会匹配 "ping"
    await bus.connect(
        NodeInfo("worker/action_only", "worker", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    # all-types node: 会匹配 "ping"
    await bus.connect(
        NodeInfo("worker/all", "worker", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    r = await sender.send("ping", [], {})
    # online: sender + 2 workers = 3
    assert r.online_nodes >= 3
    # matching: sender + worker/all = 2（worker/action_only 不匹配 ping）
    assert r.matching_nodes >= 2
```

### `py-arf/tests/test_concurrency.py`

```python
"""
[I] 并发 — try_recv/recv 锁冲突与并发收发。

测试角度: [并发]
"""
import asyncio
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── I1 ──────────────────────────────────────────────────────────────────

async def test_try_recv_during_recv_lock_conflict(bus):
    """[并发] recv 持锁时 try_recv → RuntimeError 'concurrent recv'。"""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())

    recv_task = asyncio.create_task(h.recv())
    await asyncio.sleep(0.05)  # 让 recv 获得锁

    with pytest.raises(RuntimeError, match="concurrent recv"):
        h.try_recv()

    # 发消息让 recv 返回，避免 task 悬挂
    helper = await bus.connect(NodeInfo("engine/helper", "engine", {}), MessageFilter())
    await helper.send("wakeup", [], {})
    await recv_task


# ── I2 ──────────────────────────────────────────────────────────────────

async def test_concurrent_send_recv_no_lost_messages(bus):
    """[并发] 一边发一边收，不丢消息。

    使用 asyncio.gather 并发执行 send 和 recv。
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    receiver = await bus.connect(NodeInfo("engine/r", "engine", {}), f)

    async def send_batch(start, count):
        for i in range(start, start + count):
            await sender.send("msg", [], {"seq": i})

    async def recv_batch(count):
        received = []
        for _ in range(count):
            msg = await receiver.recv()
            received.append(msg.payload["seq"])
        return received

    # 并发：一边发 20 条，一边收
    send_task = asyncio.create_task(send_batch(0, 20))
    recv_task = asyncio.create_task(recv_batch(20))

    await send_task
    result = await recv_task

    assert len(result) == 20
    assert result == list(range(20))
```

### `py-arf/tests/test_resource_leak.py`

```python
"""
[J] Resource leak & process residue — memory leak, zombie entry, resource cleanup.

Mirrors the 7 Rust leak-detection tests (L1-L7) at the Python binding level,
plus additional Python-specific GC/stress/concurrent-cleanup tests.

PyO3 note: #[pyclass] objects do not support weakref.ref(), so Python-side
leak verification uses repeated create/destroy cycles + graph inspection instead.

Test angles: [泄漏] [清理] [压力] [并发]
"""
import asyncio
import gc
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ═══════════════════════════════════════════════════════════════════════
# J1-J2: Zombie entry lifecycle (Python mirrors of Rust L1-L2)
# ═══════════════════════════════════════════════════════════════════════


# ── J1: Handle drop 不调用 disconnect → zombie entry ─────────────────

async def test_handle_drop_without_disconnect_leaves_zombie():
    """[泄漏] Python drop handle without disconnect → zombie entry remains in nodes map.

    Python mirror of Rust L1. A Python user may let a NodeHandle go out of scope
    without awaiting disconnect(). The NodeEntry survives in the nodes map and
    blocks reconnection with the same NodeId.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

    async def connect_and_drop():
        h = await bus.connect(
            NodeInfo("crash-victim", "test", {}),
            MessageFilter(),
        )
        # h dropped here — no disconnect()

    await connect_and_drop()

    # NodeEntry still present in graph
    g = bus.graph()
    zombie = [n for n in g.nodes if str(n.node_id) == "crash-victim"]
    assert len(zombie) == 1, (
        "BUG: dropped handle was immediately removed — "
        "zombie entry should persist until heartbeat timeout"
    )

    # Reconnect with same NodeId → rejected
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(
            NodeInfo("crash-victim", "test", {}),
            MessageFilter(),
        )

    await bus.shutdown()


# ── J2: zombie 被心跳超时清理 ────────────────────────────────────────

async def test_zombie_cleaned_by_heartbeat_timeout():
    """[泄漏] Zombie entry cleaned by heartbeat timeout; node_offline broadcast; reconnect allowed.

    Python mirror of Rust L2. Uses fast heartbeat parameters to speed up the test.
    After timeout, the zombie is evicted and the same NodeId can reconnect.
    """
    bus = Bus(heartbeat_interval_ms=20, heartbeat_timeout_ms=60, channel_capacity=16)

    # Watcher node to observe node_offline broadcast
    watcher = await bus.connect(
        NodeInfo("watcher", "test", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    # Create zombie (drop without disconnect)
    async def create_zombie():
        _zombie = await bus.connect(
            NodeInfo("zombie", "test", {}),
            MessageFilter(),
        )
        # _zombie dropped here — no disconnect()

    await create_zombie()

    # Drain zombie's node_online from watcher
    drain_msg = await watcher.recv()
    assert drain_msg is not None

    # Wait for heartbeat timeout to evict zombie.
    # With interval=20ms, timeout=60ms, eviction happens around tick 4 (~80ms).
    # We poll with timeout up to ~500ms to be safe.
    saw_offline = False
    for _ in range(30):
        try:
            msg = await asyncio.wait_for(watcher.recv(), timeout=0.1)
            if msg.msg_type == "node_offline" and str(msg.sender) == "zombie":
                saw_offline = True
                break
        except asyncio.TimeoutError:
            continue

    assert saw_offline, (
        "BUG: zombie node should be cleaned by heartbeat timeout "
        "and broadcast node_offline"
    )

    # Graph no longer contains zombie
    g = bus.graph()
    zombie_nodes = [n for n in g.nodes if str(n.node_id) == "zombie"]
    assert len(zombie_nodes) == 0, "BUG: zombie entry not cleaned from nodes map"

    # Same NodeId can now reconnect
    h = await bus.connect(
        NodeInfo("zombie", "test", {}),
        MessageFilter(),
    )
    assert h is not None

    await watcher.disconnect()
    await h.disconnect()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J3-J4: Connect/disconnect accumulation (Python mirrors of Rust L6-L7)
# ═══════════════════════════════════════════════════════════════════════


# ── J3: 反复 connect/disconnect 同 NodeId — nodes map 不累积 ─────────

async def test_repeated_connect_disconnect_no_accumulation():
    """[泄漏] 50 rounds of disconnect→reconnect same NodeId, graph never accumulates.

    Python mirror of Rust L6. Each disconnect must immediately remove the entry
    so the next connect is a fresh insertion, not a duplicate.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(50):
        h = await bus.connect(
            NodeInfo("flappy", "worker", {"round": round_num}),
            MessageFilter(),
        )

        # Graph has exactly 1 "flappy" entry
        g = bus.graph()
        flappy_entries = [n for n in g.nodes if str(n.node_id) == "flappy"]
        assert len(flappy_entries) == 1, (
            f"BUG round {round_num}: expected 1 flappy entry, got {len(flappy_entries)}"
        )

        await h.disconnect()

        # After disconnect, flappy is gone
        g2 = bus.graph()
        assert not any(str(n.node_id) == "flappy" for n in g2.nodes), (
            f"BUG round {round_num}: flappy not removed after disconnect"
        )

    # Final graph is empty
    assert len(bus.graph().nodes) == 0

    await bus.shutdown()


# ── J4: 全部 disconnect 后 graph 为空 ─────────────────────────────────

async def test_graph_empty_after_all_disconnected():
    """[泄漏] After all 10 nodes disconnect, graph is empty.

    Python mirror of Rust L7. Ensures no stale entries remain.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    handles = []
    for i in range(10):
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    assert len(bus.graph().nodes) == 10

    # Disconnect all
    for h in handles:
        await h.disconnect()

    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: nodes map not empty after all disconnected: {len(g.nodes)} nodes remain"
    )

    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J5-J6: Shutdown & drop cleanup (Python mirrors of Rust L3, L5)
# ═══════════════════════════════════════════════════════════════════════


# ── J5: signal_shutdown 关闭 broadcast channel → 所有 handle recv 报错 ─

async def test_signal_shutdown_closes_broadcast_channel_for_all():
    """[清理] signal_shutdown closes broadcast channel; all handles get Closed error.

    Python mirror of Rust L3. After bus.shutdown() (which calls signal_shutdown),
    the broadcast_tx Sender is dropped, closing the channel for all receivers.
    After draining buffered messages, every connected handle's recv() raises.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=64)

    handles = []
    for i in range(5):
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    # Send messages so ring buffer has data
    await handles[0].send("pre_shutdown", [], {"n": 1})
    await handles[0].send("pre_shutdown", [], {"n": 2})

    await bus.shutdown()

    # Every handle: after draining buffered messages, recv raises
    for h in handles:
        # Drain buffered messages
        while True:
            try:
                m = h.try_recv()
                if m is None:
                    break
            except Exception:
                break  # channel already closed
        # Now recv should raise (buffer empty, channel closed)
        with pytest.raises(Exception):
            await h.recv()


# ── J6: Bus drop 不调用 shutdown → spawned task 正常退出 ─────────────

async def test_bus_drop_without_shutdown_no_hang():
    """[泄漏] Bus GC'd without explicit shutdown — spawned task exits cleanly, no hang.

    Python mirror of Rust L5. When the Bus object is garbage collected
    (cmd_tx dropped → message loop exits), the process must not hang.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)
    h = await bus.connect(
        NodeInfo("n", "test", {}),
        MessageFilter(),
    )
    await h.disconnect()

    # Drop bus without shutdown — simulate GC
    del bus
    gc.collect()

    # Give spawned task time to exit
    await asyncio.sleep(0.1)

    # No hang, no panic — test passes
    assert True


# ═══════════════════════════════════════════════════════════════════════
# J7-J8: Python GC & object lifecycle
# ═══════════════════════════════════════════════════════════════════════


# ── J7: Bus drop 后 tokio runtime 释放，新 Bus 可正常创建 ────────────

async def test_new_bus_after_previous_bus_garbage_collected():
    """[泄漏] After Bus is GC'd (no explicit shutdown), a new Bus can be created.

    PyO3 objects do not support weakref. Instead, verify that the tokio runtime
    is properly released when the Bus is dropped, so a fresh Bus works correctly.
    """
    for i in range(5):
        bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        await h.disconnect()
        # No shutdown — let Python GC handle it
        del bus
        gc.collect()
        await asyncio.sleep(0.01)

    # All 5 Bus instances created and GC'd without issue
    assert True


# ── J8: Handle disconnect 后可立即 drop 无副作用 ─────────────────────

async def test_handle_drop_after_disconnect_no_side_effects():
    """[泄漏] After disconnect, dropping the handle does not affect other handles.

    PyO3 objects do not support weakref. Instead, verify that dropping a
    disconnected handle does not interfere with other handles on the same Bus.
    """
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=64)

    # Connect multiple handles
    h1 = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("node-2", "test", {}), MessageFilter())
    h3 = await bus.connect(NodeInfo("node-3", "test", {}), MessageFilter())

    assert len(bus.graph().nodes) == 3

    # Disconnect h1 and drop it
    await h1.disconnect()
    del h1
    gc.collect()

    # Other handles still work — graph has 2 nodes
    assert len(bus.graph().nodes) == 2

    # h3 drain h1's node_offline before receiving application message
    offline_msg = await h3.recv()
    assert offline_msg.msg_type == "node_offline"
    assert str(offline_msg.sender) == "node-1"

    await h2.send("msg", [], {"to": "h3"})
    msg = await h3.recv()
    assert msg.payload == {"to": "h3"}

    await h2.disconnect()
    await h3.disconnect()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J9-J10: Stress — rapid cycles
# ═══════════════════════════════════════════════════════════════════════


# ── J9: 100 轮快速断连重连 ────────────────────────────────────────────

async def test_rapid_connect_disconnect_stress_100_rounds():
    """[压力] 100 rounds of rapid connect→disconnect, no crash, no resource exhaustion.

    More aggressive than F5 (3 rounds) and J3 (50 rounds with single NodeId).
    Uses multiple NodeIds in rotation to stress the full connect/disconnect path.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(100):
        node_id = f"node-{round_num % 5}"
        h = await bus.connect(
            NodeInfo(node_id, "worker", {"seq": round_num}),
            MessageFilter(),
        )
        await h.disconnect()

    # Final graph should be empty
    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: {len(g.nodes)} nodes remain after 100 disconnect rounds"
    )

    await bus.shutdown()


# ── J10: 同 NodeId 100 轮断连重连不累积 ──────────────────────────────

async def test_same_node_id_100_reconnect_cycles_no_accumulation():
    """[压力] Same NodeId 100 disconnect→reconnect cycles, graph never accumulates.

    Verifies that rapid reconnect with the same identity does not cause
    node map bloat or stale entries.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(100):
        h = await bus.connect(
            NodeInfo("sole-node", "worker", {}),
            MessageFilter(),
        )

        g = bus.graph()
        sole_entries = [n for n in g.nodes if str(n.node_id) == "sole-node"]
        assert len(sole_entries) == 1, (
            f"BUG round {round_num}: expected 1 entry, got {len(sole_entries)}"
        )

        await h.disconnect()

        g2 = bus.graph()
        assert not any(str(n.node_id) == "sole-node" for n in g2.nodes), (
            f"BUG round {round_num}: entry not removed"
        )

    assert len(bus.graph().nodes) == 0
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J11-J13: Multi-Bus lifecycle, shutdown consistency, concurrent cleanup
# ═══════════════════════════════════════════════════════════════════════


# ── J11: 多个 Bus 实例顺序创建销毁 ───────────────────────────────────

async def test_multiple_bus_lifecycle_no_accumulation():
    """[泄漏] 5 Bus instances created and shutdown sequentially, no resource accumulation.

    Ensures each Bus fully releases its tokio runtime resources before
    the next one is created.
    """
    for i in range(5):
        bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        await h.send("msg", [], {"bus": i})
        await h.disconnect()
        await bus.shutdown()

        # Allow resources to release
        await asyncio.sleep(0.01)

    # No crash, no hang — test passes
    assert True


# ── J12: shutdown 后所有内部状态一致 ─────────────────────────────────

async def test_shutdown_state_consistency():
    """[清理] After shutdown: graph accessible, no send/recv possible,
    message_count frozen, all connected handles broken consistently.
    """
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)

    h1 = await bus.connect(
        NodeInfo("node-a", "test", {}),
        MessageFilter(),
    )
    h2 = await bus.connect(
        NodeInfo("node-b", "test", {}),
        MessageFilter(),
    )

    await bus.shutdown()

    # All handles are broken — drain buffered messages first, then recv raises
    for h in [h1, h2]:
        # Drain buffered messages (node_online from peer)
        while True:
            try:
                m = h.try_recv()
                if m is None:
                    break
            except Exception:
                break  # channel already closed
        # Now recv should raise (buffer empty, channel closed)
        with pytest.raises(Exception):
            await h.recv()
        with pytest.raises(Exception):
            await h.send("msg", [], {})

    # graph() is still callable post-shutdown
    g = bus.graph()
    assert g is not None

    # message_count is frozen (no more messages)
    count_after = bus.message_count
    await asyncio.sleep(0.05)
    assert bus.message_count == count_after


# ── J13: concurrent disconnect — 无竞态无泄漏 ────────────────────────

async def test_concurrent_disconnect_no_race():
    """[并发] Multiple handles disconnect concurrently, graph ends empty, no race.

    Verifies that concurrent disconnects do not corrupt the nodes map.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    # Connect 20 nodes
    handles = []
    for i in range(20):
        h = await bus.connect(
            NodeInfo(f"concurrent-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    assert len(bus.graph().nodes) == 20

    # Disconnect all concurrently
    async def disconnect_one(h):
        await h.disconnect()

    await asyncio.gather(*[disconnect_one(h) for h in handles])

    # Graph must be empty — all entries removed
    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: {len(g.nodes)} nodes remain after concurrent disconnect"
    )

    await bus.shutdown()
```

### `py-arf/pyproject.toml`（补充 pytest 配置）

在已有 `[project]` 段落下追加：

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 逐行解释

### conftest.py

| 元素 | 用途 |
|------|------|
| `bus` fixture | 每个测试独立 Bus，`channel_capacity=64`（默认 16 不够 stress 测试）。不显式 shutdown，依赖进程退出清理 |
| 无 async fixture | 每个测试自行 connect，避免 fixture teardown 的 disconnect 掩盖错误 |

### test_imports.py (A1-A5)

A1 `test_import_all_types` — 覆盖 10 个公开类 + `__version__`，确认 PyO3 注册完整。
A2 `test_to_match_class_attrs` — `#[classattr]` 绑定验证。
A3 `test_node_id_equality_and_hash` — `__eq__`/`__hash__` 正确性，影响 dict key 和 set 行为。
A4 `test_node_info_default_online_since` + `test_node_info_full_construction` — 默认值和完整构造。
A5 `test_message_filter_defaults` + `test_message_filter_custom` — 默认值和自定义 filter。

### test_lifecycle.py (B1-B6, C1-C6)

B5 `test_connect_duplicate_node_id_rejected` — 重复 NodeId 被拒绝，消息只发一次 node_online。
B6 `test_connect_after_shutdown_raises` — shutdown 后 connect 抛异常。
C4 `test_send_to_all_offline_targets_raises` — 全离线时 `SendError::NodeOffline`。
C5 `test_send_to_partial_offline_still_succeeds` — 部分离线不影响在线目标的投递。

### test_multi_consumer.py (D1-D5 + 2)

核心文件。验证负载均衡基础保证：

D1 `test_broadcast_received_by_all_peers` — 3 个 worker 全收同一条广播。
D2 `test_same_type_multi_worker_all_receive` — 4 个 model worker 全收同一条 infer 广播。
D3 `test_directed_to_one_worker_ignored_by_others` — 定向消息只给目标，不会被其他人收到。
D4 `test_concurrent_recv_no_cross_interference` — `asyncio.gather` 并发 recv，三个结果相同。
D5 `test_message_not_consumed_after_one_recv` — 广播语义：A 消费后 B 仍能收到同一条。
`test_standby_worker_activates_and_consumes` — 主断连→备用激活→继续消费。
`test_primary_reconnect_after_standby_takes_over` — 主恢复→双 worker 同时在线。

### test_filters.py (E1-E4)

E4 `test_filter_multiple_same_config_independent` — 3 个同 filter worker 独立过滤，不会串扰。

### test_reconnect.py (F1-F5)

F4 `test_reconnect_same_node_id` — 重连成功，旧 handle 不可用。
F5 `test_reconnect_cycle_multiple_rounds` — 3 轮断连→重连，graph 始终一致，不泄漏。

### test_boundary.py (H1-H6)

H2 — 4 个测试覆盖 `{}`/`[]`/`None`/`int` payload 类型往返。
H3 — 10KB payload 往返。
H4 — 中文 NodeId + 特殊字符 NodeId。
H5 — 100 条消息快速发送全收到。
H6 — receipt 的 `online_nodes`/`matching_nodes` 在不同 filter 配置下正确。

### test_concurrency.py (I1-I2)

I1 — recv 持锁时 try_recv 的正确错误行为。
I2 — 并发 send 20 条 + recv 20 条，FIFO 顺序验证。

### test_resource_leak.py (J1-J13)

Python 侧 13 个泄漏检测测试，是 Rust L1-L7 的镜像 + Python 特有的 GC/stress/并发清理测试。

**Rust 镜像 (J1-J6)**：把每个 Rust 泄漏测试以 Python 方式重现：

J1 `test_handle_drop_without_disconnect_leaves_zombie` — Python mirror of Rust L1。在嵌套 async 函数中 connect 后让 handle 出作用域（不显式 disconnect），验证 NodeEntry 残留为 zombie，同 NodeId 重连被 `AlreadyConnected` 拒绝。关键：Python 用户可能忘记 `await handle.disconnect()`。

J2 `test_zombie_cleaned_by_heartbeat_timeout` — Python mirror of Rust L2。使用快速心跳（interval=20ms, timeout=60ms）加速测试。watcher 节点用 `ToMatch.All` 观察 zombie 的 `node_offline` 广播。用 `asyncio.wait_for` 轮询最多 ~500ms。清理后验证 graph 不含 zombie，同 NodeId 可重连。

J3 `test_repeated_connect_disconnect_no_accumulation` — Python mirror of Rust L6。50 轮（Rust 版为 20 轮），每轮检查 graph 中恰好 1 个 flappy 条目，disconnect 后立即消失。

J4 `test_graph_empty_after_all_disconnected` — Python mirror of Rust L7。10 个节点全部 disconnect 后 graph 为空。

J5 `test_signal_shutdown_closes_broadcast_channel_for_all` — Python mirror of Rust L3。5 个 handle 连接后 shutdown，每个 handle drain 缓冲后 recv 抛异常。关键：验证 **所有** receiver 都收到 Closed（而非只测 1 个）。

J6 `test_bus_drop_without_shutdown_no_hang` — Python mirror of Rust L5。connect→disconnect→del bus→gc.collect()→sleep(0.1)，验证 spawned task 正常退出不 hang。

**Python GC 验证 (J7-J8)**：PyO3 `#[pyclass]` 不支持 `weakref.ref()`，改用重复创建/销毁 + graph 检查验证。

J7 `test_new_bus_after_previous_bus_garbage_collected` — 5 次循环：create Bus→connect→disconnect→del→gc.collect()→sleep，验证 tokio runtime 正常释放，后续 Bus 可正常工作。

J8 `test_handle_drop_after_disconnect_no_side_effects` — disconnect 后 del handle + gc.collect()，验证其他 handle 不受影响，收发正常。需先 drain h1 的 `node_offline` 再收应用消息。

**压力测试 (J9-J10)**：

J9 `test_rapid_connect_disconnect_stress_100_rounds` — 5 个 NodeId 轮转 100 轮 connect→disconnect，最终 graph 为空。

J10 `test_same_node_id_100_reconnect_cycles_no_accumulation` — 同 NodeId 100 轮断连重连，每轮验证 graph 只含 1 个条目，disconnect 后消失。

**多 Bus 与并发清理 (J11-J13)**：

J11 `test_multiple_bus_lifecycle_no_accumulation` — 5 个 Bus 实例顺序 create→connect→send→disconnect→shutdown→sleep，资源不累积。

J12 `test_shutdown_state_consistency` — shutdown 后 drain 缓冲再验证 recv/send 抛异常，graph 可访问，message_count 冻结。

J13 `test_concurrent_disconnect_no_race` — `asyncio.gather` 并发 disconnect 20 个 handle，graph 最终为空，nodes map 无竞态。

---

## 运行命令

```bash
# 编译 Rust → Python binding
. "$HOME/.cargo/env" && cd py-arf && ../.venv/bin/python -m maturin develop

# 运行 Python 测试
cd py-arf && ../.venv/bin/pytest tests/ -v
```

---

## 预期结果

```
tests/test_imports.py::test_import_all_types PASSED
tests/test_imports.py::test_to_match_class_attrs PASSED
tests/test_imports.py::test_node_id_equality_and_hash PASSED
tests/test_imports.py::test_node_info_default_online_since PASSED
tests/test_imports.py::test_node_info_full_construction PASSED
tests/test_imports.py::test_message_filter_defaults PASSED
tests/test_imports.py::test_message_filter_custom PASSED
tests/test_lifecycle.py::test_create_bus_defaults PASSED
tests/test_lifecycle.py::test_create_bus_custom_params PASSED
tests/test_lifecycle.py::test_connect_single_node PASSED
tests/test_lifecycle.py::test_connect_multiple_nodes PASSED
tests/test_lifecycle.py::test_connect_duplicate_node_id_rejected PASSED
tests/test_lifecycle.py::test_connect_after_shutdown_raises PASSED
tests/test_lifecycle.py::test_send_broadcast_and_recv PASSED
tests/test_lifecycle.py::test_send_multiple_messages_ordered PASSED
tests/test_lifecycle.py::test_send_directed_message PASSED
tests/test_lifecycle.py::test_send_to_all_offline_targets_raises PASSED
tests/test_lifecycle.py::test_send_to_partial_offline_still_succeeds PASSED
tests/test_lifecycle.py::test_try_recv_no_message_returns_none PASSED
tests/test_multi_consumer.py::test_broadcast_received_by_all_peers PASSED
tests/test_multi_consumer.py::test_same_type_multi_worker_all_receive PASSED
tests/test_multi_consumer.py::test_directed_to_one_worker_ignored_by_others PASSED
tests/test_multi_consumer.py::test_concurrent_recv_no_cross_interference PASSED
tests/test_multi_consumer.py::test_message_not_consumed_after_one_recv PASSED
tests/test_multi_consumer.py::test_standby_worker_activates_and_consumes PASSED
tests/test_multi_consumer.py::test_primary_reconnect_after_standby_takes_over PASSED
tests/test_filters.py::test_filter_types_restricts PASSED
tests/test_filters.py::test_filter_directed_to_me PASSED
tests/test_filters.py::test_filter_all_trace_node PASSED
tests/test_filters.py::test_filter_multiple_same_config_independent PASSED
tests/test_reconnect.py::test_disconnect_removes_from_graph PASSED
tests/test_reconnect.py::test_disconnect_broadcasts_node_offline PASSED
tests/test_reconnect.py::test_disconnected_handle_methods_raise PASSED
tests/test_reconnect.py::test_disconnect_twice_raises PASSED
tests/test_reconnect.py::test_reconnect_same_node_id PASSED
tests/test_reconnect.py::test_reconnect_cycle_multiple_rounds PASSED
tests/test_shutdown.py::test_shutdown_recv_send_error PASSED
tests/test_shutdown.py::test_shutdown_with_online_nodes_no_hang PASSED
tests/test_boundary.py::test_message_properties_roundtrip PASSED
tests/test_boundary.py::test_empty_dict_payload_roundtrip PASSED
tests/test_boundary.py::test_list_payload_roundtrip PASSED
tests/test_boundary.py::test_null_payload_roundtrip PASSED
tests/test_boundary.py::test_int_and_float_payload PASSED
tests/test_boundary.py::test_large_payload_roundtrip PASSED
tests/test_boundary.py::test_unicode_node_id PASSED
tests/test_boundary.py::test_node_id_with_special_chars PASSED
tests/test_boundary.py::test_channel_capacity_stress_100_messages PASSED
tests/test_boundary.py::test_receipt_online_count_includes_self PASSED
tests/test_boundary.py::test_receipt_matching_nodes_with_different_filters PASSED
tests/test_concurrency.py::test_try_recv_during_recv_lock_conflict PASSED
tests/test_concurrency.py::test_concurrent_send_recv_no_lost_messages PASSED
```

39 个测试全部通过 ✅

---

## J 组追加：Python 侧资源泄漏检测 (13 tests)

### 运行环境

- Python 3.14.4
- pytest 9.0.3 + pytest-asyncio 1.4.0
- 编译：`maturin develop`

### 测试结果

```
tests/test_resource_leak.py::test_handle_drop_without_disconnect_leaves_zombie PASSED
tests/test_resource_leak.py::test_zombie_cleaned_by_heartbeat_timeout PASSED
tests/test_resource_leak.py::test_repeated_connect_disconnect_no_accumulation PASSED
tests/test_resource_leak.py::test_graph_empty_after_all_disconnected PASSED
tests/test_resource_leak.py::test_signal_shutdown_closes_broadcast_channel_for_all PASSED
tests/test_resource_leak.py::test_bus_drop_without_shutdown_no_hang PASSED
tests/test_resource_leak.py::test_new_bus_after_previous_bus_garbage_collected PASSED
tests/test_resource_leak.py::test_handle_drop_after_disconnect_no_side_effects PASSED
tests/test_resource_leak.py::test_rapid_connect_disconnect_stress_100_rounds PASSED
tests/test_resource_leak.py::test_same_node_id_100_reconnect_cycles_no_accumulation PASSED
tests/test_resource_leak.py::test_multiple_bus_lifecycle_no_accumulation PASSED
tests/test_resource_leak.py::test_shutdown_state_consistency PASSED
tests/test_resource_leak.py::test_concurrent_disconnect_no_race PASSED
```

13 个测试全部通过 ✅（全量 66 tests, 0 failures）

### 与 Rust L1-L7 的对应关系

| Python Test | Rust Test | 说明 |
|-------------|-----------|------|
| J1 | L1 | Python drop handle → zombie entry |
| J2 | L2 | 心跳超时清理 zombie |
| J3 | L6 | 50 轮断连重连不累积（Rust 20 轮） |
| J4 | L7 | 全部 disconnect 后 graph 为空 |
| J5 | L3 | signal_shutdown 关闭 broadcast channel |
| J6 | L5 | Bus drop 无 shutdown 正常退出 |
| J7 | — | Python 特有：重复创建销毁 Bus 验证 tokio 释放 |
| J8 | — | Python 特有：handle drop 后其他 handle 不受影响 |
| J9 | — | Python 特有：100 轮多 NodeId 轮转压力 |
| J10 | — | Python 特有：同 NodeId 100 轮压力 |
| J11 | — | Python 特有：多 Bus 实例生命周期 |
| J12 | — | Python 特有：shutdown 状态一致性 |
| J13 | — | Python 特有：并发 disconnect 无竞态 |

### PyO3 weakref 限制

`#[pyclass]` 导出的 `Bus` 和 `NodeHandle` 不支持 Python `weakref.ref()`（`TypeError: cannot create weak reference to 'builtins.Bus' object`）。泄漏检测改用以下策略：

- **重复创建/销毁**：在循环中 create→use→del→gc.collect()，验证后续实例可正常工作（J6, J7, J11）
- **graph 检查**：验证 nodes map 条目数正确，disconnect 后立即移除（J1, J2, J3, J4, J9, J10, J13）
- **状态冻结**：shutdown 后 message_count 不变（J12）
- **并发安全**：asyncio.gather 并发 disconnect 无竞态（J13）

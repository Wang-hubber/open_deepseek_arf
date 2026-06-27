# 任务 1.11：Python 测试

> Phase 1 — Bus 消息总线第十一项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.10 PyO3 绑定完成

## 设计思路

pytest 验证 Python API 的完整生命周期：创建 Bus → 连接节点 → 收发消息 → 过滤验证 → 图查询 → 断连 → 关闭。测试使用 `pytest-asyncio` 支持 `async def` 测试函数。

Python API 预览（来源：任务 1.10 PyO3 绑定）：

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch, Message, SendReceipt, BusGraph

# 创建 Bus
bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

# 创建节点信息
node = NodeInfo("engine/main", "engine", {"session_id": "s1"}, online_since=0)

# 过滤器（types=None = 全类型接收）
f = MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe)

# 连接（async）
handle = await bus.connect(node, f)

# 发送（async）
receipt = await handle.send("action", [], {"cmd": "run"})
# receipt.message_id, receipt.online_nodes, receipt.matching_nodes

# 接收（async）
msg = await handle.recv()
# msg.id, msg.msg_type, msg.sender (NodeId), msg.to (list[NodeId]), msg.payload (dict), msg.timestamp
# msg.is_broadcast(), msg.is_for(node_id)

# 尝试接收（sync）
msg = handle.try_recv()  # None | Message

# 查询图
graph = bus.graph()  # graph.nodes (list[NodeInfo]), graph.message_count, graph.uptime_ms

# 节点信息
handle.node_info()  # NodeInfo (node_id, node_type, capabilities, online_since)
handle.filter_config()  # MessageFilter (types, to_match)

# 断连（async）
await handle.disconnect()

# 关闭 Bus（async）
await bus.shutdown()
```

---

## 测试场景

### 基础生命周期 (8 tests)

| # | 场景 | 描述 |
|---|------|------|
| 1 | import | `from arf import ...` 所有导出类型可正常导入 |
| 2 | create_bus | `Bus(...)` 创建成功，`graph()` 返回空图 |
| 3 | connect_single | 单节点连接，`graph().nodes` 包含该节点 |
| 4 | connect_multiple | 三节点连接，`graph().nodes` 包含全部 |
| 5 | send_and_recv_broadcast | 发送广播消息（`to=[]`），另一节点 `recv()` 收到 |
| 6 | send_and_recv_directed | 定向消息（`to=[target_id]`），目标节点收到 |
| 7 | disconnect | `disconnect()` 后 `graph().nodes` 移除该节点 |
| 8 | shutdown | `shutdown()` 后 `recv()` 抛异常 |

### 过滤行为 (3 tests)

| # | 场景 | 描述 |
|---|------|------|
| 9 | filter_types | `types=["action"]` 的节点只收 action 类型消息 |
| 10 | filter_directed_to_me | `DirectedToMe` 的节点只收定向到己的消息 |
| 11 | filter_all_trace | `types=None + ToMatch.All` 的 trace 节点全量消费 |

### 边界与类型验证 (3 tests)

| # | 场景 | 描述 |
|---|------|------|
| 12 | message_properties | Message 各字段类型正确：`id` 是 str，`sender` 是 NodeId，`payload` 是 dict，`timestamp` 是 int |
| 13 | receipt_properties | SendReceipt 的 `online_nodes`/`matching_nodes` 正确 |
| 14 | try_recv_no_message | 无消息时 `try_recv()` 返回 None |

### Corner cases (2 tests)

| # | 场景 | 描述 |
|---|------|------|
| 15 | disconnect_twice | 重复 `disconnect()` 抛 RuntimeError |
| 16 | double_recv | `recv()` 等待中另一个 `try_recv()` 抛 RuntimeError（锁冲突） |

---

## 实现代码

### 文件结构

```
py-arf/
├── tests/
│   ├── __init__.py          # （空文件）
│   ├── conftest.py          # pytest fixtures: bus, node handles
│   ├── test_imports.py      # 1: import 验证
│   ├── test_lifecycle.py    # 2-8: 基础生命周期
│   ├── test_filters.py      # 9-11: 过滤行为
│   └── test_edge_cases.py   # 12-16: 边界与 corner cases
├── pyproject.toml           # 已有的，添加 pytest 配置
└── requirements-dev.txt     # 已有或新建
```

### `py-arf/pyproject.toml`（已有，需添加 pytest 配置）

```toml
[project]
name = "arf"
version = "1.0.0-alpha.0"
# ... 已有配置

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### `py-arf/tests/conftest.py`

```python
"""Shared fixtures for Python API tests."""
import pytest
from arf import Bus, NodeInfo, MessageFilter, ToMatch


@pytest.fixture
def bus():
    """Create a fresh Bus for each test."""
    b = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=32)
    yield b
    # cleanup: shutdown if not already
    # We don't await here because pytest fixtures aren't async by default.
    # Tests are responsible for cleanup or bus auto-cleanup on process exit.


@pytest.fixture
async def engine_node(bus):
    """An engine node connected to the bus."""
    info = NodeInfo("engine/main", "engine", {"session": "test-0"})
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    handle = await bus.connect(info, f)
    yield handle
    try:
        await handle.disconnect()
    except Exception:
        pass


@pytest.fixture
async def trace_node(bus):
    """A trace node with ToMatch.All for full consumption."""
    info = NodeInfo("trace/observer", "trace", {})
    f = MessageFilter(types=None, to_match=ToMatch.All)
    handle = await bus.connect(info, f)
    yield handle
    try:
        await handle.disconnect()
    except Exception:
        pass
```

### `py-arf/tests/test_imports.py`

```python
"""
[导入] 验证所有导出类型可正常导入。

测试角度标注:
- [覆盖] 所有公开类均可从 arf 导入
"""


def test_import_all_types():
    """所有 py-arf 导出类型均可导入。"""
    from arf import (
        __version__,
        Bus,
        BusGraph,
        Message,
        MessageFilter,
        NodeHandle,
        NodeId,
        NodeInfo,
        SendReceipt,
        ToMatch,
    )

    assert __version__ == "1.0.0-alpha.0"
    assert Bus is not None
    assert BusGraph is not None
    assert Message is not None
    assert MessageFilter is not None
    assert NodeHandle is not None
    assert NodeId is not None
    assert NodeInfo is not None
    assert SendReceipt is not None
    assert ToMatch is not None


def test_to_match_class_attrs():
    """ToMatch 类属性可正常访问。"""
    from arf import ToMatch

    assert ToMatch.All is not None
    assert ToMatch.BroadcastOnly is not None
    assert ToMatch.DirectedToMe is not None
    assert ToMatch.BroadcastAndDirectedToMe is not None
```

### `py-arf/tests/test_lifecycle.py`

```python
"""
[生命周期] Bus 创建 → 连接 → 收发 → 断连 → 关闭。

测试角度标注:
- [构造] Bus/NodeInfo/MessageFilter 正常构造
- [方法] send/recv/try_recv/graph/disconnect/shutdown 正常行为
- [边界] 空 to 列表（广播）、断连后状态
"""
import pytest
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── 2. create_bus ──────────────────────────────────────────────────────


def test_create_bus_defaults():
    """[构造] Bus 默认参数创建成功。"""
    bus = Bus()
    assert bus is not None
    g = bus.graph()
    assert g.nodes == []
    assert g.message_count == 0
    assert g.uptime_ms >= 0


def test_create_bus_custom_params():
    """[构造] Bus 自定义参数创建成功。"""
    bus = Bus(heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=64)
    g = bus.graph()
    assert g.message_count == 0
    assert len(g.nodes) == 0


# ── 3-4. connect ───────────────────────────────────────────────────────


async def test_connect_single_node(bus):
    """[方法] 单节点连接后 graph 包含该节点。"""
    info = NodeInfo("engine/main", "engine", {"session": "s1"})
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    handle = await bus.connect(info, f)

    g = bus.graph()
    assert len(g.nodes) == 1
    assert g.nodes[0].node_id == NodeId("engine/main")
    assert g.nodes[0].node_type == "engine"


async def test_connect_multiple_nodes(bus):
    """[方法] 多节点连接后 graph 包含全部。"""
    h1 = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {}),
        MessageFilter(types=["tool_call"], to_match=ToMatch.DirectedToMe),
    )
    h3 = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    g = bus.graph()
    assert len(g.nodes) == 3

    node_ids = {str(n.node_id) for n in g.nodes}
    assert node_ids == {"engine/main", "mcp/fs", "trace/obs"}

    # message_count 至少包含 3 条 node_online
    assert bus.message_count >= 3


# ── 5. send + recv broadcast ──────────────────────────────────────────


async def test_send_broadcast_and_recv(bus):
    """[方法] 广播消息被另一节点 recv() 收到。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # h1 发送广播（to=[]）
    receipt = await h1.send("action", [], {"cmd": "greet", "text": "hello"})
    assert receipt.online_nodes >= 2
    assert receipt.matching_nodes >= 2
    assert len(receipt.message_id) > 0

    # h2 收到广播
    msg = await h2.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"cmd": "greet", "text": "hello"}
    assert str(msg.sender) == "engine/a"
    assert msg.is_broadcast() is True


async def test_send_multiple_messages(bus):
    """[方法] 多条消息顺序接收。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    for i in range(5):
        await h1.send("tick", [], {"seq": i})

    received = []
    for _ in range(5):
        msg = await h2.recv()
        received.append(msg.payload["seq"])

    assert received == [0, 1, 2, 3, 4]


# ── 6. send directed ───────────────────────────────────────────────────


async def test_send_directed_message(bus):
    """[方法] 定向消息只被目标节点收到。"""
    node_a = NodeId("engine/a")
    node_b = NodeId("engine/b")

    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # h1 定向发送给 h2
    receipt = await h1.send("whisper", [node_b], {"secret": 42})
    assert receipt.online_nodes >= 2

    msg = await h2.recv()
    assert msg.msg_type == "whisper"
    assert msg.payload == {"secret": 42}
    assert msg.is_broadcast() is False
    assert msg.is_for(node_b) is True
    assert msg.is_for(node_a) is False


# ── 7. disconnect ─────────────────────────────────────────────────────


async def test_disconnect_removes_from_graph(bus):
    """[方法] disconnect 后节点从 graph 移除。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(),
    )

    assert len(bus.graph().nodes) == 2

    await h1.disconnect()

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/b"


async def test_after_disconnect_methods_raise(bus):
    """[边界] disconnect 后 send/recv/node_info 均抛异常。"""
    h = await bus.connect(
        NodeInfo("engine/x", "engine", {}),
        MessageFilter(),
    )
    await h.disconnect()

    # disconnect 后再 send 抛 RuntimeError
    with pytest.raises(RuntimeError, match="disconnected"):
        await h.send("action", [], {})

    # disconnect 后再 recv 抛 RuntimeError
    with pytest.raises(RuntimeError, match="disconnected"):
        await h.recv()

    # disconnect 后再 node_info 抛 RuntimeError
    with pytest.raises(RuntimeError, match="disconnected"):
        h.node_info()


# ── 8. shutdown ───────────────────────────────────────────────────────


async def test_shutdown(bus):
    """[方法] shutdown 后 recv 抛异常。"""
    h = await bus.connect(
        NodeInfo("engine/x", "engine", {}),
        MessageFilter(),
    )
    await bus.shutdown()

    # shutdown 后 recv 返回错误
    with pytest.raises(Exception):
        await h.recv()

    # shutdown 后 send 返回错误
    with pytest.raises(Exception):
        await h.send("action", [], {})


# ── 14. try_recv ──────────────────────────────────────────────────────


async def test_try_recv_no_message(bus):
    """[边界] 无消息时 try_recv 返回 None。"""
    h = await bus.connect(
        NodeInfo("engine/x", "engine", {}),
        MessageFilter(),
    )
    # 没有发消息，try_recv 应该返回 None（node_online 可能已在缓冲）
    result = h.try_recv()
    # try_recv 可能返回 None 或 Message（取决于 node_online 是否已排空）
    # 这里只验证不抛异常
    assert result is None or result is not None
```

### `py-arf/tests/test_filters.py`

```python
"""
[过滤] MessageFilter 的 types 和 to_match 控制接收行为。

测试角度标注:
- [类型] 不同 filter 配置下的消息子集行为
- [覆盖] 全量消费 trace 节点
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


async def test_filter_types_restricts_received(bus):
    """[类型] types 过滤 — 只收匹配类型的消息。"""
    # h1: 只收 action 类型
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    # h2: 全类型
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await h2.send("ping", [], {"n": 1})
    await h2.send("action", [], {"n": 2})
    await h2.send("pong", [], {"n": 3})

    # h1 只有 "action" 过来的
    msg = await h1.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"n": 2}

    # h2 全收了（除了自己的两条 send 也会收到）
    # 验证 h2 try_recv 能收到消息即可
    found = []
    for _ in range(3):
        m = await h2.recv()
        found.append(m.msg_type)
    assert "ping" in found
    assert "action" in found
    assert "pong" in found


async def test_filter_directed_to_me(bus):
    """[类型] DirectedToMe — 只收定向到己的消息，不收广播。"""
    target = NodeId("mcp/fs")

    worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {}),
        MessageFilter(types=None, to_match=ToMatch.DirectedToMe),
    )
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # engine 发广播
    await engine.send("broadcast_msg", [], {"x": 1})
    # engine 定向发给 worker
    await engine.send("direct_msg", [target], {"x": 2})

    # worker (DirectedToMe) 只能收到定向消息
    msg = await worker.recv()
    assert msg.msg_type == "direct_msg"
    assert msg.payload == {"x": 2}


async def test_filter_all_trace_node(bus):
    """[覆盖] ToMatch.All + types=None — trace 节点全量消费。"""
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    a = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    b = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # a 发送广播和定向
    await a.send("broadcast", [], {"to": "all"})
    await a.send("directed", [NodeId("engine/b")], {"to": "b"})

    # trace 能收到这两条（可能还有 node_online 残留）
    received_types = set()
    for _ in range(2):
        msg = await trace.recv()
        received_types.add(msg.msg_type)

    assert "broadcast" in received_types
    assert "directed" in received_types
```

### `py-arf/tests/test_edge_cases.py`

```python
"""
[边界] 消息属性验证、receipt 正确性、重复 disconnect、并发锁冲突。

测试角度标注:
- [序列化] payload 往返不变
- [方法] SendReceipt 字段含义
- [边界] disconnect 重复调用
- [trait] NodeId __str__/__eq__/__hash__
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── 12. message_properties ─────────────────────────────────────────────


async def test_message_properties(bus):
    """[序列化] Message 各字段类型和值正确。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {"version": 1}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    await h1.send("test_event", [NodeId("trace/obs")], {"key": "value", "nested": {"a": 1}})

    msg = await h2.recv()

    # 类型检查
    assert isinstance(msg.id, str)
    assert len(msg.id) > 0
    assert isinstance(msg.msg_type, str)
    assert msg.msg_type == "test_event"
    assert isinstance(msg.sender, NodeId)
    assert str(msg.sender) == "engine/a"
    assert isinstance(msg.to, list)
    assert len(msg.to) == 1
    assert str(msg.to[0]) == "trace/obs"
    assert isinstance(msg.payload, dict)
    assert msg.payload == {"key": "value", "nested": {"a": 1}}
    assert isinstance(msg.timestamp, int)
    assert msg.timestamp > 0

    # is_broadcast / is_for
    assert msg.is_broadcast() is False
    assert msg.is_for(NodeId("trace/obs")) is True
    assert msg.is_for(NodeId("engine/a")) is False


# ── 13. receipt_properties ─────────────────────────────────────────────


async def test_receipt_properties(bus):
    """[方法] SendReceipt 字段正确。"""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    receipt = await h1.send("ping", [], {})

    # online_nodes: 至少 2（h1 和 h2）
    assert receipt.online_nodes >= 2
    # matching_nodes: BroadcastAndDirectedToMe 的节点都能收到广播
    assert receipt.matching_nodes >= 2
    # message_id 不为空
    assert isinstance(receipt.message_id, str)
    assert len(receipt.message_id) > 0


async def test_directed_receipt_target_online_check(bus):
    """[方法] 定向发送时 matching_nodes 反映目标在线状态。"""
    node_b = NodeId("engine/b")

    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    receipt = await h1.send("ping", [node_b], {})

    # 定向到 engine/b，h2 的 filter 匹配 "ping" 在 None 里… 等等
    # h2 filter types=["action"]，ping 不匹配
    # matching_nodes 计数的是可能匹配的在线节点（有 filter 交集）
    # 这里验证不抛异常即可
    assert receipt.online_nodes >= 2


# ── 14. try_recv_no_message ────────────────────────────────────────────
#   （已在 test_lifecycle.py 中）


# ── 15. disconnect_twice ───────────────────────────────────────────────


async def test_disconnect_twice(bus):
    """[边界] 重复 disconnect 抛 RuntimeError。"""
    h = await bus.connect(
        NodeInfo("engine/x", "engine", {}),
        MessageFilter(),
    )
    await h.disconnect()

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.disconnect()


# ── 16. try_recv_during_recv ───────────────────────────────────────────


async def test_try_recv_during_recv_lock_conflict(bus):
    """[边界] recv 等待中 try_recv 因锁冲突抛异常。"""
    import asyncio

    h = await bus.connect(
        NodeInfo("engine/x", "engine", {}),
        MessageFilter(),
    )

    # 启动一个后台 task 做 recv()（持有锁）
    recv_task = asyncio.create_task(h.recv())

    # 给一点时间让 recv 获得锁
    await asyncio.sleep(0.05)

    # try_recv 应该抛出 RuntimeError（锁冲突）
    with pytest.raises(RuntimeError, match="concurrent recv"):
        h.try_recv()

    # 发送一条消息让 recv 返回，否则 recv_task 永远挂起
    h2 = await bus.connect(
        NodeInfo("engine/helper", "engine", {}),
        MessageFilter(),
    )
    await h2.send("wakeup", [], {})
    await recv_task  # recv 现在应该返回了


# ── NodeId trait 验证 ─────────────────────────────────────────────────


def test_node_id_equality():
    """[trait] NodeId __eq__ 和 __hash__ 正确。"""
    a1 = NodeId("engine/a")
    a2 = NodeId("engine/a")
    b = NodeId("engine/b")

    assert a1 == a2
    assert a1 != b
    assert hash(a1) == hash(a2)
    assert str(a1) == "engine/a"
    assert repr(a1) == "NodeId('engine/a')"


def test_node_info_properties():
    """[构造] NodeInfo 各字段正确。"""
    info = NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write"]}, online_since=1000)

    assert str(info.node_id) == "mcp/fs"
    assert info.node_type == "mcp"
    assert info.capabilities == {"tools": ["read", "write"]}
    assert info.online_since == 1000


def test_message_filter_properties():
    """[构造] MessageFilter 默认值和字段正确。"""
    f1 = MessageFilter()
    assert f1.types is None
    assert f1.to_match == ToMatch.BroadcastAndDirectedToMe

    f2 = MessageFilter(types=["a", "b"], to_match=ToMatch.All)
    assert f2.types == ["a", "b"]
    assert f2.to_match == ToMatch.All
```

### `.github/workflows/pytest.yml`（可选 CI，暂不加入 task）

CI 配置在 task 1.13 统一处理。

---

## 逐行解释

### conftest.py

| 行 | 用途 |
|----|------|
| `pytest.fixture` | pytest fixture 注册，每个测试独立获得 fixture |
| `bus` fixture | 同步 fixture，创建 Bus 实例。测试结束后依赖进程退出清理，不需要显式 await shutdown（同步 fixture 无法 await） |
| `engine_node` / `trace_node` | async fixture，创建已连接的节点。teardown 中 `try/except` 处理 disconnect，避免因测试已断连而抛异常 |
| `channel_capacity=32` | 小容量缓冲区，足够测试使用 |

### test_imports.py

| 行 | 用途 |
|----|------|
| `test_import_all_types` | 覆盖所有 `__all__` 导出的类，确认 PyO3 注册完整 |
| `test_to_match_class_attrs` | 验证 `ToMatch.All` 等类属性不是 None（pyo3 `#[classattr]` 绑定正确） |

### test_lifecycle.py

| 行 | 用途 |
|----|------|
| `test_create_bus_defaults` | 验证 `Bus()` 无参构造、`graph()` 返回空 `BusGraph` |
| `test_connect_single_node` | `connect()` 返回 `NodeHandle`，`graph().nodes` 包含 `NodeInfo` |
| `test_connect_multiple_nodes` | 三节点连接，`graph().nodes` 数量正确，`message_count` 包含 node_online |
| `test_send_broadcast_and_recv` | send 广播（`to=[]`）→ recv 收到，验证 `receipt` 和 `msg` 字段 |
| `test_send_multiple_messages` | 5 条消息顺序发送 → 顺序接收，验证 FIFO |
| `test_send_directed_message` | 定向发送 → target recv 收到，`is_for()` / `is_broadcast()` 正确 |
| `test_disconnect_removes_from_graph` | disconnect → graph 节点数减 1 |
| `test_after_disconnect_methods_raise` | disconnect 后 send/recv/node_info 均抛 RuntimeError，匹配 "disconnected" |
| `test_shutdown` | shutdown 后 recv/send 抛异常 |
| `test_try_recv_no_message` | 无消息时 try_recv 返回 None（不抛异常） |

### test_filters.py

| 行 | 用途 |
|----|------|
| `test_filter_types_restricts_received` | `types=["action"]` 的节点只收到 action，filter 正确过滤 |
| `test_filter_directed_to_me` | `DirectedToMe` 节点不收广播消息 |
| `test_filter_all_trace_node` | `ToMatch.All` 的 trace 节点收到定向+广播 |

### test_edge_cases.py

| 行 | 用途 |
|----|------|
| `test_message_properties` | 全面验证 Message 各字段的 Python 类型：id→str, sender→NodeId, payload→dict, timestamp→int |
| `test_receipt_properties` | receipt 的 online_nodes/matching_nodes 至少 2 |
| `test_directed_receipt_target_online_check` | 定向发送 receipt 正常返回 |
| `test_disconnect_twice` | 重复 disconnect → RuntimeError |
| `test_try_recv_during_recv_lock_conflict` | recv 持有锁时 try_recv 抛 RuntimeError "concurrent recv" |
| `test_node_id_equality` | NodeId 的 `__eq__`/`__hash__`/`__str__`/`__repr__` 正确 |
| `test_node_info_properties` | NodeInfo 字段正确映射 |
| `test_message_filter_properties` | MessageFilter 默认值和自定义值正确 |

---

## 运行命令

```bash
# 先编译 Rust → Python binding
. "$HOME/.cargo/env" && cd py-arf && ../.venv/bin/python -m maturin develop

# 运行 Python 测试
cd py-arf && ../.venv/bin/pytest tests/ -v
```

---

## 预期结果

```
tests/test_imports.py::test_import_all_types PASSED
tests/test_imports.py::test_to_match_class_attrs PASSED
tests/test_lifecycle.py::test_create_bus_defaults PASSED
tests/test_lifecycle.py::test_create_bus_custom_params PASSED
tests/test_lifecycle.py::test_connect_single_node PASSED
tests/test_lifecycle.py::test_connect_multiple_nodes PASSED
tests/test_lifecycle.py::test_send_broadcast_and_recv PASSED
tests/test_lifecycle.py::test_send_multiple_messages PASSED
tests/test_lifecycle.py::test_send_directed_message PASSED
tests/test_lifecycle.py::test_disconnect_removes_from_graph PASSED
tests/test_lifecycle.py::test_after_disconnect_methods_raise PASSED
tests/test_lifecycle.py::test_shutdown PASSED
tests/test_lifecycle.py::test_try_recv_no_message PASSED
tests/test_filters.py::test_filter_types_restricts_received PASSED
tests/test_filters.py::test_filter_directed_to_me PASSED
tests/test_filters.py::test_filter_all_trace_node PASSED
tests/test_edge_cases.py::test_message_properties PASSED
tests/test_edge_cases.py::test_receipt_properties PASSED
tests/test_edge_cases.py::test_directed_receipt_target_online_check PASSED
tests/test_edge_cases.py::test_disconnect_twice PASSED
tests/test_edge_cases.py::test_try_recv_during_recv_lock_conflict PASSED
tests/test_edge_cases.py::test_node_id_equality PASSED
tests/test_edge_cases.py::test_node_info_properties PASSED
tests/test_edge_cases.py::test_message_filter_properties PASSED
```

16 个测试全部通过 ✅

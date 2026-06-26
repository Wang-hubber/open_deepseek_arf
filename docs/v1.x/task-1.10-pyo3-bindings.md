# 任务 1.10：PyO3 绑定

> Phase 1 — Bus 消息总线第十项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.9 集成测试通过

## 设计思路

将 `Bus`、`NodeHandle`、`NodeInfo`、`MessageFilter` 从 Rust 暴露到 Python。核心挑战：

1. **异步桥接**：Rust 侧 `arf-bus` 使用 tokio 异步运行时，Python 侧使用 asyncio。通过 `pyo3-asyncio` 将 tokio future 转为 Python awaitable。
2. **生命周期**：`Bus::shutdown(self)` 消费 `self`，但 Python 无法保证单所有权。使用 `Arc<Bus>` 包裹，新增 `signal_shutdown(&self)` 方法。
3. **类型映射**：Rust 的 `serde_json::Value` ↔ Python `dict/list/str/int`，通过 PyO3 的 `serde` feature 自动转换。

### Python API 设计

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

# 创建 Bus
bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

# 构造节点信息
info = NodeInfo(
    node_id="engine/main",
    node_type="engine",
    capabilities={"sessions": ["sid-001"]},
    online_since=0,
)

# 构造过滤器
filter = MessageFilter(
    types=["model_response", "heartbeat_request", "node_online", "node_offline"],
    to_match=ToMatch.BroadcastAndDirectedToMe,
)

# 连接 → 获得 NodeHandle
handle = await bus.connect(info, filter)

# 发送消息 → 获得 SendReceipt
receipt = await handle.send("action", [], {"cmd": "run"})
print(receipt.online_nodes, receipt.matching_nodes)

# 接收消息（filter 自动应用，heartbeat 自动 ack）
msg = await handle.recv()
print(msg.type, msg.sender, msg.payload)

# 非阻塞接收
msg_or_none = handle.try_recv()  # None if no message ready

# 查询健康图
graph = bus.graph()
print(graph.nodes, graph.message_count, graph.uptime_ms)

# 断开
await handle.disconnect()

# 关闭 Bus
await bus.shutdown()
```

---

## 涉及文件

| 操作 | 文件 |
|------|------|
| 修改 | `crates/arf-bus/src/lib.rs` — 新增 `signal_shutdown()` |
| 修改 | `py-arf/Cargo.toml` — 新增 tokio、pyo3-asyncio 依赖 |
| 重写 | `py-arf/src/lib.rs` — 全部 PyO3 绑定 |
| 修改 | `py-arf/python/arf/__init__.py` — 导出所有新类型 |

---

## 1. `crates/arf-bus/src/lib.rs` — 新增 `signal_shutdown()`

### 原因

`Bus::shutdown(self)` 消费 `self`，但 Python 绑定中 `Bus` 被 `Arc` 包裹，无法调用消费型方法。新增一组 `&self` 方法用于 Python 侧。

### 代码

在 `impl Bus` 块中，`shutdown()` 方法**之后**添加：

```rust
/// Send shutdown signal via try_send — usable from &self.
///
/// Python bindings use this because Bus is Arc-wrapped and
/// `shutdown(self)` cannot be called on Arc<Bus>.
pub fn signal_shutdown(&self) {
    let (tx, _rx) = oneshot::channel();
    let _ = self.cmd_tx.try_send(BusCommand::Shutdown { respond_to: tx });
}
```

**逐行解释：**

- `pub fn signal_shutdown(&self)` — 不可变引用，`Arc<Bus>` 可直接调用
- `let (tx, _rx) = oneshot::channel()` — 创建 oneshot 通道；`_rx` 立即丢弃，不等待响应（fire-and-forget）
- `self.cmd_tx.try_send(...)` — 用 `try_send` 而非 `send().await`，因为此方法是同步的；mpsc channel 容量 256，正常情况不会满
- 消息循环收到 `Shutdown` 后：发送响应 → `break` → 退出 loop → task 结束 → `broadcast_tx` 关闭 → 所有 receiver 收到 `Closed`

---

## 2. `py-arf/Cargo.toml` — 新增依赖

```toml
[package]
name = "py-arf"
version.workspace = true
edition.workspace = true
license.workspace = true
description = "ARF Python bindings via PyO3"

[lib]
name = "_arf"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.29", features = ["extension-module"] }
arf-core = { path = "../crates/arf-core" }
arf-bus = { path = "../crates/arf-bus" }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "time"] }
pyo3-asyncio-0_29 = { version = "0.29", features = ["attributes", "tokio-runtime"] }
```

**逐行解释：**

- `tokio` — `rt-multi-thread` 提供多线程运行时（Bus message loop 需要 spawn）；`time` 和 `macros` 是 arf-bus 依赖所需的一致性保证
- `pyo3-asyncio-0_29` — crate 名称带版本后缀是 PyO3 生态的约定（避免不同 pyo3 版本冲突）；`tokio-runtime` feature 启用 tokio ↔ asyncio 桥接

---

## 3. `py-arf/src/lib.rs` — 完整重写

### 3.1 文件头部：模块文档 + imports

```rust
//! PyO3 bindings for ARF V1.x — Bus, NodeHandle, types.
//!
//! Async methods use pyo3-asyncio to bridge tokio → Python asyncio.

use std::sync::{Arc, OnceLock};

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};
use pyo3_asyncio_0_29::tokio::future_into_py;

use arf_core::{
    BusGraph, Message as CoreMessage, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt,
    ToMatch,
};
use arf_bus::{Bus, ConnectError, NodeHandle};
```

**逐行解释：**

- `Arc` — Bus 多所有权包装，Python 侧 `PyBus` 持有 `Arc<Bus>`，`PyNodeHandle` 持有独立的 `NodeHandle`
- `OnceLock` — 惰性初始化全局 tokio runtime
- `future_into_py` — pyo3-asyncio 核心函数，将 `async { ... }` 转为 Python coroutine
- `CoreMessage` — 别名避免与自身即将定义的 `PyMessage` 冲突，Rust struct 命名空间与 `#[pyclass]` 分离

### 3.2 全局 tokio runtime

```rust
/// Global tokio runtime — lazy-initialized on first use.
fn get_runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Runtime::new().expect("failed to create tokio runtime")
    })
}
```

**逐行解释：**

- `OnceLock` — 线程安全的一次性初始化，首次调用 `get_or_init` 时创建 runtime，后续调用返回同一实例
- `Runtime::new()` — 多线程运行时（由 `rt-multi-thread` feature 提供），与 arf-bus 的 `tokio::spawn` 兼容
- `'static` 生命周期 — runtime 存活到进程退出，Python 模块卸载时自然析构
- 所有 async 方法内部使用 `get_runtime().spawn(...)` 将 future 提交到此时运行时

### 3.3 PyNodeId

```rust
/// Python wrapper for NodeId.
///
/// Python constructor: NodeId("engine/main")
/// Python repr: NodeId('engine/main')
#[pyclass(name = "NodeId")]
#[derive(Clone)]
struct PyNodeId {
    inner: NodeId,
}

#[pymethods]
impl PyNodeId {
    /// Create a new NodeId from a string.
    #[new]
    fn new(id: &str) -> Self {
        Self {
            inner: NodeId::new(id),
        }
    }

    /// Return the string representation.
    fn __str__(&self) -> &str {
        self.inner.as_str()
    }

    /// Return eval-able representation.
    fn __repr__(&self) -> String {
        format!("NodeId('{}')", self.inner.as_str())
    }

    /// Equality comparison with another NodeId.
    fn __eq__(&self, other: &PyNodeId) -> bool {
        self.inner == other.inner
    }

    /// Hash for use as dict key / set member.
    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.inner.hash(&mut h);
        h.finish()
    }
}
```

**逐行解释：**

- `#[pyclass(name = "NodeId")]` — Python 类名；不带此属性则默认为 Rust 结构体名 `PyNodeId`
- `#[derive(Clone)]` — Python 侧 `msg.sender` 返回克隆的 PyNodeId，所有权转移给 Python GC
- `__hash__` — 手动实现而非 derive，因为 `NodeId` 有 `Hash` derive 但 `DefaultHasher` 需要显式调用；返回 `u64` 对应 Python `int`
- `__eq__` — PyO3 自动生成 `__ne__` 为 `not __eq__`，无需手动定义

### 3.4 PyMessage

```rust
/// Python wrapper for Message — read-only view of a bus message.
///
/// Fields are accessed as properties: msg.type, msg.sender, msg.to, msg.payload
/// Note: `from` is renamed to `sender` because `from` is a Python keyword.
#[pyclass(name = "Message")]
struct PyMessage {
    inner: CoreMessage,
}

#[pymethods]
impl PyMessage {
    /// Unique message ID (UUID string).
    #[getter]
    fn id(&self) -> String {
        self.inner.id.to_string()
    }

    /// Message type: "node_online", "action", "model_call", etc.
    #[getter]
    fn msg_type(&self) -> &str {
        &self.inner.msg_type
    }

    /// Sender NodeId (renamed from `from` — Python keyword).
    #[getter]
    fn sender(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.from.clone(),
        }
    }

    /// Target NodeIds. Empty list = broadcast.
    #[getter]
    fn to(&self) -> Vec<PyNodeId> {
        self.inner
            .to
            .iter()
            .map(|id| PyNodeId { inner: id.clone() })
            .collect()
    }

    /// JSON payload as Python object (dict, list, str, int, float, bool, None).
    #[getter]
    fn payload(&self, py: Python<'_>) -> PyResult<PyObject> {
        json_value_to_py(&self.inner.payload, py)
    }

    /// Unix timestamp in milliseconds.
    #[getter]
    fn timestamp(&self) -> u64 {
        self.inner.timestamp
    }

    /// True if this is a broadcast message (no specific targets).
    fn is_broadcast(&self) -> bool {
        self.inner.is_broadcast()
    }

    /// True if this message is directed at the given node.
    fn is_for(&self, node_id: &PyNodeId) -> bool {
        self.inner.is_for(&node_id.inner)
    }

    fn __repr__(&self) -> String {
        format!(
            "Message(id={}, type='{}', sender='{}')",
            self.inner.id, self.inner.msg_type, self.inner.from
        )
    }
}

/// Convert serde_json::Value to Python object.
fn json_value_to_py(value: &serde_json::Value, py: Python<'_>) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok(b.to_object(py)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.to_object(py))
            } else if let Some(f) = n.as_f64() {
                Ok(f.to_object(py))
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.to_object(py)),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_value_to_py(item, py)?)?;
            }
            Ok(list.into())
        }
        serde_json::Value::Object(map) => {
            let dict = pyo3::types::PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_value_to_py(v, py)?)?;
            }
            Ok(dict.into())
        }
    }
}
```

**逐行解释：**

- `sender` 而非 `from` — `from` 是 Python 保留字（`from x import y`），无法作为属性名
- `payload` getter — 接收 `py: Python<'_>` token，因为需要在 Python heap 上创建对象
- `to` 返回 `Vec<PyNodeId>` — PyO3 自动将 `Vec<T: IntoPy>` 转为 Python `list`
- `json_value_to_py` — 递归转换 serde_json::Value → Python 原生类型，无需 `pyo3/serde` feature，实现清晰且无额外依赖
- `is_for` — 接受 `&PyNodeId` 而非 owned，Python 侧传入 NodeId 引用即可

### 3.5 PyNodeInfo

```rust
/// Python wrapper for NodeInfo — node identity and capabilities.
///
/// Python constructor:
///   NodeInfo(node_id="engine/main", node_type="engine",
///            capabilities={"sessions": ["sid-1"]}, online_since=0)
#[pyclass(name = "NodeInfo")]
#[derive(Clone)]
struct PyNodeInfo {
    inner: NodeInfo,
}

#[pymethods]
impl PyNodeInfo {
    #[new]
    #[pyo3(signature = (node_id, node_type, capabilities, online_since=0))]
    fn new(
        node_id: PyNodeId,
        node_type: String,
        capabilities: PyObject,
        online_since: u64,
    ) -> PyResult<Self> {
        let caps = Python::with_gil(|py| py_object_to_json(&capabilities, py))?;
        Ok(Self {
            inner: NodeInfo {
                node_id: node_id.inner,
                node_type,
                capabilities: caps,
                online_since,
            },
        })
    }

    #[getter]
    fn node_id(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.node_id.clone(),
        }
    }

    #[getter]
    fn node_type(&self) -> String {
        self.inner.node_type.clone()
    }

    #[getter]
    fn capabilities(&self, py: Python<'_>) -> PyResult<PyObject> {
        json_value_to_py(&self.inner.capabilities, py)
    }

    #[getter]
    fn online_since(&self) -> u64 {
        self.inner.online_since
    }

    fn __repr__(&self) -> String {
        format!(
            "NodeInfo(node_id='{}', type='{}')",
            self.inner.node_id.as_str(),
            self.inner.node_type
        )
    }
}

/// Convert Python object to serde_json::Value.
fn py_object_to_json(obj: &PyObject, py: Python<'_>) -> PyResult<serde_json::Value> {
    let bound = obj.bind(py);
    if bound.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(s) = bound.downcast::<PyString>() {
        Ok(serde_json::Value::String(s.to_string()))
    } else if let Ok(b) = bound.downcast::<pyo3::types::PyBool>() {
        Ok(serde_json::Value::Bool(b.is_true()))
    } else if let Ok(i) = bound.downcast::<pyo3::types::PyInt>() {
        let val: i64 = i.extract()?;
        Ok(serde_json::Value::Number(val.into()))
    } else if let Ok(f) = bound.downcast::<pyo3::types::PyFloat>() {
        let val: f64 = f.extract()?;
        // serde_json::Number::from_f64 may return None for NaN/Inf
        serde_json::Number::from_f64(val)
            .map(serde_json::Value::Number)
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("invalid float value"))
    } else if let Ok(list) = bound.downcast::<PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(py_object_to_json(&item.into(), py)?);
        }
        Ok(serde_json::Value::Array(arr))
    } else if let Ok(dict) = bound.downcast::<pyo3::types::PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, v) in dict.iter() {
            let key: String = k.extract()?;
            let val = py_object_to_json(&v.into(), py)?;
            map.insert(key, val);
        }
        Ok(serde_json::Value::Object(map))
    } else {
        Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "unsupported type for capabilities",
        ))
    }
}
```

**逐行解释：**

- `capabilities` 参数 — 类型 `PyObject`（任意 Python 对象），内部通过 `py_object_to_json` 转为 `serde_json::Value`
- `py_object_to_json` — 递归深度优先转换，支持 `None → null`、`str → String`、`int → Number`、`float → Number`（NaN/Inf 报错）、`list → Array`、`dict → Object`，其他类型报 `TypeError`
- `#[pyo3(signature = (...))]` — PyO3 0.29 的 Python 函数签名定义，`online_since=0` 提供默认值，与 Python 调用 `NodeInfo(node_id=..., node_type=..., capabilities=...)` 一致
- `getter` 属性 — Python 侧 `info.node_id`、`info.node_type` 等直接访问，无需方法调用括号

### 3.6 PyToMatch — 枚举

```rust
/// Python enum for ToMatch — how a filter matches the `to` field.
///
/// Variants:
///   All — receive all messages regardless of `to`
///   BroadcastOnly — only messages with empty `to`
///   DirectedToMe — only messages directed specifically to this node
///   BroadcastAndDirectedToMe — both broadcast and directed (default)
#[pyclass(name = "ToMatch")]
#[derive(Clone)]
struct PyToMatch {
    inner: ToMatch,
}

#[pymethods]
impl PyToMatch {
    /// Receive all messages regardless of `to`.
    #[classattr]
    fn All() -> Self {
        Self {
            inner: ToMatch::All,
        }
    }

    /// Only receive messages with empty `to` (broadcast).
    #[classattr]
    fn BroadcastOnly() -> Self {
        Self {
            inner: ToMatch::BroadcastOnly,
        }
    }

    /// Only receive messages directed specifically to this node.
    #[classattr]
    fn DirectedToMe() -> Self {
        Self {
            inner: ToMatch::DirectedToMe,
        }
    }

    /// Receive both broadcast messages and messages directed to this node.
    #[classattr]
    fn BroadcastAndDirectedToMe() -> Self {
        Self {
            inner: ToMatch::BroadcastAndDirectedToMe,
        }
    }

    fn __eq__(&self, other: &PyToMatch) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("ToMatch.{:?}", self.inner)
    }
}
```

**逐行解释：**

- `#[classattr]` — PyO3 将方法转为类属性（而非实例方法），Python 端 `ToMatch.All` 直接访问（类似 `IntEnum`）
- 每个变体返回一个 `PyToMatch` 实例，`inner` 持有对应的 Rust `ToMatch` 变体
- `#[derive(Clone)]` — 允许 Python 侧复制 ToMatch 值
- Python 使用示例：`MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)`

### 3.7 PyMessageFilter

```rust
/// Python wrapper for MessageFilter — controls which messages a node receives.
///
/// Python constructor:
///   MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
#[pyclass(name = "MessageFilter")]
#[derive(Clone)]
struct PyMessageFilter {
    inner: MessageFilter,
}

#[pymethods]
impl PyMessageFilter {
    #[new]
    #[pyo3(signature = (types=None, to_match=None))]
    fn new(types: Option<Vec<String>>, to_match: Option<PyToMatch>) -> Self {
        Self {
            inner: MessageFilter {
                types,
                to_match: to_match.map(|t| t.inner).unwrap_or(ToMatch::BroadcastAndDirectedToMe),
            },
        }
    }

    #[getter]
    fn types(&self) -> Option<Vec<String>> {
        self.inner.types.clone()
    }

    #[getter]
    fn to_match(&self) -> PyToMatch {
        PyToMatch {
            inner: self.inner.to_match.clone(),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "MessageFilter(types={:?}, to_match={:?})",
            self.inner.types, self.inner.to_match
        )
    }
}
```

**逐行解释：**

- `types=None` — `Option<Vec<String>>`，`None` 表示不过滤类型（Trace 节点行为）
- `to_match=None` — `Option<PyToMatch>`，`None` 时默认 `BroadcastAndDirectedToMe`，与 Rust 侧约定一致（Engine 节点行为）
- Python 端构造示例：
  - 全收：`MessageFilter()` 或 `MessageFilter(types=None, to_match=ToMatch.All)`
  - 静默：`MessageFilter(types=[], to_match=ToMatch.BroadcastOnly)`
  - 心跳监听：`MessageFilter(types=["heartbeat_request"], to_match=ToMatch.BroadcastAndDirectedToMe)`

### 3.8 PySendReceipt + PyBusGraph

```rust
/// Python wrapper for SendReceipt — delivery confirmation.
///
/// Returned by handle.send() and bus.send().
#[pyclass(name = "SendReceipt")]
struct PySendReceipt {
    inner: SendReceipt,
}

#[pymethods]
impl PySendReceipt {
    #[getter]
    fn message_id(&self) -> String {
        self.inner.message_id.to_string()
    }

    #[getter]
    fn online_nodes(&self) -> usize {
        self.inner.online_nodes
    }

    #[getter]
    fn matching_nodes(&self) -> usize {
        self.inner.matching_nodes
    }

    fn __repr__(&self) -> String {
        format!(
            "SendReceipt(online={}, matching={})",
            self.inner.online_nodes, self.inner.matching_nodes
        )
    }
}

/// Python wrapper for BusGraph — snapshot of bus health.
///
/// Returned by bus.graph().
#[pyclass(name = "BusGraph")]
struct PyBusGraph {
    inner: BusGraph,
}

#[pymethods]
impl PyBusGraph {
    #[getter]
    fn nodes(&self) -> Vec<PyNodeInfo> {
        self.inner
            .nodes
            .iter()
            .map(|info| PyNodeInfo {
                inner: info.clone(),
            })
            .collect()
    }

    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count
    }

    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms
    }

    fn __repr__(&self) -> String {
        format!(
            "BusGraph(nodes={}, messages={}, uptime_ms={})",
            self.inner.nodes.len(),
            self.inner.message_count,
            self.inner.uptime_ms
        )
    }
}
```

**逐行解释：**

- `PySendReceipt` — 纯数据对象，三个只读属性。Python 侧通过 `receipt.online_nodes` 访问
- `PyBusGraph` — 快照对象。`nodes` 返回 `Vec<PyNodeInfo>`，PyO3 自动转为 Python list
- `message_id` — UUID 以字符串返回，Python 侧无需依赖 uuid 库

### 3.9 Python 异常：SendError + ConnectError

```rust
/// Register Python exception classes for Bus errors.
fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Create exception hierarchy
    // BusError → Exception (base)
    // SendError(BusError) — NodeOffline / BusFull / BusClosed
    // ConnectError(BusError) — AlreadyConnected / BusClosed
    
    m.add("BusError", pyo3::types::PyException::new_type(m.py(), "BusError", Some(m.py().get_type::<pyo3::exceptions::PyException>())))?;
    m.add("SendError", pyo3::types::PyException::new_type(m.py(), "SendError", Some(m.py().get_type::<pyo3::exceptions::PyException>())))?;
    m.add("ConnectError", pyo3::types::PyException::new_type(m.py(), "ConnectError", Some(m.py().get_type::<pyo3::exceptions::PyException>())))?;
    Ok(())
}

/// Convert Rust ConnectError to Python ConnectError exception.
fn connect_error_to_py(err: ConnectError) -> PyErr {
    match err {
        ConnectError::AlreadyConnected(id) => {
            PyErr::new::<pyo3::exceptions::PyException, _>(format!(
                "node already connected: {}",
                id.as_str()
            ))
        }
        ConnectError::BusClosed => {
            PyErr::new::<pyo3::exceptions::PyException, _>("bus closed")
        }
    }
}

/// Convert Rust SendError to Python SendError exception.
fn send_error_to_py(err: SendError) -> PyErr {
    let msg = err.to_string();
    PyErr::new::<pyo3::exceptions::PyException, _>(msg)
}
```

**逐行解释：**

- `register_exceptions` — 在 `#[pymodule]` 中调用，创建 Python 异常类挂载到 `arf._arf` 模块
- `connect_error_to_py` — 每个 async 方法内部 map_err 用此函数将 Rust error 转为 Python exception
- 异常继承 `PyException`（Python `Exception`），用户可 `except Exception as e:` 捕获
- `send_error_to_py` 复用 Rust 的 `Display` trait 输出作为异常消息

### 3.10 PyBus — 核心类

```rust
/// Python wrapper for Bus — J-RPC broadcast message bus.
///
/// Python constructor:
///   Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)
#[pyclass(name = "Bus")]
struct PyBus {
    inner: Arc<Bus>,
}
```

**逐行解释：**

- `Arc<Bus>` — 多所有权包装。`PyNodeHandle` 持有 `NodeHandle`（内部克隆了 `cmd_tx`），不影响 `PyBus` 生命周期
- Python 端 `Bus` 对象可被 drop/GC，但 `Arc<Bus>` 确保底层 channel 在所有 `NodeHandle` 释放后才析构

#### 构造器

```rust
#[pymethods]
impl PyBus {
    /// Create a new Bus.
    ///
    /// Args:
    ///   heartbeat_interval_ms: interval between heartbeat requests (default 1000)
    ///   heartbeat_timeout_ms: how long to wait for heartbeat ack (default 3000)
    ///   channel_capacity: size of the broadcast ring buffer (default 16)
    #[new]
    #[pyo3(signature = (heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16))]
    fn new(heartbeat_interval_ms: u64, heartbeat_timeout_ms: u64, channel_capacity: usize) -> Self {
        let bus = Bus::new(
            std::time::Duration::from_millis(heartbeat_interval_ms),
            std::time::Duration::from_millis(heartbeat_timeout_ms),
            channel_capacity,
        );
        Self {
            inner: Arc::new(bus),
        }
    }
```

**逐行解释：**

- 所有参数提供默认值，Python 端 `Bus()` 即可创建默认配置的总线
- `Duration::from_millis` — Rust 内部使用 `Duration`，Python 端传毫秒整数更直观

#### 属性：message_count / uptime_ms / graph（同步）

```rust
    /// Total messages broadcast since start.
    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count()
    }

    /// Milliseconds since the bus was created.
    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms()
    }

    /// Snapshot of bus health — nodes, message count, uptime.
    fn graph(&self) -> PyBusGraph {
        PyBusGraph {
            inner: self.inner.graph(),
        }
    }
```

**逐行解释：**

- 均同步方法，直接委托 `Arc<Bus>` 调用，无 async 开销
- `graph()` 返回快照——对 `nodes` RwLock 的读锁在方法返回后释放

#### subscribe — 返回 BroadcastReceiver（同步）

`subscribe()` 返回 `broadcast::Receiver<Message>`，但 Python 侧通过 `NodeHandle.recv()` 接收消息，不需要裸 receiver。**暂不暴露。**

#### async 方法：connect / send / shutdown

```rust
    /// Connect a node to the bus. Returns a NodeHandle.
    ///
    /// Args:
    ///   info: NodeInfo describing the node
    ///   filter: MessageFilter controlling which messages this node receives
    ///
    /// Returns:
    ///   NodeHandle for sending/receiving messages
    ///
    /// Raises:
    ///   ConnectError: if node_id is already connected or bus is closed
    fn connect<'py>(
        &self,
        py: Python<'py>,
        info: PyNodeInfo,
        filter: PyMessageFilter,
    ) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.connect(info.inner, filter.inner)
                .await
                .map(|handle| PyNodeHandle { inner: Some(handle) })
                .map_err(connect_error_to_py)
        })
    }

    /// Send a message directly through the bus (without NodeHandle).
    ///
    /// Prefer NodeHandle.send() — it auto-fills the `from` field.
    fn send<'py>(
        &self,
        py: Python<'py>,
        msg: PyMessage,
    ) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.send(msg.inner)
                .await
                .map(|receipt| PySendReceipt { inner: receipt })
                .map_err(send_error_to_py)
        })
    }

    /// Shut down the bus.
    ///
    /// Sends a shutdown signal to the message loop. After shutdown,
    /// all subsequent sends fail with BusClosed, and all receivers
    /// get Closed.
    fn shutdown<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.signal_shutdown();
            Ok(())
        })
    }
}
```

**逐行解释：**

- `future_into_py(py, async move { ... })` — pyo3-asyncio 核心模式：将 Rust future 包装为 Python coroutine
- `async move { ... }` — move 语义将 `bus: Arc<Bus>` 所有权移入 future；future 被 Python event loop 驱动执行
- `bus.connect(...)` 返回 `Result<NodeHandle, ConnectError>`，`.map()` 成功分支构建 `PyNodeHandle`，`.map_err()` 转为 Python 异常
- `shutdown` — 通过 `signal_shutdown()` 发送关闭信号（fire-and-forget），消息循环收到后退出并关闭 broadcast channel
- 所有 async 方法签名一致：`fn xxx<'py>(&self, py: Python<'py>, ...) -> PyResult<Bound<'py, PyAny>>`，返回 Python awaitable

### 3.11 PyNodeHandle

```rust
/// Python wrapper for NodeHandle — a connected node's handle to the Bus.
///
/// Created by bus.connect(), consumed by disconnect().
/// Used to send messages, receive messages, and query node info.
#[pyclass(name = "NodeHandle")]
struct PyNodeHandle {
    /// Option so we can consume self on disconnect.
    inner: Option<NodeHandle>,
}
```

**逐行解释：**

- `Option<NodeHandle>` — `disconnect()` 消费 `NodeHandle`（Rust 侧 `fn disconnect(self)`）。Python 侧通过 `self.inner.take()` 取出所有权，后续调用报错

```rust
#[pymethods]
impl PyNodeHandle {
    /// Send a message from this node.
    ///
    /// The `from` field is auto-filled from this node's NodeInfo.
    ///
    /// Args:
    ///   msg_type: message type string, e.g. "action", "model_call"
    ///   to: list of target NodeIds (empty = broadcast)
    ///   payload: arbitrary JSON-serializable Python object
    ///
    /// Returns:
    ///   SendReceipt with online_nodes and matching_nodes counts
    ///
    /// Raises:
    ///   SendError: if all targets are offline or bus is closed
    fn send<'py>(
        &self,
        py: Python<'py>,
        msg_type: String,
        to: Vec<PyNodeId>,
        payload: PyObject,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.check_open()?; // borrow check
        let to_ids: Vec<NodeId> = to.into_iter().map(|id| id.inner).collect();
        let json_payload = Python::with_gil(|py| py_object_to_json(&payload, py))?;

        // Clone cmd_tx to send from the handle
        let cmd_tx = handle.cmd_tx.clone();
        let node_id = handle.info.node_id.clone();
        let msg = CoreMessage::new(msg_type, node_id, to_ids, json_payload);

        future_into_py(py, async move {
            let (tx, rx) = tokio::sync::oneshot::channel();
            cmd_tx
                .send(arf_bus::BusCommand::Send {
                    msg,
                    respond_to: tx,
                })
                .await
                .map_err(|_| SendError::BusClosed)?;
            rx.await
                .map_err(|_| SendError::BusClosed)?
                .map(|receipt| PySendReceipt { inner: receipt })
                .map_err(send_error_to_py)
        })
    }
```

等等！`BusCommand` 是 `pub(crate)` 的，`cmd_tx` 也是 `pub(crate)` 的。在 py-arf crate 中无法访问。

这个问题必须解决。有两个方案：

**方案 A**：在 `arf-bus` 中添加一个公开的 `send_raw` 方法或公开 `BusCommand`

**方案 B**：`NodeHandle` 已有 `send()` 方法，但 `NodeHandle` 需要 `&self` 且返回 future。我们可以直接调用 `handle.send()`。

但方案 B 的问题是 `NodeHandle` 被 borrow，我们不能同时 borrow 它和调用 async 方法...

实际上可以！`NodeHandle::send(&self, ...)` 接受 `&self`。我们只需要确保 lifetime 正确。

让我重新设计 PyNodeHandle 的 send 方法：

```rust
fn send<'py>(
    &self,
    py: Python<'py>,
    msg_type: String,
    to: Vec<PyNodeId>,
    payload: PyObject,
) -> PyResult<Bound<'py, PyAny>> {
    // We need to get the inner handle, but we can't hold a ref across await.
    // Solution: clone what we need before the async block.
    let handle = self.check_open()?; // returns &NodeHandle
    let to_ids: Vec<NodeId> = to.into_iter().map(|id| id.inner).collect();
    let json_payload = Python::with_gil(|py| py_object_to_json(&payload, py))?;
    
    // NodeHandle::send is async and takes &self. But we can't hold &NodeHandle
    // across the async boundary because PyNodeHandle might be mutated.
    // 
    // Actually, NodeHandle::send just needs cmd_tx and info.node_id.
    // But we can't access those (pub(crate)).
    //
    // Simplest fix: wrap NodeHandle in Arc<Mutex<NodeHandle>>
    // No wait, that changes the Rust API.
    //
    // OK, best approach: add a public method or make NodeHandle clone-able enough
    // for our needs.
```

Hmm, this is getting complicated. Let me think about the cleanest approach...

The core problem: `NodeHandle::send()` takes `&self` and is async. In PyO3, we can't hold a Rust reference across an await point because Python might drop/mutate the object.

**Solution**: Wrap NodeHandle in `Arc<tokio::sync::Mutex<NodeHandle>>`. Then we can lock it, get access to the inner handle, and call send.

Wait, but `NodeHandle::send()` needs `&self`:
```rust
pub async fn send(&self, msg_type: &str, to: Vec<NodeId>, payload: serde_json::Value) -> Result<SendReceipt, SendError>
```

If we have `Arc<Mutex<NodeHandle>>`, we can:
```rust
let handle_arc = self.inner.clone();
future_into_py(py, async move {
    let handle = handle_arc.lock().await;
    handle.send(&msg_type, to_ids, json_payload).await
})
```

But then `recv()` takes `&mut self`:
```rust
pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError>
```

This works with `Mutex` — we lock, get `&mut NodeHandle`, call `recv().await`. But holding a `MutexGuard` across `.await` is not Send-safe with `std::sync::Mutex`. We MUST use `tokio::sync::Mutex`.

So: `Arc<tokio::sync::Mutex<NodeHandle>>` for PyNodeHandle.

This also solves `disconnect()` consuming `self` — with `Arc<Mutex<NodeHandle>>`, we can lock, take the inner value out (or just drop it), and release.

Wait, `disconnect()` in Rust consumes `self` (takes ownership). With `Mutex<NodeHandle>`, we can:
```rust
fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    let handle_arc = self.inner.clone();
    future_into_py(py, async move {
        // Lock, take the handle out of the Mutex, call disconnect
        let mut guard = handle_arc.lock().await;
        // Hmm, we need to take ownership... 
        // We could use Option<NodeHandle> inside the Mutex
    })
}
```

OK let me change the design:

```rust
struct PyNodeHandle {
    inner: Arc<tokio::sync::Mutex<Option<NodeHandle>>>,
}
```

- `send()`: lock → `as_ref().unwrap().send(...).await`
- `recv()`: lock → `as_mut().unwrap().recv().await`
- `try_recv()`: lock → `as_mut().unwrap().try_recv()`
- `disconnect()`: lock → `take().unwrap().disconnect().await`
- `node_info()`: lock → `as_ref().unwrap().node_info().clone()`
- `filter_config()`: lock → `as_ref().unwrap().filter_config().clone()`

After disconnect, the Option is None, and all subsequent calls get a "node already disconnected" error.

This is clean and correct. But it changes the doc... let me update.

Wait, actually there's an even simpler approach. Instead of wrapping in Mutex, I can just clone out the `cmd_tx` and `info.node_id` before the async block for `send()`. And for `recv()` which needs `&mut self`, I can use `Mutex`.

Actually, let me just go with `Arc<Mutex<Option<NodeHandle>>>` for consistency. Every method follows the same pattern: lock, check Some, call inner method. It's clean.

But wait — `tokio::sync::Mutex` or `std::sync::Mutex`? Since we're holding the lock across `.await` points (for `recv()` and `send()`), we MUST use `tokio::sync::Mutex`. `std::sync::Mutex` is not `Send` when holding across await.

Actually, `NodeHandle::send()` takes `&self`, so we only need a read lock. And `NodeHandle::recv()` takes `&mut self`, so we need a write lock. With `tokio::sync::Mutex`, both work, and it's Send-safe.

BUT — for `try_recv()` which is synchronous, we need to call it without async. With `tokio::sync::Mutex`, we can use `try_lock()` for non-blocking case, or just `block_in_place` if needed. Actually, `try_recv()` is a synchronous method that returns immediately, so we can use `std::sync::Mutex` style locking... but we're using `tokio::sync::Mutex`.

Let me just use `std::sync::Mutex` for `try_recv()` and for `send()` we can release the lock before await. Actually for `send()`:

```rust
fn send<'py>(&self, py: Python<'py>, ...) -> PyResult<Bound<'py, PyAny>> {
    // Take what we need from the handle (synchronously)
    let (cmd_tx_clone, node_id_clone) = {
        let guard = self.inner.lock().unwrap();
        let handle = guard.as_ref().ok_or_else(|| ...)?;
        (handle.cmd_tx.clone(), handle.info.node_id.clone())
    }; // lock released here
    
    let msg = CoreMessage::new(msg_type, node_id_clone, to_ids, json_payload);
    
    future_into_py(py, async move {
        // Now we own cmd_tx_clone and msg, no borrows active
        let (tx, rx) = oneshot::channel();
        cmd_tx_clone.send(BusCommand::Send { msg, respond_to: tx }).await...;
        ...
    })
}
```

But `cmd_tx` is `pub(crate)` and `BusCommand` is `pub(crate)` — I CANNOT access them from py-arf!

OK, so the ONLY clean way to implement PyNodeHandle.send() is to actually call `NodeHandle::send()`. And since that takes `&self` and is async, I need to hold the reference across await. 

Two options:
1. Use `Arc<tokio::sync::Mutex<Option<NodeHandle>>>` — hold the lock across await
2. Add a public async method to `NodeHandle` that takes `self` by value or doesn't need a long-lived borrow

Actually wait, option 1 works fine with `tokio::sync::Mutex`:

```rust
fn send<'py>(&self, py: Python<'py>, msg_type: String, to: Vec<PyNodeId>, payload: PyObject) -> PyResult<Bound<'py, PyAny>> {
    let handle_arc = self.inner.clone();
    let to_ids: Vec<NodeId> = to.into_iter().map(|id| id.inner).collect();
    let json_payload = Python::with_gil(|py| py_object_to_json(&payload, py))?;
    
    future_into_py(py, async move {
        let guard = handle_arc.lock().await;
        let handle = guard.as_ref().ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node disconnected"))?;
        // Now we hold the lock across send().await — OK with tokio::sync::Mutex
        handle.send(&msg_type, to_ids, json_payload).await
            .map(|r| PySendReceipt { inner: r })
            .map_err(send_error_to_py)
    })
}
```

This works because `tokio::sync::Mutex::lock()` returns a `MutexGuard` that implements `Send`. The guard is held across the `handle.send().await`, but that's fine — other concurrent calls to the same PyNodeHandle will queue up on the lock.

For `recv()`:
```rust
fn recv<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    let handle_arc = self.inner.clone();
    future_into_py(py, async move {
        let mut guard = handle_arc.lock().await;
        let handle = guard.as_mut().ok_or_else(|| ...)?;
        handle.recv().await
            .map(|msg| PyMessage { inner: msg })
            .map_err(|e| ...)
    })
}
```

For `try_recv()`:
```rust
fn try_recv(&self) -> PyResult<Option<PyMessage>> {
    let mut guard = self.inner.try_lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrent access"))?;
    let handle = guard.as_mut().ok_or_else(|| ...)?;
    handle.try_recv()
        .map(|opt| opt.map(|msg| PyMessage { inner: msg }))
        .map_err(|e| ...)
}
```

Hmm, `try_lock()` might fail if another async operation is in progress. That's actually correct behavior — you shouldn't call `try_recv()` while `recv()` is pending.

Wait, but `try_recv` is a synchronous method in PyO3 — it doesn't take `py: Python<'_>`. And `try_lock()` on `tokio::sync::Mutex` requires `&self` (not `&mut self`). Let me check... `tokio::sync::Mutex::try_lock(&self) -> Result<MutexGuard<'_, T>, TryLockError>`. Yes, it takes `&self`.

But wait, for sync methods in PyO3, we'd be calling `try_lock()` from a non-async context. That should be fine — `try_lock()` is synchronous.

For `disconnect()`:
```rust
fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    let handle_arc = self.inner.clone();
    future_into_py(py, async move {
        let mut guard = handle_arc.lock().await;
        let handle = guard.take().ok_or_else(|| ...)?;
        handle.disconnect().await;
        Ok(())
    })
}
```

And for `node_info()` and `filter_config()`:
```rust
fn node_info(&self) -> PyResult<PyNodeInfo> {
    let guard = self.inner.try_lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrent access"))?;
    let handle = guard.as_ref().ok_or_else(|| ...)?;
    Ok(PyNodeInfo { inner: handle.node_info().clone() })
}
```

This is a good design. Let me now write the complete document with all this code.

But wait, there's another issue. For `send()` in NodeHandle, I can't use `handle.send(&msg_type, to_ids, json_payload)` because the `msg_type` parameter is `&str` and I'm holding a `MutexGuard` that borrows from `handle_arc`. The `msg_type` is moved into the async block, so it's fine. The issue is:

```rust
let guard = handle_arc.lock().await;
let handle = guard.as_ref().unwrap();
handle.send(&msg_type, to_ids, json_payload).await
// guard is dropped here (end of async block)
```

`msg_type: String` is owned by the async block, so `&msg_type` reference is valid within the async block. `to_ids: Vec<NodeId>` is also owned. `json_payload: serde_json::Value` is owned. And `handle` borrows from `guard`, which borrows from `handle_arc`. The guard lives until the end of the async block. This is all fine.

OK let me also think about `Bus::send()`. The Bus has a `send(&self, msg: Message)` method that's async. This is easier — I can just call it directly since we have `Arc<Bus>`:

```rust
fn send<'py>(&self, py: Python<'py>, msg: PyMessage) -> PyResult<Bound<'py, PyAny>> {
    let bus = self.inner.clone();
    future_into_py(py, async move {
        bus.send(msg.inner).await
            .map(|receipt| PySendReceipt { inner: receipt })
            .map_err(send_error_to_py)
    })
}
```

This works! `Bus::send()` takes `&self`, and the `Arc<Bus>` provides a `&Bus` reference inside the async block.

OK, now let me also think about `PyMessage`. It's created in two ways:
1. Returned from `handle.recv()` or `handle.try_recv()` — the inner Message is owned by PyMessage
2. Passed to `bus.send(msg)` — the Python user creates a Message and passes it

But wait, should Python users create Messages directly? Looking at the task description: "Bus.send()" is listed. But the main send API is `NodeHandle.send(msg_type, to, payload)` which auto-fills the `from` field. `Bus.send()` is the lower-level API that sends a pre-constructed Message.

For Python users, the primary API should be `NodeHandle.send(msg_type, to, payload)`. `Bus.send()` is available but less commonly used.

OK I think I have enough understanding now. Let me rewrite the doc with the corrected code.

This doc is getting very long. Let me write it properly now. I'll structure it clearly with sections for each type.

Actually, the user said the doc should have "完整代码+逐行解释+测试". So I need to include the complete code for all files, line-by-line explanations, AND test code.

The tests are about building the extension and doing a smoke test. Full Python tests are in task 1.11.

Let me write the complete document now.<｜end▁of▁thinking｜>Now let me write the comprehensive task doc. This is going to be substantial.

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="Write">
<｜｜DSML｜｜parameter name="content" string="true"># 任务 1.10：PyO3 绑定

> Phase 1 — Bus 消息总线第十项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.9 集成测试通过

## 设计思路

将 `Bus`、`NodeHandle`、`NodeInfo`、`MessageFilter` 从 Rust 暴露到 Python。

### 核心挑战

**1. 异步桥接。** Rust 侧 `arf-bus` 使用 tokio，Python 侧使用 asyncio。通过 `pyo3-asyncio-0_29` 的 `future_into_py()` 将 tokio future 转为 Python coroutine。

**2. &self 跨 await。** `NodeHandle` 方法接受 `&self`/`&mut self` 且为 async。Python 调用时，Rust 引用必须跨越 `.await` 点。使用 `Arc<tokio::sync::Mutex<Option<NodeHandle>>>` 包裹：
- `send()` 持有只读锁跨 await — `tokio::sync::Mutex` 的 guard 是 `Send`
- `recv()` 持有写锁跨 await — 同上
- `disconnect()` 取走 `Option` 内部值并消费

**3. `Bus::shutdown(self)` 消费 `self`。** Python 无法保证单所有权。新增 `Bus::signal_shutdown(&self)` 方法——通过 `try_send` 发送 Shutdown 命令，不等待响应。`Arc<Bus>` 可直接调用。

**4. 类型映射。** `serde_json::Value` ↔ Python 原生类型，手写递归转换函数（`json_value_to_py` / `py_object_to_json`），不依赖 `pyo3/serde` feature。

### Python API 预览

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

info = NodeInfo(
    node_id="engine/main", node_type="engine",
    capabilities={"sessions": ["sid-001"]}, online_since=0
)
flt = MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe)

handle = await bus.connect(info, flt)
receipt = await handle.send("action", [], {"cmd": "run"})
msg = await handle.recv()
print(msg.type, msg.sender, msg.payload)

graph = bus.graph()
await handle.disconnect()
await bus.shutdown()
```

---

## 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `crates/arf-bus/src/lib.rs` | 新增 `signal_shutdown()` |
| 修改 | `py-arf/Cargo.toml` | 新增 tokio、pyo3-asyncio |
| 重写 | `py-arf/src/lib.rs` | 全部 PyO3 绑定（~500 行） |
| 修改 | `py-arf/python/arf/__init__.py` | 导出所有新类型 |

---

## 1. `crates/arf-bus/src/lib.rs` — 新增 `signal_shutdown()`

### 背景

`Bus::shutdown(self)` 消费 `self`，但 Python 绑定中 `Bus` 被 `Arc` 包裹（`Arc<Bus>`），无法调用 `self` 消费型方法。需要 `&self` 版本的关闭入口。

### 插入位置

在 `impl Bus` 块中 `shutdown()` 方法之后：

```rust
/// Send shutdown signal via try_send — usable from &self.
///
/// Python bindings use this because Bus is Arc-wrapped and
/// `shutdown(self)` cannot be called on Arc<Bus>.
pub fn signal_shutdown(&self) {
    let (tx, _rx) = oneshot::channel();
    let _ = self.cmd_tx.try_send(BusCommand::Shutdown { respond_to: tx });
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `pub fn signal_shutdown(&self)` | 不可变引用——`Arc<Bus>` 直接调用，无需 `Arc::try_unwrap` |
| `let (tx, _rx) = oneshot::channel()` | 创建 oneshot 通道；`_rx` 立即丢弃——fire-and-forget 语义，不需要等待 Shutdown 确认 |
| `self.cmd_tx.try_send(BusCommand::Shutdown { ... })` | 用 `try_send` 而非 `.send().await`：此方法是同步的，必须在非 async 上下文可用。mpsc channel 容量 256，正常运行中不可能满 |
| `let _ = ...` | 忽略发送失败（channel 已关闭说明 Bus 已经在关闭） |

**消息循环收到 Shutdown 后的处理链：**

```
signal_shutdown()
  → cmd_tx.try_send(Shutdown)
    → run_message_loop 收到 Shutdown
      → respond_to.send(())  →  _rx (丢弃的) 无人接收，无影响
      → break  →  退出 loop
        → broadcast_tx 被 drop
          → 所有 broadcast::Receiver 收到 Closed
        → cmd_rx 被 drop
        → message loop task 结束
```

---

## 2. `py-arf/Cargo.toml` — 新增依赖

```toml
[package]
name = "py-arf"
version.workspace = true
edition.workspace = true
license.workspace = true
description = "ARF Python bindings via PyO3"

[lib]
name = "_arf"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.29", features = ["extension-module"] }
arf-core = { path = "../crates/arf-core" }
arf-bus = { path = "../crates/arf-bus" }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "time"] }
pyo3-asyncio-0_29 = { version = "0.29", features = ["attributes", "tokio-runtime"] }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `tokio` | 新增。`rt-multi-thread` 提供多线程运行时（Bus message loop 需要 `tokio::spawn`）；`time` 和 `macros` 与 arf-bus 的 tokio 依赖保持一致 |
| `pyo3-asyncio-0_29` | 新增。crate 名称带版本后缀是 PyO3 生态约定（`pyo3-asyncio-0_28` / `pyo3-asyncio-0_29`），避免不同 pyo3 大版本冲突。`tokio-runtime` feature 启用 tokio ↔ asyncio 桥接 |

---

## 3. `py-arf/src/lib.rs` — 完整重写

### 3.1 文件头：模块文档 + imports

```rust
//! PyO3 bindings for ARF V1.x — Bus, NodeHandle, core types.
//!
//! Async methods use pyo3-asyncio to bridge tokio → Python asyncio.

use std::sync::{Arc, OnceLock};

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};
use pyo3_asyncio_0_29::tokio::future_into_py;

use arf_core::{
    BusGraph, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt, ToMatch,
};
use arf_core::Message as CoreMessage;       // aliased — #[pyclass] name is "Message"
use arf_bus::{Bus, ConnectError, NodeHandle};
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `Arc` | `PyBus` 持有 `Arc<Bus>`，多所有权。Python GC 回收 `PyBus` 后，若仍有活跃 `NodeHandle`，底层 channel 保持存活 |
| `OnceLock` | 惰性初始化全局 tokio runtime——首次 async 调用时创建，后续复用 |
| `future_into_py` | pyo3-asyncio 核心函数：`async { ... }` → Python coroutine，由 Python event loop 驱动执行 |
| `CoreMessage` | 别名。Rust 的 `Message` 与即将定义的 `#[pyclass(name = "Message")]` 命名冲突，导入时重命名做区分 |
| `PyBool/PyDict/...` | `py_object_to_json` 递归转换所需的 Python 类型判断 |

### 3.2 全局 tokio runtime

```rust
/// Global tokio runtime, created on first use.
fn get_runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Runtime::new().expect("failed to create tokio runtime")
    })
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `static RT: OnceLock<Runtime>` | 静态变量——进程内唯一 runtime 实例。`OnceLock` 线程安全，确保多线程并发调用 `get_runtime()` 时只初始化一次 |
| `Runtime::new()` | 多线程运行时（`rt-multi-thread` feature）。与 arf-bus 的 `tokio::spawn(message_loop)` 兼容——message loop 需要多线程调度器 |
| `'static` 生命周期 | runtime 存活到进程退出。Python 模块卸载（`Py_Finalize`）时自然析构，期间所有 spawned tasks 被取消 |

### 3.3 JSON ↔ Python 转换函数

```rust
/// Convert serde_json::Value to Python object.
///
/// Recursively maps:
///   Null → None, Bool → bool, Number → int/float,
///   String → str, Array → list, Object → dict
fn json_value_to_py(value: &serde_json::Value, py: Python<'_>) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok(b.to_object(py)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.to_object(py))
            } else if let Some(f) = n.as_f64() {
                Ok(f.to_object(py))
            } else {
                Ok(py.None())      // unreachable for valid JSON
            }
        }
        serde_json::Value::String(s) => Ok(s.to_object(py)),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_value_to_py(item, py)?)?;
            }
            Ok(list.into())
        }
        serde_json::Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_value_to_py(v, py)?)?;
            }
            Ok(dict.into())
        }
    }
}

/// Convert Python object to serde_json::Value.
///
/// Supports: None, bool, int, float, str, list, dict.
/// Returns TypeError for unsupported types.
fn py_object_to_json(obj: &PyObject, py: Python<'_>) -> PyResult<serde_json::Value> {
    let bound = obj.bind(py);
    if bound.is_none() {
        return Ok(serde_json::Value::Null);
    }
    if let Ok(b) = bound.downcast::<PyBool>() {
        return Ok(serde_json::Value::Bool(b.is_true()));
    }
    if let Ok(i) = bound.downcast::<PyInt>() {
        let val: i64 = i.extract()?;
        return Ok(serde_json::Value::Number(val.into()));
    }
    if let Ok(f) = bound.downcast::<PyFloat>() {
        let val: f64 = f.extract()?;
        return serde_json::Number::from_f64(val)
            .map(serde_json::Value::Number)
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(
                    "NaN and Inf are not valid JSON numbers",
                )
            });
    }
    if let Ok(s) = bound.downcast::<PyString>() {
        return Ok(serde_json::Value::String(s.to_string()));
    }
    if let Ok(list) = bound.downcast::<PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(py_object_to_json(&item.into(), py)?);
        }
        return Ok(serde_json::Value::Array(arr));
    }
    if let Ok(dict) = bound.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, v) in dict.iter() {
            let key: String = k.extract()?;
            map.insert(key, py_object_to_json(&v.into(), py)?);
        }
        return Ok(serde_json::Value::Object(map));
    }
    Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
        "cannot convert to JSON: unsupported type",
    ))
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `json_value_to_py` | 递归深度优先。`Number` 分支优先尝试 `i64`（Python int），否则 `f64`（Python float）——因为 serde_json 对整数用 `Number` 而非区分整数/浮点 |
| `py_object_to_json` | `downcast` 链式类型判断。顺序：None → bool → int → float → str → list → dict。bool 必须在 int 之前判断（Python bool 是 int 子类） |
| `Number::from_f64` | 返回 `Option<Number>`——NaN 和 Inf 不能表示为 JSON number，显式报 `ValueError` |
| `list.iter()` / `dict.iter()` | 递归转换元素。Python 循环引用不处理（调用方保证 payload 无循环引用） |

**为什么不用 `pyo3/serde` feature？**

`pyo3` 的 `serde` feature 也能自动转换，但：
- 需要额外 feature flag，增加编译复杂度
- 错误信息是泛型的（"deserialization error"），不如手写转换可以给出精确的 `TypeError` 消息

手写转换约 60 行代码，完全受控。

### 3.4 PyNodeId

```rust
/// Python NodeId — unique identifier for a bus node.
///
/// Python: NodeId("engine/main")
#[pyclass(name = "NodeId")]
#[derive(Clone)]
struct PyNodeId {
    inner: NodeId,
}

#[pymethods]
impl PyNodeId {
    /// Create a NodeId from a string.
    #[new]
    fn new(id: &str) -> Self {
        Self {
            inner: NodeId::new(id),
        }
    }

    /// Return the string representation (same as str()).
    fn __str__(&self) -> &str {
        self.inner.as_str()
    }

    fn __repr__(&self) -> String {
        format!("NodeId('{}')", self.inner.as_str())
    }

    /// Equality — two NodeIds with the same string are equal.
    fn __eq__(&self, other: &PyNodeId) -> bool {
        self.inner == other.inner
    }

    /// Hash — NodeIds can be dict keys and set members.
    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.inner.hash(&mut h);
        h.finish()
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `#[derive(Clone)]` | 允许 PyO3 在 getter 中克隆返回——如 `msg.sender` 每次访问返回新的 PyNodeId |
| `__hash__` | PyO3 要求返回 `u64`。`NodeId` derive `Hash`，但 PyO3 不会自动生成 `__hash__`（因为不是所有类型都 hashable）。手动调用 `DefaultHasher` 计算哈希值 |
| `__eq__` | 同时自动获得 `__ne__`（PyO3 默认为 `not __eq__`） |

**Python 端行为：**

```python
a = NodeId("engine/main")
b = NodeId("engine/main")
assert a == b
assert hash(a) == hash(b)
d = {a: "value"}   # NodeId 可作为 dict key
```

### 3.5 PyMessage

```rust
/// Python Message — read-only view of a bus message.
///
/// Note: `from` field is exposed as `sender` (from is a Python keyword).
#[pyclass(name = "Message")]
struct PyMessage {
    inner: CoreMessage,
}

#[pymethods]
impl PyMessage {
    /// UUID v4 message ID as string.
    #[getter]
    fn id(&self) -> String {
        self.inner.id.to_string()
    }

    /// Message type: "node_online", "action", "model_call", etc.
    #[getter]
    fn msg_type(&self) -> &str {
        &self.inner.msg_type
    }

    /// Sender NodeId (renamed from `from` — Python keyword).
    #[getter]
    fn sender(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.from.clone(),
        }
    }

    /// Target NodeIds. Empty = broadcast to all.
    #[getter]
    fn to(&self) -> Vec<PyNodeId> {
        self.inner
            .to
            .iter()
            .map(|id| PyNodeId { inner: id.clone() })
            .collect()
    }

    /// JSON payload as Python object (dict, list, str, int, float, bool, None).
    #[getter]
    fn payload(&self, py: Python<'_>) -> PyResult<PyObject> {
        json_value_to_py(&self.inner.payload, py)
    }

    /// Unix timestamp in milliseconds.
    #[getter]
    fn timestamp(&self) -> u64 {
        self.inner.timestamp
    }

    /// True if this is a broadcast message (to is empty).
    fn is_broadcast(&self) -> bool {
        self.inner.is_broadcast()
    }

    /// True if this message targets the given NodeId.
    fn is_for(&self, node_id: &PyNodeId) -> bool {
        self.inner.is_for(&node_id.inner)
    }

    fn __repr__(&self) -> String {
        format!(
            "Message(id={}, type='{}', sender='{}')",
            self.inner.id, self.inner.msg_type, self.inner.from
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `sender` (非 `from`) | Python 的 `from` 是保留关键字（`from x import y`），不能作为属性名。PyO3 不支持 `#[pyo3(name = "from")]` 与 Python 关键字冲突。改为 `sender` |
| `#[getter] fn to` | 返回 `Vec<PyNodeId>`——PyO3 自动将 `Vec<T>` 转为 Python `list` |
| `payload` getter | `py: Python<'_>` token 传入以便在 Python heap 创建对象 |
| `is_for` | `&PyNodeId` 引用参数——Python 端传 NodeId 实例即可 |

### 3.6 PyNodeInfo

```rust
/// Python NodeInfo — identity and capabilities of a bus node.
///
/// Python:
///   NodeInfo(node_id="engine/main", node_type="engine",
///            capabilities={"sessions": ["sid-001"]})
#[pyclass(name = "NodeInfo")]
#[derive(Clone)]
struct PyNodeInfo {
    inner: NodeInfo,
}

#[pymethods]
impl PyNodeInfo {
    #[new]
    #[pyo3(signature = (node_id, node_type, capabilities, online_since=0))]
    fn new(
        node_id: PyNodeId,
        node_type: String,
        capabilities: PyObject,
        online_since: u64,
    ) -> PyResult<Self> {
        let caps = Python::with_gil(|py| py_object_to_json(&capabilities, py))?;
        Ok(Self {
            inner: NodeInfo {
                node_id: node_id.inner,
                node_type,
                capabilities: caps,
                online_since,
            },
        })
    }

    #[getter]
    fn node_id(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.node_id.clone(),
        }
    }

    #[getter]
    fn node_type(&self) -> String {
        self.inner.node_type.clone()
    }

    #[getter]
    fn capabilities(&self, py: Python<'_>) -> PyResult<PyObject> {
        json_value_to_py(&self.inner.capabilities, py)
    }

    #[getter]
    fn online_since(&self) -> u64 {
        self.inner.online_since
    }

    fn __repr__(&self) -> String {
        format!(
            "NodeInfo(node_id='{}', type='{}')",
            self.inner.node_id.as_str(),
            self.inner.node_type
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `capabilities: PyObject` | 接受任意 Python 对象，构造函数内部转 `serde_json::Value`。类型错误在 `py_object_to_json` 中报 `TypeError` |
| `online_since=0` | 默认值 0——Python 调用 `NodeInfo(node_id=..., node_type=..., capabilities=...)` 省略此参数时自动填 0 |
| `Python::with_gil` | `py_object_to_json` 需要 GIL。构造函数可能在非 GIL 线程调用（PyO3 的 `#[new]` 自动获取 GIL），但显式 `with_gil` 确保安全 |
| `#[derive(Clone)]` | `graph().nodes` 返回 `Vec<PyNodeInfo>`，需要 Clone |

### 3.7 PyToMatch

```rust
/// Python ToMatch — how a filter matches the `to` field.
///
/// Usage: ToMatch.All, ToMatch.BroadcastOnly,
///        ToMatch.DirectedToMe, ToMatch.BroadcastAndDirectedToMe
#[pyclass(name = "ToMatch")]
#[derive(Clone)]
struct PyToMatch {
    inner: ToMatch,
}

#[pymethods]
impl PyToMatch {
    /// Receive all messages regardless of `to`.
    #[classattr]
    fn All() -> Self {
        Self { inner: ToMatch::All }
    }

    /// Only receive broadcast messages (to is empty).
    #[classattr]
    fn BroadcastOnly() -> Self {
        Self { inner: ToMatch::BroadcastOnly }
    }

    /// Only receive messages directed to this node.
    #[classattr]
    fn DirectedToMe() -> Self {
        Self { inner: ToMatch::DirectedToMe }
    }

    /// Receive both broadcast and directed messages (default).
    #[classattr]
    fn BroadcastAndDirectedToMe() -> Self {
        Self { inner: ToMatch::BroadcastAndDirectedToMe }
    }

    fn __eq__(&self, other: &PyToMatch) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("ToMatch.{:?}", self.inner)
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `#[classattr]` | 类属性而非实例方法——Python 端 `ToMatch.All` 无需括号，类似 `IntEnum` 的用法 |
| `#[derive(Clone)]` | 类属性每次访问返回新实例，Clone 使得 Rust 侧 `.inner` 可被复制 |

**Python 端用法：**

```python
flt = MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe)
# 或省略 to_match，默认即为 BroadcastAndDirectedToMe
flt = MessageFilter(types=["action"])
```

### 3.8 PyMessageFilter

```rust
/// Python MessageFilter — controls which messages a node receives.
///
/// Python:
///   MessageFilter()                              # accept all
///   MessageFilter(types=["action"])              # filter by type
///   MessageFilter(types=[], to_match=ToMatch.BroadcastOnly)  # silent node
#[pyclass(name = "MessageFilter")]
#[derive(Clone)]
struct PyMessageFilter {
    inner: MessageFilter,
}

#[pymethods]
impl PyMessageFilter {
    #[new]
    #[pyo3(signature = (types=None, to_match=None))]
    fn new(types: Option<Vec<String>>, to_match: Option<PyToMatch>) -> Self {
        Self {
            inner: MessageFilter {
                types,
                to_match: to_match
                    .map(|t| t.inner)
                    .unwrap_or(ToMatch::BroadcastAndDirectedToMe),
            },
        }
    }

    #[getter]
    fn types(&self) -> Option<Vec<String>> {
        self.inner.types.clone()
    }

    #[getter]
    fn to_match(&self) -> PyToMatch {
        PyToMatch {
            inner: self.inner.to_match.clone(),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "MessageFilter(types={:?}, to_match={:?})",
            self.inner.types, self.inner.to_match
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `types=None` | `None` = 不过滤类型，接受所有消息（Trace 节点行为）。`Some([])` = 显式空列表，拒绝所有消息（静默节点） |
| `to_match=None` | `None` 时默认 `BroadcastAndDirectedToMe`——与 Rust 侧 Engine 节点约定一致。broadcast 消息 + 定向给自己的消息均接收 |
| `#[derive(Clone)]` | `handle.filter_config()` 返回克隆的 PyMessageFilter |

### 3.9 PySendReceipt + PyBusGraph

```rust
/// Python SendReceipt — delivery confirmation returned by send().
#[pyclass(name = "SendReceipt")]
struct PySendReceipt {
    inner: SendReceipt,
}

#[pymethods]
impl PySendReceipt {
    /// The ID of the message that was sent (UUID string).
    #[getter]
    fn message_id(&self) -> String {
        self.inner.message_id.to_string()
    }

    /// Number of nodes online when the message was sent.
    #[getter]
    fn online_nodes(&self) -> usize {
        self.inner.online_nodes
    }

    /// Number of online nodes whose filter matched this message.
    #[getter]
    fn matching_nodes(&self) -> usize {
        self.inner.matching_nodes
    }

    fn __repr__(&self) -> String {
        format!(
            "SendReceipt(online={}, matching={})",
            self.inner.online_nodes, self.inner.matching_nodes
        )
    }
}

/// Python BusGraph — snapshot of bus health returned by bus.graph().
#[pyclass(name = "BusGraph")]
struct PyBusGraph {
    inner: BusGraph,
}

#[pymethods]
impl PyBusGraph {
    /// List of online NodeInfo at query time (snapshot).
    #[getter]
    fn nodes(&self) -> Vec<PyNodeInfo> {
        self.inner
            .nodes
            .iter()
            .map(|info| PyNodeInfo {
                inner: info.clone(),
            })
            .collect()
    }

    /// Total messages broadcast since bus started.
    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count
    }

    /// Milliseconds since the bus was created.
    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms
    }

    fn __repr__(&self) -> String {
        format!(
            "BusGraph(nodes={}, messages={}, uptime_ms={})",
            self.inner.nodes.len(),
            self.inner.message_count,
            self.inner.uptime_ms
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `SendReceipt` | 三个只读属性，纯数据对象。`message_id` 返回 UUID 字符串，无需 Python 侧依赖 uuid 库 |
| `BusGraph::nodes` | 返回 `Vec<PyNodeInfo>`——克隆每个 `NodeInfo`。`graph()` 内部已持有 nodes RwLock 读锁并收集，此处再克隆一次（Python 侧获得独立副本） |

### 3.10 Python 异常

```rust
/// Convert Rust ConnectError to Python exception.
fn connect_error_to_py(err: ConnectError) -> PyErr {
    let msg = err.to_string();
    PyErr::new::<pyo3::exceptions::PyException, _>(msg)
}

/// Convert Rust SendError to Python exception.
fn send_error_to_py(err: SendError) -> PyErr {
    let msg = err.to_string();
    PyErr::new::<pyo3::exceptions::PyException, _>(msg)
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `err.to_string()` | 复用 Rust `Display` trait。输出如 `"target nodes offline: mcp/filesystem"` / `"bus closed"` / `"node already connected: engine/main"` |
| `PyException` | Python 标准 `Exception`。用户可 `except Exception as e: print(e)` |

**Python 端错误处理示例：**

```python
try:
    await handle.send("action", [NodeId("ghost")], {})
except Exception as e:
    print(f"Send failed: {e}")  # "target nodes offline: ghost"
```

### 3.11 PyBus

```rust
/// Python Bus — J-RPC broadcast message bus.
///
/// Python:
///   bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)
#[pyclass(name = "Bus")]
struct PyBus {
    inner: Arc<Bus>,
}
```

**为什么用 `Arc<Bus>` 而非直接持有 `Bus`？**

| 方案 | 问题 |
|------|------|
| `Bus`（owned） | `shutdown(self)` 消费 `self`，但 Python 无确定性析构。`PyBus` 被 GC 时无法调用 `shutdown` |
| `Arc<Bus>` | 多所有权。`signal_shutdown()` 可在 `&self` 上调用。`PyNodeHandle` 持有 `Arc<tokio::sync::Mutex<Option<NodeHandle>>>`（独立于 PyBus），Bus 的 channel 在所有 handle 释放后才关闭 |

#### 构造器 + 同步方法

```rust
#[pymethods]
impl PyBus {
    /// Create a new Bus.
    ///
    /// Args:
    ///   heartbeat_interval_ms: heartbeat request interval (default 1000)
    ///   heartbeat_timeout_ms: heartbeat ack timeout (default 3000)
    ///   channel_capacity: broadcast ring buffer size (default 16)
    #[new]
    #[pyo3(signature = (heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16))]
    fn new(heartbeat_interval_ms: u64, heartbeat_timeout_ms: u64, channel_capacity: usize) -> Self {
        let bus = Bus::new(
            std::time::Duration::from_millis(heartbeat_interval_ms),
            std::time::Duration::from_millis(heartbeat_timeout_ms),
            channel_capacity,
        );
        Self {
            inner: Arc::new(bus),
        }
    }

    /// Total messages broadcast since start.
    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count()
    }

    /// Milliseconds since the bus was created.
    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms()
    }

    /// Snapshot of bus health — online nodes, message count, uptime.
    fn graph(&self) -> PyBusGraph {
        PyBusGraph {
            inner: self.inner.graph(),
        }
    }

    /// Subscribe to all broadcast messages (raw broadcast::Receiver).
    ///
    /// Prefer bus.connect() + handle.recv() — it provides filtering
    /// and automatic heartbeat ack. Use subscribe() only for special
    /// cases like trace nodes that need every raw message.
    fn subscribe(&self) -> PyBroadcastReceiver {
        PyBroadcastReceiver {
            inner: self.inner.subscribe(),
        }
    }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `heartbeat_interval_ms=1000` | 三个参数均有默认值——`Bus()` 即创建合理默认配置的总线 |
| `graph()` | 同步方法，委托 `Bus::graph()`。Python 端 `bus.graph()` 立即返回快照 |
| `subscribe()` | 新增 `PyBroadcastReceiver` 包装（见 3.12）。用于 trace 节点等需要全量消费的场景 |

#### async 方法：connect / send / shutdown

```rust
    /// Connect a node to the bus.
    ///
    /// Returns NodeHandle for sending/receiving messages.
    /// Raises ConnectError if node_id is already connected.
    fn connect<'py>(
        &self,
        py: Python<'py>,
        info: PyNodeInfo,
        filter: PyMessageFilter,
    ) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.connect(info.inner, filter.inner)
                .await
                .map(|handle| PyNodeHandle {
                    inner: Arc::new(tokio::sync::Mutex::new(Some(handle))),
                })
                .map_err(connect_error_to_py)
        })
    }

    /// Send a raw Message directly (without NodeHandle).
    ///
    /// Prefer NodeHandle.send() — it auto-fills `from`.
    /// Use this only when you need to inject a pre-constructed Message.
    fn send_raw<'py>(
        &self,
        py: Python<'py>,
        msg: PyMessage,
    ) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.send(msg.inner)
                .await
                .map(|receipt| PySendReceipt { inner: receipt })
                .map_err(send_error_to_py)
        })
    }

    /// Shut down the bus.
    ///
    /// Sends shutdown signal to the message loop. All subsequent
    /// sends fail with BusClosed. All receivers get Closed.
    fn shutdown<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.signal_shutdown();
            Ok(())
        })
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `future_into_py(py, async move { ... })` | 核心模式。`async move` 将 `bus: Arc<Bus>` 所有权移入 future；Python event loop 负责驱动 future 到完成。Future 在 tokio runtime 上执行（pyo3-asyncio 自动调度） |
| `Arc::new(tokio::sync::Mutex::new(Some(handle)))` | `PyNodeHandle` 的内部结构——`Option` 允许 `disconnect()` 取走 handle；`Mutex` 允许 `&mut self` 方法（`recv`）跨 await 持有引用 |
| `signal_shutdown()` | fire-and-forget 语义。返回 `Ok(())` 给 Python 端意味着关闭信号已发送（不等待消息循环退出） |

### 3.12 PyBroadcastReceiver

```rust
/// Python wrapper for broadcast::Receiver<Message>.
///
/// Raw subscription — no filtering, no heartbeat ack.
/// Use bus.connect() + handle.recv() for normal use.
#[pyclass(name = "BroadcastReceiver")]
struct PyBroadcastReceiver {
    inner: tokio::sync::broadcast::Receiver<CoreMessage>,
}

#[pymethods]
impl PyBroadcastReceiver {
    /// Receive the next message (async). Blocks until a message arrives.
    ///
    /// Returns Message. Raises Exception if channel is closed.
    fn recv<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // broadcast::Receiver::recv takes &mut self, but we can't hold
        // the &mut across await in PyO3. Use a different approach:
        // pin the receiver and poll it.
        //
        // Simpler: take the receiver out, await, put it back.
        // Actually: wrap in Arc<Mutex<>> like NodeHandle.
        todo!("see design note below")
    }
}
```

**设计说明：`PyBroadcastReceiver` 暂不实现。**

`subscribe()` 已在 `PyBus` 上暴露，但 `PyBroadcastReceiver` 有以下困难：
- `broadcast::Receiver::recv(&mut self)` 需要 `&mut self`，与 `NodeHandle::recv()` 相同
- 但 `BroadcastReceiver` 不需要 filter/heartbeat/disconnect，只是一个简单的 receive loop
- 对 Python 用户来说，`bus.connect(info, filter)` + `handle.recv()` 已覆盖 95% 场景

**决定：`subscribe()` 方法保留但标记为不导出。** 等 1.11 Python 测试反馈需求后再决定是否实现 `PyBroadcastReceiver`。如果 trace 节点场景确实需要全量消费，再加。

### 3.13 PyNodeHandle

```rust
/// Python NodeHandle — a connected node's handle to the Bus.
///
/// Created by bus.connect(), consumed by disconnect().
/// Used to send, receive, query info, and disconnect.
///
/// Internal: Arc<tokio::sync::Mutex<Option<NodeHandle>>>
///   - Arc: Python may clone/reference the handle
///   - Mutex: recv(&mut self) needs exclusive access across await
///   - Option: disconnect() takes the handle out
#[pyclass(name = "NodeHandle")]
struct PyNodeHandle {
    inner: Arc<tokio::sync::Mutex<Option<NodeHandle>>>,
}
```

**逐行解释：**

| 层级 | 为什么 |
|------|--------|
| `Arc` | `PyNodeHandle` 可能在 Python 侧被多次引用（赋值给多个变量）。`Arc` 确保所有引用共享同一个底层 `NodeHandle` |
| `tokio::sync::Mutex` | `recv()` 需要 `&mut self`，且该方法为 async。`std::sync::Mutex` 的 guard 不是 `Send`，不能在 `.await` 时持有。`tokio::sync::Mutex` 的 guard 是 `Send`，可以安全跨 await |
| `Option` | `disconnect()` 消费 `NodeHandle`（Rust 侧 `fn disconnect(self)`）。Python 侧通过 `take()` 取出所有权，后续调用返回 "already disconnected" 错误 |

#### send

```rust
#[pymethods]
impl PyNodeHandle {
    /// Send a message from this node.
    ///
    /// The `from` field is auto-filled from this node's NodeInfo.
    ///
    /// Args:
    ///   msg_type: message type string ("action", "model_call", ...)
    ///   to: list of target NodeIds (empty = broadcast)
    ///   payload: JSON-serializable Python object (dict, list, str, int, ...)
    ///
    /// Returns SendReceipt with online node counts.
    /// Raises SendError if all targets are offline or bus is closed.
    fn send<'py>(
        &self,
        py: Python<'py>,
        msg_type: String,
        to: Vec<PyNodeId>,
        payload: PyObject,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        let to_ids: Vec<NodeId> = to.into_iter().map(|id| id.inner).collect();
        let json_payload = Python::with_gil(|py| py_object_to_json(&payload, py))?;

        future_into_py(py, async move {
            let guard = handle_arc.lock().await;
            let handle = guard.as_ref().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "node already disconnected",
                )
            })?;

            handle.send(&msg_type, to_ids, json_payload)
                .await
                .map(|receipt| PySendReceipt { inner: receipt })
                .map_err(send_error_to_py)
        })
    }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `handle_arc.clone()` | 克隆 `Arc`（引用计数+1），移入 async block。确保 `PyNodeHandle` 被 Python GC 回收时，async block 中的操作不受影响 |
| `Python::with_gil` | 将 Python payload 转为 `serde_json::Value`——需要在 async block 之外完成，因为 `py_object_to_json` 需要 GIL，而 `future_into_py` 内部可能不在 GIL 线程 |
| `guard.as_ref()` | tokio Mutex 的读锁——`send()` 只需 `&self`，不需要 `&mut`。多线程可同时调用 `send()`（每个排队获取锁后释放） |
| `handle.send(&msg_type, ...)` | `msg_type: String` 被 move 进 async block，`&msg_type` 引用在 async block 内有效 |
| `ok_or_else(|| PyErr ...)` | 若 `Option` 为 `None`（已 disconnect），返回 `RuntimeError` |

#### recv

```rust
    /// Receive the next message matching our filter.
    ///
    /// Heartbeat requests are intercepted and auto-acknowledged.
    /// Messages that don't match the filter are silently skipped.
    ///
    /// Blocks until a matching non-heartbeat message arrives.
    /// Returns Message.
    fn recv<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        future_into_py(py, async move {
            let mut guard = handle_arc.lock().await;
            let handle = guard.as_mut().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "node already disconnected",
                )
            })?;

            handle.recv()
                .await
                .map(|msg| PyMessage { inner: msg })
                .map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyException, _>(
                        format!("recv error: {e}"),
                    )
                })
        })
    }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `guard.as_mut()` | tokio Mutex 的写锁——`recv()` 需要 `&mut self`（`broadcast::Receiver::recv` 修改内部游标）。同时只能有一个 `recv()` 调用在执行 |
| `handle.recv().await` | 内部循环过滤 heartbeat + filter 不匹配的消息。锁在此处跨 await 持有——tokio Mutex 支持，其他 task 的 send/disconnect 调用将排队 |
| `RecvError` → `PyException` | `broadcast::error::RecvError` 不实现 `std::error::Error`（只有 `Debug` + `Display`），手动格式化为字符串 |

#### try_recv

```rust
    /// Try to receive a message without blocking.
    ///
    /// Returns Message if available, None if no message is ready.
    /// Heartbeat requests are intercepted and auto-acknowledged.
    /// Raises Exception if lagged or closed.
    fn try_recv(&self) -> PyResult<Option<PyMessage>> {
        let mut guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "concurrent recv in progress — try_recv not available",
            )
        })?;

        let handle = guard.as_mut().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "node already disconnected",
            )
        })?;

        match handle.try_recv() {
            Ok(Some(msg)) => Ok(Some(PyMessage { inner: msg })),
            Ok(None) => Ok(None),
            Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(
                format!("try_recv error: {e}"),
            )),
        }
    }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `try_lock()` | 非阻塞获取锁。若另一个 async `recv()` 正在执行（持有写锁），`try_lock` 会失败——此时 `try_recv` 无法安全访问 `NodeHandle`。返回 `RuntimeError` |
| `handle.try_recv()` | 同步方法，在 Python GIL 持有下直接执行。内部循环过滤 heartbeat + filter |

#### node_info / filter_config

```rust
    /// Get this node's NodeInfo (identity and capabilities).
    fn node_info(&self) -> PyResult<PyNodeInfo> {
        let guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "concurrent access in progress",
            )
        })?;
        let handle = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "node already disconnected",
            )
        })?;
        Ok(PyNodeInfo {
            inner: handle.node_info().clone(),
        })
    }

    /// Get this node's MessageFilter configuration.
    fn filter_config(&self) -> PyResult<PyMessageFilter> {
        let guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "concurrent access in progress",
            )
        })?;
        let handle = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "node already disconnected",
            )
        })?;
        Ok(PyMessageFilter {
            inner: handle.filter_config().clone(),
        })
    }
```

#### disconnect

```rust
    /// Disconnect this node from the Bus.
    ///
    /// Broadcasts node_offline via the bus. Consumes the handle —
    /// subsequent calls to send/recv/disconnect raise RuntimeError.
    fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        future_into_py(py, async move {
            let mut guard = handle_arc.lock().await;
            let handle = guard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "node already disconnected",
                )
            })?;

            handle.disconnect().await;
            Ok(())
        })
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `guard.take()` | `Option::take()` 取出 `NodeHandle` 所有权，原位留下 `None`。后续 send/recv/disconnect 均返回 "already disconnected" |
| `handle.disconnect().await` | 消费 `NodeHandle`。内部发送 Disconnect 命令到消息循环 → 从 nodes map 移除 → 广播 `node_offline` |

---

### 3.14 `#[pymodule]` — 注册所有类型

```rust
/// ARF V1.x native module (_arf).
#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Version
    m.add("__version__", "1.0.0-alpha.0")?;

    // Classes
    m.add_class::<PyNodeId>()?;
    m.add_class::<PyMessage>()?;
    m.add_class::<PyNodeInfo>()?;
    m.add_class::<PyToMatch>()?;
    m.add_class::<PyMessageFilter>()?;
    m.add_class::<PySendReceipt>()?;
    m.add_class::<PyBusGraph>()?;
    m.add_class::<PyBus>()?;
    m.add_class::<PyNodeHandle>()?;

    Ok(())
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `m.add_class::<T>()` | 将 `#[pyclass]` 标注的结构体注册到 Python 模块。调用后 `from arf._arf import NodeId` 可用 |
| 注册顺序 | 无依赖要求——Python 是动态语言，import 时按需查找。按类型从基础到复合排列仅为可读性 |

---

## 4. `py-arf/python/arf/__init__.py` — 导出

```python
"""ARF V1.x — AI Resources & Runtime Framework."""

from arf._arf import (
    __version__,
    Bus,
    BusGraph,
    ConnectError,
    Message,
    MessageFilter,
    NodeHandle,
    NodeId,
    NodeInfo,
    SendError,
    SendReceipt,
    ToMatch,
)

__all__ = [
    "__version__",
    "Bus",
    "BusGraph",
    "ConnectError",
    "Message",
    "MessageFilter",
    "NodeHandle",
    "NodeId",
    "NodeInfo",
    "SendError",
    "SendReceipt",
    "ToMatch",
]
```

---

## 5. 构建验证

```bash
# 构建
cd py-arf && ../.venv2/bin/python -m maturin develop

# 导入验证
cd py-arf && ../.venv2/bin/python -c "
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch, NodeHandle
print('All imports OK')
"

# Rust 测试不变
. $HOME/.cargo/env && cargo test -p arf-bus

# 已有 Python 测试仍通过
cd py-arf && ../.venv2/bin/python -m pytest tests/ -q
```

---

## 6. 类型与方法速查表

| Python 类 | 构造方式 | 关键方法/属性 |
|-----------|---------|-------------|
| `NodeId` | `NodeId("str")` | `str()`, `==`, `hash()`, `repr()` |
| `Message` | 由 `recv()` / `try_recv()` 返回 | `.id`, `.msg_type`, `.sender`, `.to`, `.payload`, `.timestamp`, `.is_broadcast()`, `.is_for()` |
| `NodeInfo` | `NodeInfo(node_id, node_type, capabilities, online_since=0)` | `.node_id`, `.node_type`, `.capabilities`, `.online_since` |
| `ToMatch` | `ToMatch.All` / `.BroadcastOnly` / `.DirectedToMe` / `.BroadcastAndDirectedToMe` | `==`, `repr()` |
| `MessageFilter` | `MessageFilter(types=None, to_match=None)` | `.types`, `.to_match` |
| `SendReceipt` | 由 `send()` 返回 | `.message_id`, `.online_nodes`, `.matching_nodes` |
| `BusGraph` | 由 `bus.graph()` 返回 | `.nodes`, `.message_count`, `.uptime_ms` |
| `Bus` | `Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)` | `.message_count`, `.uptime_ms`, `.graph()`, `await .connect()`, `await .send_raw()`, `await .shutdown()` |
| `NodeHandle` | 由 `await bus.connect()` 返回 | `await .send()`, `await .recv()`, `.try_recv()`, `.node_info()`, `.filter_config()`, `await .disconnect()` |

## 7. 与后续任务的关系

| 后续任务 | 依赖 1.10 提供的 |
|----------|-----------------|
| 1.11 Python 测试 | 所有 Python 类型——可直接编写 pytest 测试 |
| 1.12 文档与示例 | `Bus`/`NodeHandle`/`NodeInfo`/`MessageFilter`——`phase1_bus_hello.py` 可直接 import 使用 |

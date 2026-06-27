# 任务 1.10：PyO3 绑定

> Phase 1 — Bus 消息总线第十项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.9 集成测试通过

## 设计思路

将 `Bus`、`NodeHandle`、`NodeInfo`、`MessageFilter` 从 Rust 暴露到 Python。

### 核心挑战

**1. 异步桥接。** Rust 侧 `arf-bus` 使用 tokio，Python 侧使用 asyncio。通过 `pyo3-asyncio-0_29` 的 `future_into_py()` 将 tokio future 转为 Python coroutine。

**2. `&self` 跨 await。** `NodeHandle` 方法接受 `&self`/`&mut self` 且为 async。Python 调用时 Rust 引用必须跨越 `.await` 点。使用 `Arc<tokio::sync::Mutex<Option<NodeHandle>>>` 包裹：
- `send()` 持有只读锁跨 await — `tokio::sync::Mutex` 的 guard 是 `Send`
- `recv()` 持有写锁跨 await — 同上
- `disconnect()` 取走 `Option` 内部值并消费

**3. `Bus::shutdown(self)` 消费 `self`。** Python 无法保证单所有权。新增 `Bus::signal_shutdown(&self)` 方法——通过 `try_send` 发送 Shutdown 命令，fire-and-forget。`Arc<Bus>` 可直接调用。

**4. 类型映射。** `serde_json::Value` ↔ Python 原生类型，手写递归转换函数，不依赖 `pyo3/serde` feature。

**5. `subscribe()` / `BroadcastReceiver` 不暴露。** `subscribe()` 提供的是匿名被动监听（不注册到 nodes map），但 trace 全量消费场景用 `connect()` + `MessageFilter(types=None, to_match=ToMatch.All)` 完全等价且更干净——trace 节点本就希望被其他节点感知。技术上也避免了 `broadcast::Receiver` 跨 await 持有 `&mut self` 的重复劳动。

### Python API 预览

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

# Engine 节点
info = NodeInfo(
    node_id="engine/main", node_type="engine",
    capabilities={"sessions": ["sid-001"]}, online_since=0
)
flt = MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe)
handle = await bus.connect(info, flt)

receipt = await handle.send("action", [], {"cmd": "run"})
msg = await handle.recv()
print(msg.type, msg.sender, msg.payload)

# Trace 节点 — 全量消费
trace = await bus.connect(
    NodeInfo(node_id="trace/observer", node_type="trace", capabilities={}),
    MessageFilter(types=None, to_match=ToMatch.All)
)

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
| 重写 | `py-arf/src/lib.rs` | 全部 PyO3 绑定 |
| 修改 | `py-arf/python/arf/__init__.py` | 导出新类型 |

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
| `let (tx, _rx) = oneshot::channel()` | 创建 oneshot 通道；`_rx` 立即丢弃——fire-and-forget，不等待 Shutdown 确认 |
| `self.cmd_tx.try_send(BusCommand::Shutdown { ... })` | 用 `try_send` 而非 `.send().await`：此方法是同步的，必须在非 async 上下文可用。mpsc channel 容量 256，正常运行中不可能满 |
| `let _ = ...` | 忽略发送失败（channel 已关闭说明 Bus 已经在关闭） |

**消息循环收到 Shutdown 后的处理链：**

```
signal_shutdown()
  → cmd_tx.try_send(Shutdown)
    → run_message_loop 收到 Shutdown
      → respond_to.send(())  →  _rx (丢弃的) 无人接收，无影响
      → break  →  退出 loop
        → broadcast_tx 被 drop → 所有 broadcast::Receiver 收到 Closed
        → cmd_rx 被 drop → message loop task 结束
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
| `pyo3-asyncio-0_29` | 新增。crate 名称带版本后缀是 PyO3 生态约定，避免不同 pyo3 大版本冲突。`tokio-runtime` feature 启用 tokio ↔ asyncio 桥接 |

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
| `CoreMessage` | 别名。Rust 的 `Message` 与即将定义的 `#[pyclass(name = "Message")]` 命名冲突，导入时重命名 |
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
| `static RT: OnceLock<Runtime>` | 静态变量——进程内唯一 runtime。`OnceLock` 线程安全，多线程并发调用也只初始化一次 |
| `Runtime::new()` | 多线程运行时（`rt-multi-thread` feature）。与 arf-bus 的 `tokio::spawn(message_loop)` 兼容 |
| `'static` 生命周期 | runtime 存活到进程退出，Python 模块卸载时自然析构 |

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
| `json_value_to_py` — `Number` 分支 | 优先尝试 `i64`（Python int），否则 `f64`（Python float）——serde_json 对整数用 `Number` 不区分子类型 |
| `py_object_to_json` — `downcast` 顺序 | None → bool → int → float → str → list → dict。**bool 必须在 int 之前判断**（Python bool 是 int 子类） |
| `Number::from_f64` | 返回 `Option<Number>`——NaN 和 Inf 不能表示为 JSON number，显式报 `ValueError` |
| 无循环引用处理 | 调用方保证 payload 无循环引用（消息 payload 自然满足） |

**为什么不用 `pyo3/serde` feature？** 手写转换约 60 行，精确控制错误信息（TypeError "unsupported type" vs 泛型 "deserialization error"），且不增加编译复杂度。

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
        Self { inner: NodeId::new(id) }
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
| `#[derive(Clone)]` | 允许 getter 中克隆返回——`msg.sender` 每次访问返回新 PyNodeId |
| `__hash__` → `u64` | `NodeId` derive `Hash`，但 PyO3 不自动生成 `__hash__`（非所有类型都 hashable）。手动调用 `DefaultHasher` |
| `__eq__` | PyO3 自动生成 `__ne__` 为 `not __eq__` |

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
        PyNodeId { inner: self.inner.from.clone() }
    }

    /// Target NodeIds. Empty = broadcast to all.
    #[getter]
    fn to(&self) -> Vec<PyNodeId> {
        self.inner.to.iter()
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
| `sender` (非 `from`) | `from` 是 Python 保留关键字，不能作为属性名 |
| `#[getter] fn to` | 返回 `Vec<PyNodeId>`——PyO3 自动将 `Vec<T>` 转为 Python `list` |
| `payload` getter | `py: Python<'_>` token 传入——需要在 Python heap 创建对象 |
| `is_for` | 接受 `&PyNodeId` 引用 |

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
        PyNodeId { inner: self.inner.node_id.clone() }
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
| `online_since=0` | 默认值——Python 调用可省略此参数 |
| `Python::with_gil` | `py_object_to_json` 需要 GIL，显式获取确保安全（即使 PyO3 已自动获取） |
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
    #[classattr]
    fn All() -> Self {
        Self { inner: ToMatch::All }
    }

    #[classattr]
    fn BroadcastOnly() -> Self {
        Self { inner: ToMatch::BroadcastOnly }
    }

    #[classattr]
    fn DirectedToMe() -> Self {
        Self { inner: ToMatch::DirectedToMe }
    }

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
| `#[classattr]` | 类属性——Python 端 `ToMatch.All` 无需括号，类似 `IntEnum` |
| `#[derive(Clone)]` | 每次访问返回新实例，Clone 使得 `.inner` 可复制 |

### 3.8 PyMessageFilter

```rust
/// Python MessageFilter — controls which messages a node receives.
///
/// Python:
///   MessageFilter()                              # accept all (trace node)
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
        PyToMatch { inner: self.inner.to_match.clone() }
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
| `types=None` | `None` = 不过滤类型，所有消息都接收（Trace 节点）。`Some([])` = 显式空列表，拒绝所有消息（静默节点） |
| `to_match=None` | `None` 时默认 `BroadcastAndDirectedToMe`——Engine 节点行为：broadcast + 定向给自己的都收 |

**常用 filter 组合：**

```python
MessageFilter()                                          # Trace: 全收
MessageFilter(types=None, to_match=ToMatch.All)          # 同上，显式写法
MessageFilter(types=["action"])                          # Engine: 只收 action 类型的 broadcast+directed
MessageFilter(types=[], to_match=ToMatch.BroadcastOnly)  # Silent: 什么都不收
```

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
        self.inner.nodes.iter()
            .map(|info| PyNodeInfo { inner: info.clone() })
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
| `SendReceipt::message_id` | UUID 以字符串返回，Python 侧无需 uuid 库 |
| `BusGraph::nodes` | 返回 `Vec<PyNodeInfo>`——克隆每个。`graph()` 内部已持锁收集，此处再克隆确保 Python 侧获得独立副本 |

### 3.10 Python 异常：SendError + ConnectError

```rust
/// Convert Rust ConnectError to Python exception.
fn connect_error_to_py(err: ConnectError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}

/// Convert Rust SendError to Python exception.
fn send_error_to_py(err: SendError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `err.to_string()` | 复用 Rust `Display` trait。输出如 `"target nodes offline: mcp/filesystem"` / `"bus closed"` / `"node already connected: engine/main"` |
| `PyException` | Python `Exception`。用户可 `except Exception as e: print(e)` |

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

**为什么用 `Arc<Bus>`？**

| 方案 | 问题 |
|------|------|
| `Bus`（owned） | `shutdown(self)` 消费 `self`，Python 无确定性析构 |
| `Arc<Bus>` | `signal_shutdown()` 可在 `&self` 上调用。`PyNodeHandle` 持有独立 `Arc<Mutex<Option<NodeHandle>>>`，Bus channel 在所有 handle 释放后才关闭 |

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
        Self { inner: Arc::new(bus) }
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
        PyBusGraph { inner: self.inner.graph() }
    }
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| 三个默认参数 | `Bus()` 即创建合理默认配置 |
| `graph()` | 同步方法，立即返回快照。RwLock 读锁在方法返回后释放 |

#### async 方法：connect / shutdown

```rust
    /// Connect a node to the bus. Returns a NodeHandle.
    ///
    /// Args:
    ///   info: NodeInfo describing the node
    ///   filter: MessageFilter controlling which messages this node receives
    ///
    /// Raises ConnectError if node_id is already connected or bus is closed.
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
| `future_into_py(py, async move { ... })` | 核心模式：`async move` 将 `bus: Arc<Bus>` 所有权移入 future，Python event loop 驱动执行。pyo3-asyncio 自动在 tokio runtime 上调度 |
| `Arc::new(tokio::sync::Mutex::new(Some(handle)))` | `PyNodeHandle` 内部结构——`Option` 允许 disconnect 取走；`Mutex` 允许 `&mut self` 跨 await |
| `signal_shutdown()` | fire-and-forget——发送关闭信号后立即返回 |

### 3.12 PyNodeHandle

```rust
/// Python NodeHandle — a connected node's handle to the Bus.
///
/// Created by bus.connect(), consumed by disconnect().
///
/// Internal: Arc<tokio::sync::Mutex<Option<NodeHandle>>>
///   - Arc: Python may clone/reference the handle
///   - tokio::sync::Mutex: recv(&mut self) needs exclusive access across await
///   - Option: disconnect() takes the handle out
#[pyclass(name = "NodeHandle")]
struct PyNodeHandle {
    inner: Arc<tokio::sync::Mutex<Option<NodeHandle>>>,
}
```

**逐层解释：**

| 层级 | 为什么 |
|------|--------|
| `Arc` | `PyNodeHandle` 可能在 Python 侧被多次引用；`Arc` 确保共享同一底层 `NodeHandle` |
| `tokio::sync::Mutex` | `recv()` 需要 `&mut self` 且为 async。`std::sync::Mutex` 的 guard 不是 `Send`，不能跨 `.await`。`tokio::sync::Mutex` 的 guard 是 `Send` |
| `Option` | `disconnect()` 消费 `NodeHandle`。Python 侧通过 `take()` 取出所有权，后续调用返回 "already disconnected" |

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
    /// Returns SendReceipt.
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
| `handle_arc.clone()` | 克隆 `Arc`（引用计数+1）移入 async block。PyNodeHandle 被 GC 时 async block 内操作不受影响 |
| `Python::with_gil` | payload 转换需要在 async block **之外**完成——`future_into_py` 内部可能不在 GIL 线程 |
| `guard.as_ref()` | tokio Mutex **读锁**——`send()` 只需 `&self`。多个 send 可并发（排队获取锁后释放） |
| `handle.send(&msg_type, ...)` | `msg_type: String` 被 move 进 async block，`&msg_type` 在 block 内有效 |
| `ok_or_else` | `Option` 为 `None`（已 disconnect）时返回 `RuntimeError` |

#### recv

```rust
    /// Receive the next message matching our filter.
    ///
    /// Heartbeat requests are intercepted and auto-acknowledged.
    /// Messages that don't match the filter are silently skipped.
    ///
    /// Blocks until a matching non-heartbeat message arrives.
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
| `guard.as_mut()` | tokio Mutex **写锁**——`recv()` 需要 `&mut self`（修改 `broadcast::Receiver` 内部游标）。同时只有一个 recv 执行 |
| `handle.recv().await` | 内部循环过滤 heartbeat + filter 不匹配。锁在此处跨 await 持有——tokio Mutex 支持 |
| `RecvError` → `PyException` | `RecvError` 不实现 `std::error::Error`，手动格式化为字符串 |

#### try_recv

```rust
    /// Try to receive a message without blocking.
    ///
    /// Returns Message if available, None if no message is ready.
    /// Heartbeat requests are intercepted and auto-acknowledged.
    /// Raises RuntimeError if concurrent recv is in progress.
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
| `try_lock()` | 非阻塞。若另一个 async `recv()` 正持有写锁，`try_lock` 失败——此时无法安全访问 `NodeHandle`，报 `RuntimeError` |
| `handle.try_recv()` | 同步方法，GIL 持有下直接执行。内部循环过滤 heartbeat + filter |

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
        Ok(PyNodeInfo { inner: handle.node_info().clone() })
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
        Ok(PyMessageFilter { inner: handle.filter_config().clone() })
    }
```

#### disconnect

```rust
    /// Disconnect this node from the Bus.
    ///
    /// Broadcasts node_offline. Consumes the handle —
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
| `guard.take()` | `Option::take()` 取出 `NodeHandle` 所有权，原位留 `None`。后续调用均返回 "already disconnected" |
| `handle.disconnect().await` | 消费 `NodeHandle`。内部：发送 Disconnect → 从 nodes map 移除 → 广播 `node_offline` |

---

### 3.13 `#[pymodule]` — 注册所有类型

```rust
/// ARF V1.x native module (_arf).
#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "1.0.0-alpha.0")?;

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

---

## 4. `py-arf/python/arf/__init__.py`

```python
"""ARF V1.x — AI Resources & Runtime Framework."""

from arf._arf import (
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

__all__ = [
    "__version__",
    "Bus",
    "BusGraph",
    "Message",
    "MessageFilter",
    "NodeHandle",
    "NodeId",
    "NodeInfo",
    "SendReceipt",
    "ToMatch",
]
```

注：`SendError` 和 `ConnectError` 不作为独立类型导出——它们转为 Python `Exception` 抛出，用户 `except Exception as e:` 捕获即可。

---

## 5. 类型与方法速查

| Python 类 | 构造方式 | 关键方法/属性 |
|-----------|---------|-------------|
| `NodeId` | `NodeId("str")` | `str()`, `==`, `hash()` |
| `Message` | 由 `recv()`/`try_recv()` 返回 | `.id`, `.msg_type`, `.sender`, `.to`, `.payload`, `.timestamp`, `.is_broadcast()`, `.is_for()` |
| `NodeInfo` | `NodeInfo(node_id, node_type, capabilities, online_since=0)` | `.node_id`, `.node_type`, `.capabilities`, `.online_since` |
| `ToMatch` | `ToMatch.All` / `.BroadcastOnly` / `.DirectedToMe` / `.BroadcastAndDirectedToMe` | `==`, `repr()` |
| `MessageFilter` | `MessageFilter(types=None, to_match=None)` | `.types`, `.to_match` |
| `SendReceipt` | 由 `send()` 返回 | `.message_id`, `.online_nodes`, `.matching_nodes` |
| `BusGraph` | 由 `bus.graph()` 返回 | `.nodes`, `.message_count`, `.uptime_ms` |
| `Bus` | `Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)` | `.message_count`, `.uptime_ms`, `.graph()`, `await .connect()`, `await .shutdown()` |
| `NodeHandle` | 由 `await bus.connect()` 返回 | `await .send()`, `await .recv()`, `.try_recv()`, `.node_info()`, `.filter_config()`, `await .disconnect()` |

## 6. 构建验证

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

## 7. 与后续任务关系

| 后续 | 依赖 1.10 提供的 |
|------|-----------------|
| 1.11 Python 测试 | 所有 Python 类型——可直接编写 pytest |
| 1.12 示例 | `Bus`/`NodeHandle`/`NodeInfo`/`MessageFilter`——`phase1_bus_hello.py` 可直接 import |

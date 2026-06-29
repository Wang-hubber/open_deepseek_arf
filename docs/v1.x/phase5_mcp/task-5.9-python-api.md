# 任务 5.9：Python API（PyO3 绑定）

> Phase 5 — MCP 第九项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.5 (McpNode 统一), Task 5.8 (HttpDiscovery + HttpProxyTool)

## 设计思路

将 `McpNode`、`RemoteConfig`、`RetryConfig` 通过 PyO3 暴露给 Python，Python 开发者可用与 Rust 相同的构造模式：

```python
# 本地 MCP
node = McpNode.local("filesystem", "/path/to/tools")

# 远程 MCP
config = RemoteConfig(url="https://mcp.codetidy.dev", ...)
node = await McpNode.remote("codetidy", config)

# 连接 Bus
await node.connect(bus)
```

类型对应：

| Rust | Python |
|------|--------|
| `McpNode` (wrapped in `Arc`) | `McpNode` (holds `Arc<McpNode>`) |
| `McpNode::local(ns, root)` | `McpNode.local(ns, root)` — sync classmethod |
| `McpNode::remote(ns, config).await` | `await McpNode.remote(ns, config)` — async classmethod |
| `McpNode::connect(&bus).await` | `await node.connect(bus)` — async method |
| `RemoteConfig` | `RemoteConfig` |
| `RetryConfig` | `RetryConfig` |
| `McpError` | Python exception (subclass of `Exception`) |

**不在 5.9 范围**：`RuntimeModule` trait 的 Python 子类化（需要 PyO3 trampoline，复杂度高，留待后续 `SandboxRuntime` 需求驱动）。

| 文件 | 操作 | 内容 |
|------|------|------|
| `py-arf/Cargo.toml` | 更新 | 添加 `arf-mcp` 依赖 |
| `py-arf/src/mcp.rs` | 新建 | `PyRetryConfig` + `PyRemoteConfig` + `PyMcpNode` |
| `py-arf/src/lib.rs` | 更新 | `pub mod mcp;` + 注册 MCP 类 |
| `py-arf/python/arf/__init__.py` | 更新 | 导出 MCP 类型 |

---

## 代码实现

### `py-arf/Cargo.toml` 更新

```toml
arf-mcp = { path = "../crates/arf-mcp" }
```

### `py-arf/src/mcp.rs` — 新建

```rust
//! PyO3 bindings for arf-mcp — McpNode, RemoteConfig, RetryConfig.

use std::path::PathBuf;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use arf_mcp::config::{RemoteConfig, RetryConfig};
use arf_mcp::error::McpError;
use arf_mcp::McpNode;

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

/// Convert headers from Python dict[str, str] to HashMap<String, String>.
fn py_headers_to_hashmap(obj: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<std::collections::HashMap<String, String>> {
    let mut map = std::collections::HashMap::new();
    for pair in obj.iter()? {
        let (k, v) = pair?.extract::<(String, String)>()?;
        map.insert(k, v);
    }
    Ok(map)
}

fn mcp_error_to_py(err: McpError) -> PyErr {
    let msg = err.to_string();
    match &err {
        McpError::Discovery { .. } => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg),
        McpError::RemoteUnreachable { .. } => PyErr::new::<pyo3::exceptions::PyConnectionError, _>(msg),
        McpError::RemoteRejected { .. } => PyErr::new::<pyo3::exceptions::PyConnectionError, _>(msg),
        McpError::BusConnect { .. } => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg),
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRetryConfig
// ═══════════════════════════════════════════════════════════════════

/// Python RetryConfig — retry parameters for RemoteConfig.
#[pyclass(name = "RetryConfig", from_py_object)]
#[derive(Clone)]
pub struct PyRetryConfig {
    pub(crate) inner: RetryConfig,
}

#[pymethods]
impl PyRetryConfig {
    #[new]
    #[pyo3(signature = (max_retries=3, initial_backoff_ms=1000, max_backoff_ms=30000))]
    fn new(max_retries: u32, initial_backoff_ms: u64, max_backoff_ms: u64) -> Self {
        Self {
            inner: RetryConfig { max_retries, initial_backoff_ms, max_backoff_ms },
        }
    }

    #[getter]
    fn max_retries(&self) -> u32 { self.inner.max_retries }

    #[getter]
    fn initial_backoff_ms(&self) -> u64 { self.inner.initial_backoff_ms }

    #[getter]
    fn max_backoff_ms(&self) -> u64 { self.inner.max_backoff_ms }

    fn __repr__(&self) -> String {
        format!("RetryConfig(max_retries={}, initial_backoff_ms={}, max_backoff_ms={})",
            self.inner.max_retries, self.inner.initial_backoff_ms, self.inner.max_backoff_ms)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRemoteConfig
// ═══════════════════════════════════════════════════════════════════

/// Python RemoteConfig — configuration for a remote MCP server.
///
/// Example:
///     config = RemoteConfig(
///         url="https://mcp.codetidy.dev",
///         transport="http",
///         timeout_secs=30,
///         headers={"Authorization": "Bearer xxx"},
///         retry=RetryConfig(max_retries=3),
///     )
#[pyclass(name = "RemoteConfig", from_py_object)]
#[derive(Clone)]
pub struct PyRemoteConfig {
    pub(crate) inner: RemoteConfig,
}

#[pymethods]
impl PyRemoteConfig {
    #[new]
    #[pyo3(signature = (url, transport="http".into(), timeout_secs=None, headers=None, tls_ca_cert=None, retry=None))]
    fn new(
        url: String,
        transport: String,
        timeout_secs: Option<u64>,
        headers: Option<pyo3::Bound<'_, pyo3::PyAny>>,
        tls_ca_cert: Option<String>,
        retry: Option<PyRetryConfig>,
    ) -> PyResult<Self> {
        let hdrs = match headers {
            Some(h) => py_headers_to_hashmap(&h)?,
            None => std::collections::HashMap::new(),
        };
        Ok(Self {
            inner: RemoteConfig {
                transport,
                url,
                timeout_secs,
                headers: hdrs,
                tls_ca_cert: tls_ca_cert.map(PathBuf::from),
                retry: retry.map(|r| r.inner),
            },
        })
    }

    #[getter] fn transport(&self) -> &str { &self.inner.transport }
    #[getter] fn url(&self) -> &str { &self.inner.url }
    #[getter] fn timeout_secs(&self) -> Option<u64> { self.inner.timeout_secs }
    #[getter] fn tls_ca_cert(&self) -> Option<String> { self.inner.tls_ca_cert.as_ref().map(|p| p.display().to_string()) }
    #[getter] fn retry(&self) -> Option<PyRetryConfig> { self.inner.retry.as_ref().map(|r| PyRetryConfig { inner: r.clone() }) }

    fn __repr__(&self) -> String {
        format!("RemoteConfig(url='{}', transport='{}')", self.inner.url, self.inner.transport)
    }
}
```

逐行解释：
- `py_headers_to_hashmap()` — 将 Python `dict[str, str]` 转为 `HashMap<String, String>`。`iter()` 遍历 dict 条目，每个条目解构为 `(String, String)`
- `mcp_error_to_py()` — 按错误类型映射到合适的 Python 异常：`Discovery`/`BusConnect` → `RuntimeError`，`RemoteUnreachable`/`RemoteRejected` → `ConnectionError`
- `PyRetryConfig` — 三个数值字段，各有默认值（与 Rust 侧一致）。`from_py_object` 允许 Python 代码中从 `pyo3` 对象转换
- `PyRemoteConfig` — `url` 和 `transport` 必填，其余可选。`headers` 接受 Python dict（可选），`tls_ca_cert` 转为 `PathBuf`

### `py-arf/src/mcp.rs` — PyMcpNode（续上）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyMcpNode
// ═══════════════════════════════════════════════════════════════════

/// Python McpNode — unified MCP node (local or remote).
///
/// Created via classmethods, not direct construction:
///
///     # Local — scans filesystem synchronously
///     node = McpNode.local("my-ns", "/path/to/root")
///
///     # Remote — HTTP handshake (async)
///     node = await McpNode.remote("codetidy", config)
///
///     # Connect to Bus (async)
///     await node.connect(bus)
#[pyclass(name = "McpNode")]
pub struct PyMcpNode {
    pub(crate) inner: Arc<McpNode>,
}

#[pymethods]
impl PyMcpNode {
    /// Create a local MCP node — scans {root}/tools/ + {root}/skills/.
    ///
    /// Synchronous: filesystem scan happens immediately.
    /// Returns an error if root doesn't exist or is unreadable.
    #[classmethod]
    fn local(_cls: &pyo3::Bound<'_, PyType>, namespace: String, root: String) -> PyResult<Self> {
        McpNode::local(&namespace, PathBuf::from(&root))
            .map(|node| Self { inner: node })
            .map_err(mcp_error_to_py)
    }

    /// Create a remote MCP node — HTTP initialize + tools/list (async).
    ///
    /// Fetches tool definitions from the remote MCP server.
    /// Returns RemoteUnreachable if the server is down,
    /// RemoteRejected if the handshake is denied.
    #[classmethod]
    fn remote<'py>(
        _cls: &pyo3::Bound<'py, PyType>,
        py: pyo3::Python<'py>,
        namespace: String,
        config: PyRemoteConfig,
    ) -> PyResult<pyo3::Bound<'py, PyAny>> {
        future_into_py(py, async move {
            McpNode::remote(&namespace, config.inner)
                .await
                .map(|node| Self { inner: node })
                .map_err(mcp_error_to_py)
        })
    }

    // ── Properties ──────────────────────────────────────────────

    #[getter]
    fn namespace(&self) -> &str {
        &self.inner.namespace
    }

    #[getter]
    fn node_id(&self) -> String {
        self.inner.node_id.to_string()
    }

    // ── Lifecycle ───────────────────────────────────────────────

    /// Connect to Bus, broadcast node_online, and start the message loop.
    ///
    /// The spawned message loop holds a reference to keep the node alive.
    /// Call this once per node.
    fn connect<'py>(
        &self,
        py: pyo3::Python<'py>,
        bus: &crate::PyBus,
    ) -> PyResult<pyo3::Bound<'py, PyAny>> {
        let node = self.inner.clone();
        let bus_ref = bus.inner.clone();
        future_into_py(py, async move {
            node.connect(&bus_ref).await.map_err(mcp_error_to_py)
        })
    }

    fn __repr__(&self) -> String {
        format!("McpNode(namespace='{}', node_id='{}')",
            &self.inner.namespace, self.inner.node_id)
    }
}
```

逐行解释：
- `PyMcpNode` — 包裹 `Arc<McpNode>`。`Arc` 是必需的，因为 `connect()` 内部 spawn 的任务持有 `Arc` 引用
- `local()` — `#[classmethod]`，Python 侧 `McpNode.local(ns, root)`。同步方法：文件系统扫描立即执行，失败抛异常
- `remote()` — `#[classmethod]` + async。使用 `future_into_py` 将 Rust Future 桥接到 Python asyncio。HTTP 握手在此完成
- `connect()` — async method，将节点接入 Bus 并启动消息循环。需要 `PyBus` 的 `Arc<Bus>` 引用
- `namespace` / `node_id` — 只读属性

### `py-arf/src/lib.rs` 更新

在现有 `lib.rs` 中添加：

```rust
// 文件顶部添加
pub mod mcp;

// 在 _arf 模块注册中添加：
m.add_class::<mcp::PyRetryConfig>()?;
m.add_class::<mcp::PyRemoteConfig>()?;
m.add_class::<mcp::PyMcpNode>()?;
```

同时需要将 `PyBus` 的 `inner` 字段改为 `pub(crate)`（或添加 getter）以便 `mcp.rs` 访问。当前 `PyBus` 的 `inner` 是 `Arc<Bus>` 且为私有——需要暴露：

```rust
// PyBus struct 改为：
pub struct PyBus {
    pub(crate) inner: Arc<Bus>,
}
```

### `py-arf/python/arf/__init__.py` 更新

```python
from arf._arf import (
    # ... existing imports ...
    # Phase 5: MCP
    McpNode,
    RemoteConfig,
    RetryConfig,
)

__all__ = [
    # ... existing ...
    # Phase 5: MCP
    "McpNode",
    "RemoteConfig",
    "RetryConfig",
]
```

---

## 测试

### Rust 侧 — `py-arf/tests/test_mcp.py`

```python
"""Tests for arf MCP Python bindings."""
import asyncio
import tempfile
import os
import pytest
from arf import Bus, McpNode, RemoteConfig, RetryConfig


class TestRetryConfig:
    """[构造] RetryConfig — default values and custom values."""

    def test_default_values(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.initial_backoff_ms == 1000
        assert cfg.max_backoff_ms == 30000

    def test_custom_values(self):
        cfg = RetryConfig(max_retries=5, initial_backoff_ms=2000, max_backoff_ms=60000)
        assert cfg.max_retries == 5
        assert cfg.initial_backoff_ms == 2000
        assert cfg.max_backoff_ms == 60000

    def test_repr(self):
        cfg = RetryConfig()
        assert "RetryConfig" in repr(cfg)
        assert "max_retries=3" in repr(cfg)


class TestRemoteConfig:
    """[构造] RemoteConfig — URL + optional fields."""

    def test_minimal(self):
        cfg = RemoteConfig(url="https://example.com/mcp")
        assert cfg.url == "https://example.com/mcp"
        assert cfg.transport == "http"
        assert cfg.timeout_secs is None
        assert cfg.tls_ca_cert is None
        assert cfg.retry is None

    def test_full(self):
        retry = RetryConfig(max_retries=5)
        cfg = RemoteConfig(
            url="https://example.com/mcp",
            transport="http",
            timeout_secs=60,
            headers={"Authorization": "Bearer tok"},
            tls_ca_cert="/path/to/ca.pem",
            retry=retry,
        )
        assert cfg.url == "https://example.com/mcp"
        assert cfg.timeout_secs == 60
        assert cfg.tls_ca_cert == "/path/to/ca.pem"
        assert cfg.retry.max_retries == 5

    def test_repr(self):
        cfg = RemoteConfig(url="https://example.com/mcp")
        assert "RemoteConfig" in repr(cfg)


class TestMcpNodeLocal:
    """[构造][生命周期] McpNode.local() — filesystem scan + connect."""

    def test_local_creates_with_tools(self):
        """Local node scans filesystem and finds tools."""
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            tool_dir = os.path.join(root, "tools", "hello")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
                f.write('name = "hello"\ndescription = "Say hello"\nruntime = "bash"\nentrypoint = "main.sh"\n')
            with open(os.path.join(tool_dir, "main.sh"), "w") as f:
                f.write("#!/bin/bash\nread p\necho '{\"msg\":\"hello\"}'")

            node = McpNode.local("test", root)
            assert node.namespace == "test"

    def test_local_missing_root(self):
        """Non-existent root raises RuntimeError."""
        with pytest.raises(RuntimeError, match="discovery"):
            McpNode.local("test", "/nonexistent/path/xyz")

    def test_local_empty_root(self):
        """Root with no tools/skills — empty but valid."""
        with tempfile.TemporaryDirectory() as tmp:
            node = McpNode.local("test", tmp)
            assert node.namespace == "test"

    @pytest.mark.asyncio
    async def test_connect_to_bus(self):
        """Connect local node to Bus and verify node_online."""
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            tool_dir = os.path.join(root, "tools", "echo")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
                f.write('name = "echo"\ndescription = "Echo"\nruntime = "bash"\nentrypoint = "main.sh"\n')
            with open(os.path.join(tool_dir, "main.sh"), "w") as f:
                f.write("#!/bin/bash\nread p\necho '{\"msg\":\"ok\"}'")

            bus = Bus()
            node = McpNode.local("test", root)
            await node.connect(bus)

            graph = bus.graph()
            mcp_nodes = [n for n in graph.nodes if n.node_type == "mcp"]
            assert len(mcp_nodes) == 1
            assert mcp_nodes[0].node_id == node.node_id

            # Verify capabilities structure
            caps = mcp_nodes[0].capabilities
            assert "runtime" in caps
            assert "tools" in caps
            assert len(caps["tools"]) == 1
            assert caps["tools"][0]["name"] == "echo"

            await bus.shutdown()


class TestMcpNodeRemoteConfig:
    """[构造] RemoteConfig flows — new() doesn't connect."""

    def test_remote_config_roundtrip(self):
        """RemoteConfig is a pure data class — no network on construction."""
        retry = RetryConfig(max_retries=2, initial_backoff_ms=500)
        cfg = RemoteConfig(
            url="https://mcp.example.com",
            timeout_secs=45,
            headers={"X-API-Key": "secret"},
            retry=retry,
        )
        assert cfg.transport == "http"
        assert cfg.url == "https://mcp.example.com"
        assert cfg.timeout_secs == 45
        assert cfg.retry.max_retries == 2
        assert cfg.retry.initial_backoff_ms == 500


class TestMcpNodeRepr:
    """[类型] __repr__ coverage."""

    def test_retry_config_repr(self):
        r = repr(RetryConfig(max_retries=5))
        assert "RetryConfig" in r
        assert "5" in r

    def test_remote_config_repr(self):
        r = repr(RemoteConfig(url="https://x.com"))
        assert "RemoteConfig" in r
        assert "https://x.com" in r
```

### 集成测试 — `py-arf/tests/test_mcp_live.py`

```python
"""Live integration tests — CodeTidy MCP via Python bindings."""
import pytest
from arf import Bus, McpNode, RemoteConfig, RetryConfig


@pytest.mark.slow
@pytest.mark.asyncio
async def test_remote_codetidy_connect():
    """Connect to real CodeTidy MCP and verify tools appear on Bus."""
    config = RemoteConfig(
        url="https://mcp.codetidy.dev",
        timeout_secs=30,
    )
    node = await McpNode.remote("codetidy", config)
    assert node.namespace == "codetidy"

    bus = Bus()
    await node.connect(bus)

    graph = bus.graph()
    mcp_nodes = [n for n in graph.nodes if n.node_type == "mcp"]
    assert len(mcp_nodes) == 1

    caps = mcp_nodes[0].capabilities
    assert len(caps["tools"]) > 0
    # Verify a known tool exists
    tool_names = [t["name"] for t in caps["tools"]]
    assert "password_generate" in tool_names

    await bus.shutdown()
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `test_mcp.py::TestRetryConfig` | 3 | `[构造]` — 默认值(1)、自定义值(1)、repr(1) |
| `test_mcp.py::TestRemoteConfig` | 3 | `[构造]` — minimal(1)、full(1)、repr(1) |
| `test_mcp.py::TestMcpNodeLocal` | 3 | `[构造][生命周期]` — tools(1)、missing root(1)、empty(1) |
| `test_mcp.py::TestMcpNodeLocal` | 1 | `[集成]` — bus connect + graph verify |
| `test_mcp.py::TestMcpNodeRemoteConfig` | 1 | `[构造]` — RemoteConfig + RetryConfig 组合 |
| `test_mcp.py::TestMcpNodeRepr` | 2 | `[类型]` — RetryConfig repr(1)、RemoteConfig repr(1) |
| `test_mcp_live.py` | 1 | `[集成][远端]` — CodeTidy 真实连接 |
| **合计** | **14** | |

---

## 验证命令

```bash
# Rust workspace tests
. "$HOME/.cargo/env" && cargo test --workspace

# Build Python bindings (for py-arf tests)
. "$HOME/.cargo/env" && cd py-arf && cargo build --release

# Python tests (after build)
cd py-arf && ../.venv/bin/python -m pytest tests/test_mcp.py -v
```

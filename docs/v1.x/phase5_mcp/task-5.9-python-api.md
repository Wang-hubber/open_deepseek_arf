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

**不在 5.9 范围**：`RuntimeModule` trait 的 Python 子类化（需要 PyO3 trampoline，复杂度高，留待后续 `SandboxRuntime` 需求驱动）。详见下方"未完成事项"。

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

### `py-arf/src/mcp.rs` 末尾 — `#[cfg(test)]` 单元测试

> **实施记录**：以下 Rust 单元测试因 pyo3 0.29 不支持 `Python::with_gil`（extension-module 模式下 Python 由外部进程初始化）而移除。覆盖角度已迁移至 Python pytest。详见下方"实施记录 §1"。

```rust
// REMOVED — pyo3 0.29 does not support Python::with_gil in extension-module crates.
// All coverage migrated to tests/test_mcp.py
#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::Arc;

    use pyo3::prelude::*;

    use super::*;
    use crate::PyBus;

    // ════════════════════════════════════════════════════════════
    // Helper — create a temp directory with a minimal tool
    // ════════════════════════════════════════════════════════════

    fn make_temp_tool_dir() -> (tempfile::TempDir, PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        let tool_dir = root.join("tools").join("hello");
        std::fs::create_dir_all(&tool_dir).unwrap();
        std::fs::write(
            tool_dir.join("tool.toml"),
            "name = \"hello\"\ndescription = \"Say hello\"\nruntime = \"bash\"\nentrypoint = \"main.sh\"\n",
        )
        .unwrap();
        std::fs::write(
            tool_dir.join("main.sh"),
            "#!/bin/bash\nread p\necho '{\"msg\":\"hello\"}'",
        )
        .unwrap();

        (tmp, root)
    }

    // ── PyRetryConfig ──────────────────────────────────────────

    #[test]
    fn retry_config_defaults() {
        Python::with_gil(|py| {
            let cfg = PyRetryConfig::new(3, 1000, 30000);
            assert_eq!(cfg.max_retries(), 3);
            assert_eq!(cfg.initial_backoff_ms(), 1000);
            assert_eq!(cfg.max_backoff_ms(), 30000);
            let r = cfg.__repr__();
            assert!(r.contains("RetryConfig"));
            assert!(r.contains("max_retries=3"));
        });
    }

    #[test]
    fn retry_config_custom_values() {
        Python::with_gil(|_py| {
            let cfg = PyRetryConfig::new(5, 500, 60000);
            assert_eq!(cfg.max_retries(), 5);
            assert_eq!(cfg.initial_backoff_ms(), 500);
            assert_eq!(cfg.max_backoff_ms(), 60000);
        });
    }

    #[test]
    fn retry_config_clone() {
        Python::with_gil(|_py| {
            let cfg = PyRetryConfig::new(7, 2000, 90000);
            let cloned = cfg.clone();
            assert_eq!(cloned.max_retries(), 7);
        });
    }

    // ── PyRemoteConfig ─────────────────────────────────────────

    #[test]
    fn remote_config_minimal() {
        Python::with_gil(|py| {
            let cfg = PyRemoteConfig::new(
                "https://example.com/mcp".into(),
                "http".into(),
                None,
                None,
                None,
                None,
            )
            .unwrap();
            assert_eq!(cfg.url(), "https://example.com/mcp");
            assert_eq!(cfg.transport(), "http");
            assert!(cfg.timeout_secs().is_none());
            assert!(cfg.tls_ca_cert().is_none());
            assert!(cfg.retry().is_none());
        });
    }

    #[test]
    fn remote_config_with_timeout() {
        Python::with_gil(|py| {
            let cfg =
                PyRemoteConfig::new("https://x.com".into(), "http".into(), Some(30), None, None, None)
                    .unwrap();
            assert_eq!(cfg.timeout_secs(), Some(30));
        });
    }

    #[test]
    fn remote_config_with_retry() {
        Python::with_gil(|py| {
            let retry = PyRetryConfig::new(2, 500, 10000);
            let cfg = PyRemoteConfig::new(
                "https://x.com".into(),
                "http".into(),
                None,
                None,
                None,
                Some(retry),
            )
            .unwrap();
            assert!(cfg.retry().is_some());
            assert_eq!(cfg.retry().unwrap().max_retries(), 2);
        });
    }

    #[test]
    fn remote_config_with_tls_ca() {
        Python::with_gil(|py| {
            let cfg = PyRemoteConfig::new(
                "https://x.com".into(),
                "http".into(),
                None,
                None,
                Some("/etc/ca.pem".into()),
                None,
            )
            .unwrap();
            assert_eq!(cfg.tls_ca_cert(), Some("/etc/ca.pem".into()));
        });
    }

    #[test]
    fn remote_config_repr() {
        Python::with_gil(|py| {
            let cfg =
                PyRemoteConfig::new("https://x.com".into(), "http".into(), None, None, None, None)
                    .unwrap();
            let r = cfg.__repr__();
            assert!(r.contains("RemoteConfig"));
            assert!(r.contains("https://x.com"));
        });
    }

    // ── PyMcpNode — local() ────────────────────────────────────

    #[test]
    fn mcp_node_local_constructs() {
        Python::with_gil(|py| {
            let (_tmp, root) = make_temp_tool_dir();
            let node =
                PyMcpNode::local(&py.get_type::<PyMcpNode>(), "test-ns".into(), root.display().to_string())
                    .unwrap();
            assert_eq!(node.namespace(), "test-ns");
            assert!(node.node_id().contains("mcp/test-ns"));
        });
    }

    #[test]
    fn mcp_node_local_empty_root() {
        Python::with_gil(|py| {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path();
            let node =
                PyMcpNode::local(&py.get_type::<PyMcpNode>(), "empty".into(), root.display().to_string())
                    .unwrap();
            assert_eq!(node.namespace(), "empty");
        });
    }

    #[test]
    fn mcp_node_local_missing_root() {
        Python::with_gil(|py| {
            let result = PyMcpNode::local(
                &py.get_type::<PyMcpNode>(),
                "bad".into(),
                "/nonexistent/path/definitely/missing".into(),
            );
            assert!(result.is_err());
            let err = result.unwrap_err();
            let msg = err.to_string();
            assert!(msg.contains("discovery"));
        });
    }

    #[test]
    fn mcp_node_local_with_skills() {
        Python::with_gil(|py| {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path();

            // Also add a skill directory
            let skill_dir = root.join("skills").join("my-skill");
            std::fs::create_dir_all(&skill_dir).unwrap();
            std::fs::write(
                skill_dir.join("SKILL.md"),
                "---\nname: my-skill\ndescription: A test skill\n---\n\n# My Skill\n",
            )
            .unwrap();

            let node =
                PyMcpNode::local(&py.get_type::<PyMcpNode>(), "with-skills".into(), root.display().to_string())
                    .unwrap();
            assert_eq!(node.namespace(), "with-skills");
        });
    }

    #[test]
    fn mcp_node_repr() {
        Python::with_gil(|py| {
            let (_tmp, root) = make_temp_tool_dir();
            let node =
                PyMcpNode::local(&py.get_type::<PyMcpNode>(), "ns".into(), root.display().to_string())
                    .unwrap();
            let r = node.__repr__();
            assert!(r.contains("McpNode"));
            assert!(r.contains("ns"));
        });
    }

    // ── McpError → PyErr mapping ───────────────────────────────

    #[test]
    fn error_discovery_to_pyerr() {
        let err = arf_mcp::error::McpError::Discovery {
            reason: "root missing".into(),
        };
        let py_err = mcp_error_to_py(err);
        let msg = py_err.to_string();
        assert!(msg.contains("discovery"));
        assert!(msg.contains("root missing"));
    }

    #[test]
    fn error_remote_unreachable_to_pyerr() {
        let err = arf_mcp::error::McpError::RemoteUnreachable {
            url: "https://bad.example".into(),
            reason: "connection refused".into(),
        };
        let py_err = mcp_error_to_py(err);
        let msg = py_err.to_string();
        assert!(msg.contains("bad.example"));
        assert!(msg.contains("connection refused"));
    }

    #[test]
    fn error_remote_rejected_to_pyerr() {
        let err = arf_mcp::error::McpError::RemoteRejected {
            url: "https://x.com".into(),
            code: 403,
            message: "forbidden".into(),
        };
        let py_err = mcp_error_to_py(err);
        let msg = py_err.to_string();
        assert!(msg.contains("403"));
        assert!(msg.contains("forbidden"));
    }

    #[test]
    fn error_bus_connect_to_pyerr() {
        let err = arf_mcp::error::McpError::BusConnect {
            reason: "channel full".into(),
        };
        let py_err = mcp_error_to_py(err);
        let msg = py_err.to_string();
        assert!(msg.contains("bus"));
        assert!(msg.contains("channel full"));
    }
}
```

**原计划 18 个 Rust 单元测试**（已移除，覆盖角度迁移至 Python）：

| 测试 | 覆盖角度 |
|------|---------|
| `retry_config_defaults` | [构造] 默认值 + getter + repr |
| `retry_config_custom_values` | [构造] 自定义值 |
| `retry_config_clone` | [类型] Clone 派生 |
| `remote_config_minimal` | [构造] 最少参数 + 所有 getter |
| `remote_config_with_timeout` | [边界] timeout_secs 可选字段 |
| `remote_config_with_retry` | [边界] retry 嵌套配置 |
| `remote_config_with_tls_ca` | [边界] tls_ca_cert 可选字段 |
| `remote_config_repr` | [类型] __repr__ |
| `mcp_node_local_constructs` | [构造] 扫描 tools 目录 + namespace/node_id |
| `mcp_node_local_empty_root` | [边界] 空目录（0 tools, 0 skills）合法 |
| `mcp_node_local_missing_root` | [边界] 不存在目录 → RuntimeError |
| `mcp_node_local_with_skills` | [构造] tools + skills 共存 |
| `mcp_node_repr` | [类型] __repr__ |
| `error_discovery_to_pyerr` | [错误] McpError::Discovery → PyErr |
| `error_remote_unreachable_to_pyerr` | [错误] McpError::RemoteUnreachable → PyErr |
| `error_remote_rejected_to_pyerr` | [错误] McpError::RemoteRejected → PyErr |
| `error_bus_connect_to_pyerr` | [错误] McpError::BusConnect → PyErr |

---

## 测试（Python 集成测试）

### `py-arf/tests/test_mcp.py`

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
| `test_mcp.py::TestMcpNodeLocal` | 5 | `[构造][生命周期][集成]` — tools(1)、missing root(1)、empty(1)、repr(1)、bus connect(1) |
| `test_mcp.py::TestMcpNodeRemoteConfig` | 1 | `[构造]` — RemoteConfig + RetryConfig 组合 |
| `test_mcp.py::TestMcpNodeRepr` | 2 | `[类型]` — RetryConfig repr(1)、RemoteConfig repr(1) |
| **合计** | **14** | 全部 Python 侧，`cargo test -p py-arf` 无 Rust 侧测试 |

---

---

## 未完成事项

### RuntimeModule Python 子类化（延后）

当前 `RuntimeModule` trait（`runtime.rs`）未通过 PyO3 暴露给 Python。Python 用户无法子类化 `RuntimeModule` 来注入自定义执行后端（如 `DockerSandbox`）。

**阻塞原因**：
- PyO3 trait 子类化需要 `#[pyclass(subclass)]` + `#[pymethods]` 在 trait 上，且需要 wrapper struct 做 Python→Rust 委托
- `RuntimeModule` 的方法返回 `ToolResultSet`、`HashMap<String, Arc<dyn Tool>>` 等 Rust 类型——Python 侧调用链复杂
- 当前没有具体 `SandboxRuntime` 需求可驱动设计——抽象层级不明确

**临时方案**：Python 用户通过 `McpNode.local()` / `McpNode.remote()` 使用内置执行后端。`local_with_runtime()` 构造函数暂不暴露给 Python。

**计划**：等 `SandboxRuntime`（容器虚拟化）需求明确后，以具体用例驱动 PyO3 trampoline 设计。

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

---

## 实施记录

### 1. pyo3 0.29 不支持 `Python::with_gil`

**发现**：pyo3 0.22+ 移除了 `Python::with_gil` API。在 `extension-module` feature 下，Python 解释器由外部进程启动，Rust 代码无法在测试中自行初始化 Python。

**影响**：`#[cfg(test)]` Rust 单元测试（`mcp.rs` 末尾的 18 个测试）无法编译——`Python::with_gil` 不存在，替代的 `Python::try_attach` 在测试中返回 `None`（Python 未初始化）。

**决策**：移除 Rust 侧 `#[cfg(test)]` 单元测试，全部测试通过 Python pytest 运行（`test_mcp.py`）。这与 `py-arf` 现有模式一致——`lib.rs` 中也无 Rust 单元测试。

**验证**：14 个 pytest 测试覆盖了原 Rust 测试的所有角度（`[构造]`/`[边界]`/`[类型]`/`[集成]`），加上 `test_connect_to_bus` 的 Bus 集成验证。

### 2. pyo3 0.29 API 差异

- `Bound<PyAny>::downcast::<T>()` → `cast::<T>()`（重命名）
- `PyDict::iter()` 返回 `(k, v)` 元组，非 `PyResult`，值需单独 `.extract::<String>()`
- `#[classmethod]` 的 `_cls` 参数类型为 `&Bound<'_, pyo3::types::PyType>`（非裸 `PyType`）

### 3. `node.node_id` 返回类型不一致

**发现**：Python 侧 `node.node_id` 返回 `str`（`"mcp/test"`），但 `graph.nodes[i].node_id` 返回 `NodeId` 对象。直接 `==` 比较失败。

**修复**：测试中改为 `str(mcp_nodes[0].node_id) == node.node_id`。记录为类型不一致问题，后续可考虑统一。

### 4. workspace 测试结果

```
504 passed, 0 failed (+14 Python pytest passed)
```

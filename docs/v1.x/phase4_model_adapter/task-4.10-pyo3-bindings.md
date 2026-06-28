# 任务 4.10：ModelAdapter PyO3 绑定

> Phase 4 — ModelAdapter 第十项任务（收尾）
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 前置：任务 4.7 Node 实现完成、Bus 集成测试通过
> 参照：`docs/v1.x/phase1_bus/task-1.10-pyo3-bindings.md`

## 设计思路

将 `DeepSeekProvider`、`OpenAIProvider`、`AnthropicProvider`、`ModelAdapterNode` 及所有数据类型从 Rust 暴露到 Python。延续 Bus 绑定的 `future_into_py` 异步桥接 + `json_value_to_py` 类型映射模式。

### 核心挑战

**1. Provider trait object 传递。** `ModelAdapterNode::new()` 接受 `Arc<dyn Provider>`，但 PyO3 无法直接从 Python 对象提取 trait object。解决方案：每个 PyProvider 提供 `connect_to_bus()` 方法，内部将 `Arc<ConcreteProvider>` coerce 为 `Arc<dyn Provider>` 后调用 `ModelAdapterNode::new()`。Python 侧 API 也自然：

```python
node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))
```

**2. Provider 异步方法。** `chat()` 和 `chat_stream()` 是 `async_trait` 方法，通过 `future_into_py` 映射为 Python awaitable。内部 HTTP 调用（reqwest）需要 tokio runtime——复用现有 `get_runtime()`。

**3. `ModelAdapterNode::shutdown(self)` 消费 `self`。** 与 `Bus::shutdown()` 问题相同——Python 无法保证单所有权。用 `Option<ModelAdapterNode>` 包裹，shutdown 时 take 出来消费。

**4. 数据类型双向构造。** `ModelMessage`、`ModelParams`、`ToolDef` 由 Python 用户构造（→ Rust），`ModelResponsePayload`、`ModelResponseChunk`、`ToolCall`、`Usage` 由 Provider 产出（Rust → Python 只读）。

**5. `serde_json::Value` 字段的 Python 映射。** `extra`、`parameters`、`arguments` 字段复用现有 `json_value_to_py` / `py_object_to_json` 转换函数。

### Python API 预览

```python
from arf import Bus, NodeId
from arf import (
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
    AnthropicConfig, AnthropicProvider,
    ModelAdapterNode, ModelMessage,
    ModelParams, ToolDef,
    ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, Usage,
)

# ── 构造 Config ──
config = DeepSeekConfig(
    api_key="sk-xxx",
    models=["deepseek-v4-flash"],
    base_url="https://api.deepseek.com",  # 可选
    timeout_secs=320,
    max_retries=3,
)

# ── 构造 Provider ──
provider = DeepSeekProvider(config)
print(provider.name, provider.supported_models)

# ── 同步调用（测试/调试）──
messages = [ModelMessage(role="user", content="Hello")]
params = ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=False)
tools = [ToolDef(name="search", description="Search", parameters={"type": "object"})]

response = await provider.chat("deepseek-v4-flash", messages, tools, params)
print(response.message.content)
print(response.usage.input_tokens)

# ── 流式调用 ──
chunks, response = await provider.chat_stream("deepseek-v4-flash", messages, [], params)
for chunk in chunks:
    if chunk.chunk_type == "text":
        print(chunk.content, end="")

# ── 连接 Bus（生产环境）──
bus = Bus()
node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))
# node 在后台运行，监听 model_call 消息并自动回复
graph = bus.graph()
await node.shutdown()
await bus.shutdown()
```

---

## 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `py-arf/Cargo.toml` | 新增 `arf-model-adapter` 依赖 |
| 修改 | `py-arf/src/lib.rs` | 新增 ~13 个 PyClass |
| 修改 | `py-arf/python/arf/__init__.py` | 导出新类型 |
| 新增 | `py-arf/python/arf/examples/phase5_model_adapter.py` | Python 示例 |

---

## 1. `py-arf/Cargo.toml` — 新增依赖

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
arf-model-adapter = { path = "../crates/arf-model-adapter" }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "time"] }
pyo3-async-runtimes = { version = "0.29", features = ["attributes", "tokio-runtime"] }
serde_json = "1"
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `arf-model-adapter` | 新增。导入 `DeepSeekProvider`、`OpenAIProvider`、`AnthropicProvider`、`ModelAdapterNode`、`Provider` trait + 所有数据类型 |

---

## 2. `py-arf/src/lib.rs` — 新增 ModelAdapter 类型

### 2.1 新增 imports

在文件头部现有 import 块后追加：

```rust
use arf_model_adapter::{
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    ModelAdapterNode,
    OpenAIConfig, OpenAIProvider,
    ProviderError,
    ModelCallPayload, ModelParams, ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, ToolDef, Usage,
};
use arf_core::ModelMessage;
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `arf_model_adapter::*` | 导入所有 Provider、Config、Node、数据类型 |
| `arf_core::ModelMessage` | 对话消息类型——Python 用户需要构造它来构建 `chat()` 参数 |

---

### 2.2 PyModelMessage — 对话消息（用户可构造）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyModelMessage
// ═══════════════════════════════════════════════════════════════════

/// Python ModelMessage — a single message in model conversation history.
///
/// Python: ModelMessage(role="user", content="Hello")
#[pyclass(name = "ModelMessage")]
#[derive(Clone)]
struct PyModelMessage {
    inner: ModelMessage,
}

#[pymethods]
impl PyModelMessage {
    #[new]
    #[pyo3(signature = (role, content, tool_call_id=None, name=None, extra=None))]
    fn new(
        py: Python<'_>,
        role: String,
        content: String,
        tool_call_id: Option<String>,
        name: Option<String>,
        extra: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let extra_json = match extra {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::Value::Null,
        };
        Ok(Self {
            inner: ModelMessage {
                role,
                content,
                tool_call_id,
                name,
                extra: extra_json,
            },
        })
    }

    #[getter]
    fn role(&self) -> String {
        self.inner.role.clone()
    }

    #[getter]
    fn content(&self) -> String {
        self.inner.content.clone()
    }

    #[getter]
    fn tool_call_id(&self) -> Option<String> {
        self.inner.tool_call_id.clone()
    }

    #[getter]
    fn name(&self) -> Option<String> {
        self.inner.name.clone()
    }

    #[getter]
    fn extra(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.extra, py)
    }

    fn __repr__(&self) -> String {
        match &self.inner.tool_call_id {
            Some(tc_id) => format!(
                "ModelMessage(role='{}', content='{}...', tool_call_id='{}')",
                self.inner.role,
                &self.inner.content.chars().take(40).collect::<String>(),
                tc_id
            ),
            None => format!(
                "ModelMessage(role='{}', content='{}...')",
                self.inner.role,
                &self.inner.content.chars().take(40).collect::<String>(),
            ),
        }
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `#[derive(Clone)]` | Provider `chat()` 的参数是 `Vec<ModelMessage>`——需要从 Python list 中逐个 clone 提取 |
| `#[pyo3(signature = ...)]` | `tool_call_id`、`name`、`extra` 均为可选参数，默认 None。`role` 和 `content` 必需 |
| `py_object_to_json` | `extra` 是 `serde_json::Value`——用户传入 Python dict/list/str，转成 JSON 后存储 |
| `getter extra` | 读出时逆向转换：`serde_json::Value` → Python 对象。`ModelResponsePayload.message` 的 extra 可能含 `reasoning_content` |
| `__repr__` | 截断 content 到 40 字符，避免超长消息刷屏 |

---

### 2.3 PyModelParams — 模型参数（用户可构造）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyModelParams
// ═══════════════════════════════════════════════════════════════════

/// Python ModelParams — inference parameters for a model call.
///
/// Python: ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=True)
#[pyclass(name = "ModelParams")]
#[derive(Clone)]
struct PyModelParams {
    inner: ModelParams,
}

#[pymethods]
impl PyModelParams {
    #[new]
    #[pyo3(signature = (temperature=None, max_tokens=None, thinking_enabled=false, extra=None))]
    fn new(
        py: Python<'_>,
        temperature: Option<f32>,
        max_tokens: Option<u32>,
        thinking_enabled: bool,
        extra: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let extra_json = match extra {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::Value::Null,
        };
        Ok(Self {
            inner: ModelParams {
                temperature,
                max_tokens,
                thinking_enabled,
                extra: extra_json,
            },
        })
    }

    #[getter]
    fn temperature(&self) -> Option<f32> {
        self.inner.temperature
    }

    #[getter]
    fn max_tokens(&self) -> Option<u32> {
        self.inner.max_tokens
    }

    #[getter]
    fn thinking_enabled(&self) -> bool {
        self.inner.thinking_enabled
    }

    #[getter]
    fn extra(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.extra, py)
    }

    fn __repr__(&self) -> String {
        format!(
            "ModelParams(temperature={:?}, max_tokens={:?}, thinking_enabled={})",
            self.inner.temperature, self.inner.max_tokens, self.inner.thinking_enabled
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `thinking_enabled=false` | 默认不启用思考模式——与 Rust 侧 `ModelParams.thinking_enabled: bool` 默认值一致 |
| `temperature: Option<f32>` | None 表示使用供应商默认值；Some(0.0)–Some(2.0) 为有效范围 |
| `extra` | 供应商特定参数，如 `{"reasoning_effort": "high"}`（DeepSeek）或 `{"top_p": 0.9}`（OpenAI） |

---

### 2.4 PyToolDef — 工具定义（用户可构造）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyToolDef
// ═══════════════════════════════════════════════════════════════════

/// Python ToolDef — tool/function definition for function calling.
///
/// Python: ToolDef(name="search", description="Search", parameters={"type": "object"})
#[pyclass(name = "ToolDef")]
#[derive(Clone)]
struct PyToolDef {
    inner: ToolDef,
}

#[pymethods]
impl PyToolDef {
    #[new]
    #[pyo3(signature = (name, description, parameters))]
    fn new(
        py: Python<'_>,
        name: String,
        description: String,
        parameters: Py<PyAny>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ToolDef {
                name,
                description,
                parameters: py_object_to_json(&parameters, py)?,
            },
        })
    }

    #[getter]
    fn name(&self) -> String {
        self.inner.name.clone()
    }

    #[getter]
    fn description(&self) -> String {
        self.inner.description.clone()
    }

    #[getter]
    fn parameters(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.parameters, py)
    }

    fn __repr__(&self) -> String {
        format!("ToolDef(name='{}', description='{}')", self.inner.name, self.inner.description)
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `parameters` 必填 | JSON Schema 对象，如 `{"type": "object", "properties": {...}}` |
| `py_object_to_json` | Python dict → `serde_json::Value`，内部各 Provider 再转为 API 特定格式 |

---

### 2.5 PyToolCall — 工具调用（只读）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyToolCall
// ═══════════════════════════════════════════════════════════════════

/// Python ToolCall — a tool call request from the model (read-only).
#[pyclass(name = "ToolCall")]
struct PyToolCall {
    inner: ToolCall,
}

#[pymethods]
impl PyToolCall {
    #[getter]
    fn id(&self) -> String {
        self.inner.id.clone()
    }

    #[getter]
    fn name(&self) -> String {
        self.inner.name.clone()
    }

    #[getter]
    fn arguments(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.arguments, py)
    }

    fn __repr__(&self) -> String {
        format!("ToolCall(id='{}', name='{}')", self.inner.id, self.inner.name)
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| 无 `#[new]` | 只读类型——仅由 Provider 内部创建，Python 用户不构造。`from_inner()` 工厂方法在 Provider 响应解析时使用 |
| `arguments` getter | `serde_json::Value` → Python dict，如 `{"query": "rust"}` |

---

### 2.6 PyToolCallDelta — 流式工具调用增量（只读）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyToolCallDelta
// ═══════════════════════════════════════════════════════════════════

/// Python ToolCallDelta — incremental tool call update during streaming (read-only).
#[pyclass(name = "ToolCallDelta")]
struct PyToolCallDelta {
    inner: ToolCallDelta,
}

#[pymethods]
impl PyToolCallDelta {
    #[getter]
    fn index(&self) -> u32 {
        self.inner.index
    }

    #[getter]
    fn id(&self) -> Option<String> {
        self.inner.id.clone()
    }

    #[getter]
    fn name(&self) -> Option<String> {
        self.inner.name.clone()
    }

    #[getter]
    fn arguments_delta(&self) -> Option<String> {
        self.inner.arguments_delta.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "ToolCallDelta(index={}, name={:?})",
            self.inner.index, self.inner.name
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `arguments_delta` | JSON 片段字符串——调用方需跨 chunk 累积拼接后 parse |

---

### 2.7 PyUsage — token 用量（只读）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyUsage
// ═══════════════════════════════════════════════════════════════════

/// Python Usage — token usage statistics (read-only).
#[pyclass(name = "Usage")]
struct PyUsage {
    inner: Usage,
}

#[pymethods]
impl PyUsage {
    #[getter]
    fn input_tokens(&self) -> u32 {
        self.inner.input_tokens
    }

    #[getter]
    fn output_tokens(&self) -> u32 {
        self.inner.output_tokens
    }

    #[getter]
    fn total_tokens(&self) -> u32 {
        self.inner.total_tokens
    }

    fn __repr__(&self) -> String {
        format!(
            "Usage(input={}, output={}, total={})",
            self.inner.input_tokens, self.inner.output_tokens, self.inner.total_tokens
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `u32` → Python int | PyO3 自动映射，无需手动转换 |
| `total_tokens` | `input_tokens + output_tokens` 的便利汇总 |

---

### 2.8 PyModelResponseChunk — 流式响应块（只读）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyModelResponseChunk
// ═══════════════════════════════════════════════════════════════════

/// Python ModelResponseChunk — a single chunk in a streaming response (read-only).
#[pyclass(name = "ModelResponseChunk")]
struct PyModelResponseChunk {
    inner: ModelResponseChunk,
}

#[pymethods]
impl PyModelResponseChunk {
    #[getter]
    fn chunk_type(&self) -> String {
        self.inner.chunk_type.clone()
    }

    #[getter]
    fn content(&self) -> Option<String> {
        self.inner.content.clone()
    }

    #[getter]
    fn reasoning(&self) -> Option<String> {
        self.inner.reasoning.clone()
    }

    #[getter]
    fn tool_call(&self) -> Option<PyToolCallDelta> {
        self.inner.tool_call.clone().map(|tc| PyToolCallDelta { inner: tc })
    }

    #[getter]
    fn usage(&self) -> Option<PyUsage> {
        self.inner.usage.map(|u| PyUsage { inner: u })
    }

    fn __repr__(&self) -> String {
        match self.inner.chunk_type.as_str() {
            "text" => format!(
                "ModelResponseChunk(type='text', content='{}...')",
                self.inner.content.as_deref().unwrap_or("").chars().take(30).collect::<String>()
            ),
            "reasoning" => format!(
                "ModelResponseChunk(type='reasoning', len={})",
                self.inner.reasoning.as_deref().map_or(0, |r| r.len())
            ),
            _ => format!("ModelResponseChunk(type='{}')", self.inner.chunk_type),
        }
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `chunk_type` | 四种类型：`"text"`（文本增量）、`"reasoning"`（推理增量，DeepSeek）、`"tool_call"`（工具调用增量）、`"usage"`（token 统计） |
| `tool_call` getter | `Option<ToolCallDelta>` → `Option<PyToolCallDelta>`。流式过程中每个 tool_call chunk 携带增量参数 |
| `usage` getter | 仅在最后一个 `"usage"` chunk 中出现——`Usage` 是 Copy 类型（全字段 u32），直接解包 |

---

### 2.9 PyModelResponsePayload — 模型响应（只读）

```rust
// ═══════════════════════════════════════════════════════════════════
// PyModelResponsePayload
// ═══════════════════════════════════════════════════════════════════

/// Python ModelResponsePayload — complete model response (read-only).
#[pyclass(name = "ModelResponsePayload")]
struct PyModelResponsePayload {
    inner: ModelResponsePayload,
}

#[pymethods]
impl PyModelResponsePayload {
    #[getter]
    fn message(&self) -> PyModelMessage {
        PyModelMessage { inner: self.inner.message.clone() }
    }

    #[getter]
    fn tool_calls(&self) -> Option<Vec<PyToolCall>> {
        self.inner.tool_calls.as_ref().map(|tc_list| {
            tc_list
                .iter()
                .map(|tc| PyToolCall { inner: tc.clone() })
                .collect()
        })
    }

    #[getter]
    fn finish_reason(&self) -> String {
        self.inner.finish_reason.clone()
    }

    #[getter]
    fn usage(&self) -> Option<PyUsage> {
        self.inner.usage.map(|u| PyUsage { inner: u })
    }

    #[getter]
    fn id(&self) -> String {
        self.inner.id.clone()
    }

    #[getter]
    fn model(&self) -> String {
        self.inner.model.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "ModelResponsePayload(finish_reason='{}', model='{}', usage={:?})",
            self.inner.finish_reason,
            self.inner.model,
            self.inner.usage.as_ref().map(|u| u.total_tokens)
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `message` getter | 提取 `ModelMessage`——role=`"assistant"`，content 为模型文本输出，extra 可能含 `reasoning_content` |
| `tool_calls` getter | `Option<Vec<ToolCall>>` → `Option<Vec<PyToolCall>>`。有工具调用时 `finish_reason` 为 `"tool_calls"` |
| `finish_reason` | `"stop"`（正常结束）、`"tool_calls"`（工具调用）、`"length"`（token 截断）、`"error"`（出错） |
| `usage` | 可能为 None（某些错误响应不含 usage 信息） |

---

### 2.10 ProviderError 映射

```rust
// ═══════════════════════════════════════════════════════════════════
// ProviderError → Python exception
// ═══════════════════════════════════════════════════════════════════

fn provider_error_to_py(err: ProviderError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `err.to_string()` | `ProviderError` 实现 `Display`，四种变体均有可读信息——`Transport("connection refused")`、`Api { status: 401, message: "Unauthorized" }`、`RetryExhausted { attempts: 3, last_error: "..." }`、`Parse("unexpected field")` |
| 统一 `PyException` | 不细分子类——Python 侧 `try/except Exception as e` 可捕获，`str(e)` 含完整错误信息 |

---

### 2.11 PyDeepSeekConfig / PyOpenAIConfig / PyAnthropicConfig — 供应商配置

```rust
// ═══════════════════════════════════════════════════════════════════
// PyDeepSeekConfig
// ═══════════════════════════════════════════════════════════════════

/// Python DeepSeekConfig — configuration for a DeepSeek provider.
///
/// Python: DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
#[pyclass(name = "DeepSeekConfig")]
#[derive(Clone)]
struct PyDeepSeekConfig {
    inner: DeepSeekConfig,
}

#[pymethods]
impl PyDeepSeekConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.deepseek.com".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: DeepSeekConfig {
                base_url,
                api_key,
                models,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String { self.inner.base_url.clone() }

    #[getter]
    fn api_key(&self) -> String { self.inner.api_key.clone() }

    #[getter]
    fn models(&self) -> Vec<String> { self.inner.models.clone() }

    #[getter]
    fn timeout_secs(&self) -> u64 { self.inner.timeout_secs }

    #[getter]
    fn max_retries(&self) -> u32 { self.inner.max_retries }

    fn __repr__(&self) -> String {
        format!(
            "DeepSeekConfig(base_url='{}', models={:?})",
            self.inner.base_url, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyOpenAIConfig
// ═══════════════════════════════════════════════════════════════════

/// Python OpenAIConfig — configuration for an OpenAI provider.
///
/// Python: OpenAIConfig(api_key="sk-xxx", models=["gpt-4o"])
#[pyclass(name = "OpenAIConfig")]
#[derive(Clone)]
struct PyOpenAIConfig {
    inner: OpenAIConfig,
}

#[pymethods]
impl PyOpenAIConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.openai.com".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: OpenAIConfig {
                base_url,
                api_key,
                models,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String { self.inner.base_url.clone() }
    #[getter]
    fn api_key(&self) -> String { self.inner.api_key.clone() }
    #[getter]
    fn models(&self) -> Vec<String> { self.inner.models.clone() }
    #[getter]
    fn timeout_secs(&self) -> u64 { self.inner.timeout_secs }
    #[getter]
    fn max_retries(&self) -> u32 { self.inner.max_retries }

    fn __repr__(&self) -> String {
        format!(
            "OpenAIConfig(base_url='{}', models={:?})",
            self.inner.base_url, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyAnthropicConfig
// ═══════════════════════════════════════════════════════════════════

/// Python AnthropicConfig — configuration for an Anthropic provider.
///
/// Python: AnthropicConfig(api_key="sk-xxx", models=["claude-sonnet-4-6"])
#[pyclass(name = "AnthropicConfig")]
#[derive(Clone)]
struct PyAnthropicConfig {
    inner: AnthropicConfig,
}

#[pymethods]
impl PyAnthropicConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.anthropic.com".into(), api_path="/v1/messages".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        api_path: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: AnthropicConfig {
                base_url,
                api_key,
                models,
                api_path,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String { self.inner.base_url.clone() }
    #[getter]
    fn api_key(&self) -> String { self.inner.api_key.clone() }
    #[getter]
    fn models(&self) -> Vec<String> { self.inner.models.clone() }
    #[getter]
    fn api_path(&self) -> String { self.inner.api_path.clone() }
    #[getter]
    fn timeout_secs(&self) -> u64 { self.inner.timeout_secs }
    #[getter]
    fn max_retries(&self) -> u32 { self.inner.max_retries }

    fn __repr__(&self) -> String {
        format!(
            "AnthropicConfig(base_url='{}', api_path='{}', models={:?})",
            self.inner.base_url, self.inner.api_path, self.inner.models
        )
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| 三个 Config | 结构相同：`api_key` + `models` 必填，`base_url`/`timeout_secs`/`max_retries` 有默认值。Anthropic 额外有 `api_path` 字段（用于 DeepSeek 的 Anthropic 兼容端点 `/anthropic`） |
| `#[derive(Clone)]` | 允许 Python 侧复用 config 创建多个 provider |
| getters | 暴露所有字段为只读属性——配置一旦构造不鼓励修改 |

---

### 2.12 PyDeepSeekProvider / PyOpenAIProvider / PyAnthropicProvider

```rust
// ═══════════════════════════════════════════════════════════════════
// PyDeepSeekProvider
// ═══════════════════════════════════════════════════════════════════

/// Python DeepSeekProvider — DeepSeek API chat completions.
///
/// Python: provider = DeepSeekProvider(config)
#[pyclass(name = "DeepSeekProvider")]
struct PyDeepSeekProvider {
    inner: Arc<DeepSeekProvider>,
}

#[pymethods]
impl PyDeepSeekProvider {
    #[new]
    fn new(config: &PyDeepSeekConfig) -> Self {
        Self {
            inner: Arc::new(DeepSeekProvider::new(config.inner.clone())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "deepseek"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.config.models.clone()
    }

    fn chat<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        bus: &PyBus,
        node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;

        future_into_py(py, async move {
            ModelAdapterNode::new(provider, &bus_ref, nid)
                .await
                .map(|node| PyModelAdapterNode { inner: Some(node) })
                .map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())
                })
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyOpenAIProvider
// ═══════════════════════════════════════════════════════════════════

/// Python OpenAIProvider — standard OpenAI-compatible chat completions.
#[pyclass(name = "OpenAIProvider")]
struct PyOpenAIProvider {
    inner: Arc<OpenAIProvider>,
}

#[pymethods]
impl PyOpenAIProvider {
    #[new]
    fn new(config: &PyOpenAIConfig) -> Self {
        Self {
            inner: Arc::new(OpenAIProvider::new(config.inner.clone())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "openai"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.config.models.clone()
    }

    fn chat<'py>(
        &self, py: Python<'py>,
        model_name: String, messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>, params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self, py: Python<'py>,
        model_name: String, messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>, params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self, py: Python<'py>, bus: &PyBus, node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;

        future_into_py(py, async move {
            ModelAdapterNode::new(provider, &bus_ref, nid)
                .await
                .map(|node| PyModelAdapterNode { inner: Some(node) })
                .map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())
                })
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyAnthropicProvider
// ═══════════════════════════════════════════════════════════════════

/// Python AnthropicProvider — Anthropic Messages API.
#[pyclass(name = "AnthropicProvider")]
struct PyAnthropicProvider {
    inner: Arc<AnthropicProvider>,
}

#[pymethods]
impl PyAnthropicProvider {
    #[new]
    fn new(config: &PyAnthropicConfig) -> Self {
        Self {
            inner: Arc::new(AnthropicProvider::new(config.inner.clone())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "anthropic"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.config.models.clone()
    }

    fn chat<'py>(
        &self, py: Python<'py>,
        model_name: String, messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>, params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self, py: Python<'py>,
        model_name: String, messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>, params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self, py: Python<'py>, bus: &PyBus, node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;

        future_into_py(py, async move {
            ModelAdapterNode::new(provider, &bus_ref, nid)
                .await
                .map(|node| PyModelAdapterNode { inner: Some(node) })
                .map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())
                })
        })
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `inner: Arc<ConcreteProvider>` | 每个 Provider 持有 `Arc<ConcreteProvider>`（非 `Arc<dyn Provider>`）。`connect_to_bus()` 中 `let provider: Arc<dyn Provider> = self.inner.clone()` 自动 coerce |
| `chat()` | 提取参数为 Rust `Vec<ModelMessage>`/`Vec<ToolDef>` → `future_into_py` → 调用 `provider.chat()` → 映射 Result |
| `chat_stream()` | 同 `chat()` 但返回 `(Vec<PyModelResponseChunk>, PyModelResponsePayload)` tuple。PyO3 自动将 Rust tuple 转为 Python tuple |
| `connect_to_bus()` | `Arc<ConcreteProvider>` → `Arc<dyn Provider>` → `ModelAdapterNode::new()` → `PyModelAdapterNode`。Python 侧 `await provider.connect_to_bus(bus, node_id)` |
| `name` getter | 硬编码字符串——与 Rust Provider trait 的 `name()` 一致 |
| `supported_models` getter | 从内部 config 读取——不调用 Provider trait 方法（避免 async 开销），字段直接访问足够 |
| 三个 Provider 结构高度一致 | 区别仅在 `name` 返回值与 config 类型。不抽象——重复优于模棱两可的泛型 |

**为什么 `supported_models` 不调用 trait 方法？** `Provider::supported_models()` 返回 `&[String]`，但 PyO3 getter 需要返回 Python 可用的 `Vec<String>`。直接读 `self.inner.config.models.clone()` 等价且简单。

---

### 2.13 PyModelAdapterNode — Bus 节点

```rust
// ═══════════════════════════════════════════════════════════════════
// PyModelAdapterNode
// ═══════════════════════════════════════════════════════════════════

/// Python ModelAdapterNode — a model adapter connected to the Bus.
///
/// Created by Provider.connect_to_bus(), not constructed directly.
#[pyclass(name = "ModelAdapterNode")]
struct PyModelAdapterNode {
    inner: Option<ModelAdapterNode>,
}

#[pymethods]
impl PyModelAdapterNode {
    #[getter]
    fn node_id(&self) -> PyResult<PyNodeId> {
        match &self.inner {
            Some(node) => Ok(PyNodeId {
                inner: node.node_id().clone(),
            }),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "node already shut down",
            )),
        }
    }

    fn shutdown<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let node = self.inner.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already shut down")
        })?;

        future_into_py(py, async move {
            node.shutdown().await;
            Ok(())
        })
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            Some(node) => format!(
                "ModelAdapterNode(node_id='{}')",
                node.node_id().as_str()
            ),
            None => "ModelAdapterNode(shut down)".into(),
        }
    }
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `inner: Option<ModelAdapterNode>` | `shutdown(self)` 消费 `self`——用 `Option` 包裹，shutdown 时 `take()` 取出并消费。二次调用返回 `PyRuntimeError` |
| `node_id` getter | 委托给 `ModelAdapterNode::node_id()`（返回 `&NodeId`），clone 后包装为 `PyNodeId` |
| `shutdown(&mut self)` | 需 `&mut self`（修改 `self.inner` 为 None）。`future_into_py` 确保异步 shutdown 在 tokio 上运行 |
| 无 `#[new]` | 不直接构造——仅通过 `provider.connect_to_bus()` 创建（见 2.12） |

---

### 2.14 模块注册 — 追加新类型

在现有 `_arf` 函数的 `add_class` 链后追加：

```rust
#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "1.0.0-alpha.0")?;

    // Phase 1: Bus types
    m.add_class::<PyNodeId>()?;
    m.add_class::<PyMessage>()?;
    m.add_class::<PyNodeInfo>()?;
    m.add_class::<PyToMatch>()?;
    m.add_class::<PyMessageFilter>()?;
    m.add_class::<PySendReceipt>()?;
    m.add_class::<PyBusGraph>()?;
    m.add_class::<PyBus>()?;
    m.add_class::<PyNodeHandle>()?;

    // Phase 4: ModelAdapter types
    m.add_class::<PyModelMessage>()?;
    m.add_class::<PyModelParams>()?;
    m.add_class::<PyToolDef>()?;
    m.add_class::<PyToolCall>()?;
    m.add_class::<PyToolCallDelta>()?;
    m.add_class::<PyUsage>()?;
    m.add_class::<PyModelResponseChunk>()?;
    m.add_class::<PyModelResponsePayload>()?;
    m.add_class::<PyDeepSeekConfig>()?;
    m.add_class::<PyOpenAIConfig>()?;
    m.add_class::<PyAnthropicConfig>()?;
    m.add_class::<PyDeepSeekProvider>()?;
    m.add_class::<PyOpenAIProvider>()?;
    m.add_class::<PyAnthropicProvider>()?;
    m.add_class::<PyModelAdapterNode>()?;

    Ok(())
}
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| 14 个新 class | 6 个数据类（message/params/tooldef/toolcall/chunk/response） + 3 个 config + 3 个 provider + 1 个 node + 1 个旧（ToolCallDelta/Usage 各算数据类）= 14。加上注释中的分隔标记，保持 lib.rs 可读性 |

---

## 3. `py-arf/python/arf/__init__.py` — 导出新类型

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
    # Phase 4: ModelAdapter
    AnthropicConfig,
    AnthropicProvider,
    DeepSeekConfig,
    DeepSeekProvider,
    ModelAdapterNode,
    ModelMessage,
    ModelParams,
    ModelResponseChunk,
    ModelResponsePayload,
    OpenAIConfig,
    OpenAIProvider,
    ToolCall,
    ToolCallDelta,
    ToolDef,
    Usage,
)

__all__ = [
    "__version__",
    # Phase 1: Bus
    "Bus",
    "BusGraph",
    "Message",
    "MessageFilter",
    "NodeHandle",
    "NodeId",
    "NodeInfo",
    "SendReceipt",
    "ToMatch",
    # Phase 4: ModelAdapter
    "AnthropicConfig",
    "AnthropicProvider",
    "DeepSeekConfig",
    "DeepSeekProvider",
    "ModelAdapterNode",
    "ModelMessage",
    "ModelParams",
    "ModelResponseChunk",
    "ModelResponsePayload",
    "OpenAIConfig",
    "OpenAIProvider",
    "ToolCall",
    "ToolCallDelta",
    "ToolDef",
    "Usage",
]
```

---

## 4. `py-arf/python/arf/examples/phase5_model_adapter.py` — 示例

```python
"""Phase 5 ModelAdapter — Python API usage examples.

Demonstrates:
  1. Config + Provider construction
  2. Direct chat() and chat_stream() calls
  3. Bus integration via connect_to_bus()
"""

import asyncio
from arf import Bus, NodeId
from arf import (
    DeepSeekConfig,
    DeepSeekProvider,
    ModelAdapterNode,
    ModelMessage,
    ModelParams,
    ToolDef,
)


async def example_direct_chat():
    """Synchronous (non-streaming) chat — for testing/debugging."""
    print("=== Direct Chat ===")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)
    print(f"Provider: {provider.name}, models: {provider.supported_models}")

    messages = [
        ModelMessage(role="system", content="You are a helpful assistant."),
        ModelMessage(role="user", content="What is 2+2?"),
    ]
    params = ModelParams(temperature=0.7, max_tokens=256, thinking_enabled=False)
    tools = [
        ToolDef(
            name="calculator",
            description="Evaluate a math expression",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression to evaluate"}
                },
                "required": ["expression"],
            },
        )
    ]

    # NOTE: requires real API key to work
    try:
        response = await provider.chat("deepseek-v4-flash", messages, tools, params)
        print(f"Response: {response.message.content}")
        print(f"Finish: {response.finish_reason}, Tokens: {response.usage.total_tokens if response.usage else 'N/A'}")
        if response.tool_calls:
            for tc in response.tool_calls:
                print(f"  Tool call: {tc.name}({tc.arguments})")
    except Exception as e:
        print(f"Chat failed (expected with placeholder key): {e}")


async def example_streaming_chat():
    """Streaming chat — demonstrates chunk iteration."""
    print("\n=== Streaming Chat ===")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)

    messages = [ModelMessage(role="user", content="Count from 1 to 5.")]
    params = ModelParams(temperature=0.7, max_tokens=128, thinking_enabled=False)

    try:
        chunks, response = await provider.chat_stream(
            "deepseek-v4-flash", messages, [], params
        )
        for chunk in chunks:
            if chunk.chunk_type == "text":
                print(chunk.content, end="", flush=True)
            elif chunk.chunk_type == "reasoning":
                print(f"[Reasoning: {chunk.reasoning[:50]}...]")
        print(f"\nUsage: {response.usage}")
    except Exception as e:
        print(f"Streaming failed (expected with placeholder key): {e}")


async def example_bus_integration():
    """Connect a provider to the Bus as a ModelAdapterNode."""
    print("\n=== Bus Integration ===")

    bus = Bus()
    print(f"Bus created, uptime: {bus.uptime_ms}ms")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)

    # Connect as a Bus node — the node now listens for model_call messages
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))
    print(f"Node connected: {node}")

    # Verify node appears in bus graph
    graph = bus.graph()
    for n in graph.nodes:
        if n.node_id == NodeId("model/deepseek"):
            print(f"  Found in graph: {n}")
            print(f"  Capabilities: {n.capabilities}")
            break

    # Shutdown
    await node.shutdown()
    print("Node shut down")

    await bus.shutdown()
    print("Bus shut down")

    # Verify safety: double-shutdown raises
    try:
        await node.shutdown()
    except RuntimeError as e:
        print(f"Double-shutdown correctly rejected: {e}")


async def main():
    await example_bus_integration()
    # The following require real API keys:
    # await example_direct_chat()
    # await example_streaming_chat()


if __name__ == "__main__":
    asyncio.run(main())
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `example_bus_integration()` | 无需 API key——纯 Bus 操作，验证 connect/shutdown/双次 shutdown 拒绝 |
| `example_direct_chat()` / `example_streaming_chat()` | 需真实 API key 才能成功——注释掉，仅作为 API 用法示范 |
| `ToolDef(parameters=...)` | JSON Schema dict 自动转为 `serde_json::Value` |
| `ModelMessage(role, content)` | 最简构造——`tool_call_id`/`name`/`extra` 用默认值 |

---

## 验证

```bash
# 编译（确保 Cargo.toml 正确）
cargo build -p py-arf

# Python 导入测试（同步构造，无需 API key）
python -c "
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
    ModelAdapterNode, ModelMessage,
    ModelParams, ToolDef,
    ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, Usage,
)

# Config
ds = DeepSeekConfig(api_key='sk-test', models=['deepseek-v4-flash'])
print('DeepSeekConfig:', ds)

oa = OpenAIConfig(api_key='sk-test', models=['gpt-4o'])
print('OpenAIConfig:', oa)

an = AnthropicConfig(api_key='sk-test', models=['claude-sonnet-4-6'])
print('AnthropicConfig:', an)

# Provider
provider = DeepSeekProvider(ds)
print('Provider:', provider.name, provider.supported_models)

# Data types
msg = ModelMessage(role='user', content='Hello')
print('ModelMessage:', msg)

msg_full = ModelMessage(role='tool', content='result', tool_call_id='call_1', name='search')
print('ModelMessage full:', msg_full)

params = ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=True)
print('ModelParams:', params)

tool = ToolDef(name='search', description='Search', parameters={'type': 'object'})
print('ToolDef:', tool)

# Usage and ToolCall are read-only (no __init__)
# They are created by providers internally

print('All imports OK')
"

# Bus 集成测试（无需 API key）
python -c "
import asyncio
from arf import Bus, NodeId, DeepSeekConfig, DeepSeekProvider

async def test():
    bus = Bus()
    config = DeepSeekConfig(api_key='sk-test', models=['deepseek-v4-flash'])
    provider = DeepSeekProvider(config)
    node = await provider.connect_to_bus(bus, NodeId('model/test'))
    print('Node connected:', node)
    graph = bus.graph()
    assert len(graph.nodes) == 1
    print('Graph OK, nodes:', len(graph.nodes))
    await node.shutdown()
    print('Node shut down')
    await bus.shutdown()
    print('Bus shut down')
    # Double shutdown should raise
    try:
        await node.shutdown()
        assert False, 'should have raised'
    except RuntimeError as e:
        print('Double-shutdown rejected:', e)
    print('All bus integration tests passed')

asyncio.run(test())
"

# 全 workspace 测试
cargo test --workspace
```

---

## 汇总

| 项目 | 数量 |
|------|------|
| 新增 PyClass | 14 |
| 代码行数 (lib.rs 新增) | ~550 行 |
| 修改文件 | 3（Cargo.toml + lib.rs + __init__.py） |
| 新增文件 | 1（Python 示例） |
| 公开类型 | 14（3 config + 3 provider + 1 node + 7 数据类） |

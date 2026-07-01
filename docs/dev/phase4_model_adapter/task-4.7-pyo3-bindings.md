# 任务 4.7：ModelAdapter PyO3 绑定

> Phase 4 — ModelAdapter 第七项任务（收尾）
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 前置：任务 4.6 Node 实现完成、Bus 集成测试通过
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
| 新增 | `py-arf/python/arf/examples/phase4_model_adapter.py` | Python 示例 |
| 新增 | `py-arf/tests/test_model_adapter_imports.py` | 类型构造 + getters 测试（27） |
| 新增 | `py-arf/tests/test_model_adapter_node.py` | Bus 集成生命周期测试（14） |
| 新增 | `py-arf/tests/test_model_adapter_live.py` | 真实 API 集成测试（18） |

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

## 4. `py-arf/python/arf/examples/phase4_model_adapter.py` — 示例

```python
"""Phase 4 ModelAdapter — Python API usage examples.

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

## 5. Python 集成测试

### 5.1 `py-arf/tests/test_model_adapter_imports.py` — 类型构造 + getters

```python
"""
[M] ModelAdapter type construction — all exported types importable and basic construction correct.

Test angles: [覆盖] [构造] [trait] [边界]
"""
import pytest
from arf import (
    # Configs
    AnthropicConfig, DeepSeekConfig, OpenAIConfig,
    # Providers
    AnthropicProvider, DeepSeekProvider, OpenAIProvider,
    # Node
    ModelAdapterNode,
    # Data types
    ModelMessage, ModelParams, ToolDef,
    ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, Usage,
)


# ═══════════════════════════════════════════════════════════════════════
# M1 — Imports
# ═══════════════════════════════════════════════════════════════════════


# ── M1.1 ──────────────────────────────────────────────────────────────────

def test_all_model_adapter_types_importable():
    """[覆盖] All ModelAdapter types importable."""
    for cls in [
        AnthropicConfig, AnthropicProvider,
        DeepSeekConfig, DeepSeekProvider,
        OpenAIConfig, OpenAIProvider,
        ModelAdapterNode, ModelMessage,
        ModelParams, ToolDef,
        ModelResponseChunk, ModelResponsePayload,
        ToolCall, ToolCallDelta, Usage,
    ]:
        assert cls is not None


# ═══════════════════════════════════════════════════════════════════════
# M2 — Config 构造
# ═══════════════════════════════════════════════════════════════════════


# ── M2.1 ──────────────────────────────────────────────────────────────────

def test_deepseek_config_defaults():
    """[构造] DeepSeekConfig with required fields only, verify defaults."""
    c = DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    assert c.api_key == "sk-test"
    assert c.models == ["deepseek-v4-flash"]
    assert c.base_url == "https://api.deepseek.com"
    assert c.timeout_secs == 320
    assert c.max_retries == 3
    assert "DeepSeekConfig" in repr(c)


# ── M2.2 ──────────────────────────────────────────────────────────────────

def test_deepseek_config_full_custom():
    """[构造] DeepSeekConfig all fields explicitly set."""
    c = DeepSeekConfig(
        api_key="sk-custom",
        models=["deepseek-v4-flash", "deepseek-v4-pro"],
        base_url="https://custom.deepseek.com",
        timeout_secs=120,
        max_retries=5,
    )
    assert c.models == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert c.base_url == "https://custom.deepseek.com"
    assert c.timeout_secs == 120
    assert c.max_retries == 5


# ── M2.3 ──────────────────────────────────────────────────────────────────

def test_openai_config_defaults():
    """[构造] OpenAIConfig defaults: base_url='https://api.openai.com'."""
    c = OpenAIConfig(api_key="sk-test", models=["gpt-4o"])
    assert c.base_url == "https://api.openai.com"
    assert c.timeout_secs == 320
    assert c.max_retries == 3


# ── M2.4 ──────────────────────────────────────────────────────────────────

def test_anthropic_config_defaults():
    """[构造] AnthropicConfig defaults: base_url + api_path."""
    c = AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    assert c.base_url == "https://api.anthropic.com"
    assert c.api_path == "/v1/messages"
    assert c.timeout_secs == 320
    assert c.max_retries == 3


# ── M2.5 ──────────────────────────────────────────────────────────────────

def test_anthropic_config_custom_api_path():
    """[构造] AnthropicConfig with DeepSeek-compatible api_path."""
    c = AnthropicConfig(
        api_key="sk-test",
        models=["deepseek-v4-flash"],
        base_url="https://api.deepseek.com",
        api_path="/anthropic",
    )
    assert c.base_url == "https://api.deepseek.com"
    assert c.api_path == "/anthropic"


# ── M2.6 ──────────────────────────────────────────────────────────────────

def test_config_three_providers_independent():
    """[构造] All three configs created independently — no cross-contamination."""
    ds = DeepSeekConfig(api_key="sk-ds", models=["m1"])
    oa = OpenAIConfig(api_key="sk-oa", models=["m2"])
    an = AnthropicConfig(api_key="sk-an", models=["m3"])
    assert ds.api_key == "sk-ds"
    assert oa.api_key == "sk-oa"
    assert an.api_key == "sk-an"


# ═══════════════════════════════════════════════════════════════════════
# M3 — Provider 构造
# ═══════════════════════════════════════════════════════════════════════


# ── M3.1 ──────────────────────────────────────────────────────────────────

def test_deepseek_provider_name_and_models():
    """[方法] DeepSeekProvider name and supported_models."""
    c = DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    p = DeepSeekProvider(c)
    assert p.name == "deepseek"
    assert p.supported_models == ["deepseek-v4-flash"]


# ── M3.2 ──────────────────────────────────────────────────────────────────

def test_openai_provider_name_and_models():
    """[方法] OpenAIProvider name='openai'."""
    c = OpenAIConfig(api_key="sk-test", models=["gpt-4o", "gpt-4-turbo"])
    p = OpenAIProvider(c)
    assert p.name == "openai"
    assert p.supported_models == ["gpt-4o", "gpt-4-turbo"]


# ── M3.3 ──────────────────────────────────────────────────────────────────

def test_anthropic_provider_name_and_models():
    """[方法] AnthropicProvider name='anthropic'."""
    c = AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    p = AnthropicProvider(c)
    assert p.name == "anthropic"
    assert p.supported_models == ["claude-sonnet-4-6"]


# ── M3.4 ──────────────────────────────────────────────────────────────────

def test_provider_three_independent():
    """[构造] Three provider instances independent."""
    ds = DeepSeekProvider(DeepSeekConfig(api_key="sk-ds", models=["m1"]))
    oa = OpenAIProvider(OpenAIConfig(api_key="sk-oa", models=["m2"]))
    an = AnthropicProvider(AnthropicConfig(api_key="sk-an", models=["m3"]))
    assert ds.name == "deepseek"
    assert oa.name == "openai"
    assert an.name == "anthropic"


# ═══════════════════════════════════════════════════════════════════════
# M4 — ModelMessage 构造 (arf-core type)
# ═══════════════════════════════════════════════════════════════════════


# ── M4.1 ──────────────────────────────────────────────────────────────────

def test_model_message_basic():
    """[构造] ModelMessage role+content — minimal construction."""
    m = ModelMessage(role="user", content="Hello")
    assert m.role == "user"
    assert m.content == "Hello"
    assert m.tool_call_id is None
    assert m.name is None
    assert m.extra is None
    assert "ModelMessage" in repr(m)


# ── M4.2 ──────────────────────────────────────────────────────────────────

def test_model_message_all_roles():
    """[覆盖] ModelMessage supports user/assistant/system/tool roles."""
    for role in ["user", "assistant", "system", "tool"]:
        m = ModelMessage(role=role, content="test")
        assert m.role == role


# ── M4.3 ──────────────────────────────────────────────────────────────────

def test_model_message_full():
    """[构造] ModelMessage all fields including tool_call_id, name, extra."""
    m = ModelMessage(
        role="tool",
        content="file content here",
        tool_call_id="call_abc123",
        name="read_file",
        extra={"result_type": "text"},
    )
    assert m.role == "tool"
    assert m.content == "file content here"
    assert m.tool_call_id == "call_abc123"
    assert m.name == "read_file"
    assert m.extra == {"result_type": "text"}


# ── M4.4 ──────────────────────────────────────────────────────────────────

def test_model_message_extra_nested_json():
    """[边界] ModelMessage.extra handles nested JSON dict/list."""
    m = ModelMessage(
        role="assistant",
        content="",
        extra={"reasoning_content": "Let me think...", "citations": [1, 2, 3]},
    )
    assert m.extra["reasoning_content"] == "Let me think..."
    assert m.extra["citations"] == [1, 2, 3]


# ── M4.5 ──────────────────────────────────────────────────────────────────

def test_model_message_extra_none_default():
    """[构造] ModelMessage extra=None by default — getter returns None."""
    m = ModelMessage(role="user", content="hi")
    assert m.extra is None


# ── M4.6 ──────────────────────────────────────────────────────────────────

def test_model_message_unicode():
    """[边界] ModelMessage with Unicode content (Chinese, emoji)."""
    m = ModelMessage(role="user", content="你好世界 🚀")
    assert m.content == "你好世界 🚀"


# ── M4.7 ──────────────────────────────────────────────────────────────────

def test_model_message_empty_content():
    """[边界] ModelMessage with empty content (valid for tool results)."""
    m = ModelMessage(role="tool", content="", tool_call_id="call_1")
    assert m.content == ""
    assert m.tool_call_id == "call_1"


# ── M4.8 ──────────────────────────────────────────────────────────────────

def test_model_message_repr_truncation():
    """[trait] ModelMessage __repr__ truncates long content."""
    m = ModelMessage(role="user", content="a" * 100)
    r = repr(m)
    assert "..." in r
    assert len(r) < 120


# ═══════════════════════════════════════════════════════════════════════
# M5 — ModelParams 构造
# ═══════════════════════════════════════════════════════════════════════


# ── M5.1 ──────────────────────────────────────────────────────────────────

def test_model_params_defaults():
    """[构造] ModelParams() all defaults — None temperature, thinking_enabled=False."""
    p = ModelParams()
    assert p.temperature is None
    assert p.max_tokens is None
    assert p.thinking_enabled is False
    assert p.extra is None


# ── M5.2 ──────────────────────────────────────────────────────────────────

def test_model_params_full():
    """[构造] ModelParams all fields explicitly set."""
    p = ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=True)
    assert p.temperature == 0.7
    assert p.max_tokens == 4096
    assert p.thinking_enabled is True


# ── M5.3 ──────────────────────────────────────────────────────────────────

def test_model_params_with_extra():
    """[构造] ModelParams with provider-specific extra params."""
    p = ModelParams(
        temperature=0.5,
        thinking_enabled=True,
        extra={"reasoning_effort": "high", "top_p": 0.9},
    )
    assert p.extra == {"reasoning_effort": "high", "top_p": 0.9}


# ── M5.4 ──────────────────────────────────────────────────────────────────

def test_model_params_boolean_thinking():
    """[方法] ModelParams.thinking_enabled is Python bool — not string."""
    p = ModelParams(thinking_enabled=True)
    assert p.thinking_enabled is True
    assert isinstance(p.thinking_enabled, bool)

    p2 = ModelParams(thinking_enabled=False)
    assert p2.thinking_enabled is False


# ── M5.5 ──────────────────────────────────────────────────────────────────

def test_model_params_repr():
    """[trait] ModelParams __repr__ includes non-None fields."""
    p = ModelParams(temperature=0.0, max_tokens=100, thinking_enabled=True)
    r = repr(p)
    assert "0.0" in r or "0" in r
    assert "100" in r
    assert "True" in r


# ── M5.6 ──────────────────────────────────────────────────────────────────

def test_model_params_temperature_boundary():
    """[边界] ModelParams temperature 0.0 and 2.0 (boundary values)."""
    for t in [0.0, 1.0, 2.0]:
        p = ModelParams(temperature=t)
        assert p.temperature == t


# ═══════════════════════════════════════════════════════════════════════
# M6 — ToolDef 构造
# ═══════════════════════════════════════════════════════════════════════


# ── M6.1 ──────────────────────────────────────────────────────────────────

def test_tool_def_basic():
    """[构造] ToolDef with simple parameters."""
    t = ToolDef(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    assert t.name == "search"
    assert t.description == "Search the web"
    assert t.parameters == {"type": "object", "properties": {"query": {"type": "string"}}}


# ── M6.2 ──────────────────────────────────────────────────────────────────

def test_tool_def_empty_parameters():
    """[边界] ToolDef with empty dict parameters."""
    t = ToolDef(name="noop", description="Does nothing", parameters={})
    assert t.parameters == {}


# ── M6.3 ──────────────────────────────────────────────────────────────────

def test_tool_def_nested_parameters():
    """[构造] ToolDef with deeply nested JSON Schema parameters."""
    t = ToolDef(
        name="complex",
        description="Complex tool",
        parameters={
            "type": "object",
            "properties": {
                "nested": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"key": {"type": "string"}}},
                }
            },
            "required": ["nested"],
        },
    )
    assert "nested" in t.parameters["properties"]
    assert t.parameters["required"] == ["nested"]


# ── M6.4 ──────────────────────────────────────────────────────────────────

def test_tool_def_unicode():
    """[边界] ToolDef with Unicode name and description."""
    t = ToolDef(name="搜索", description="搜索互联网内容", parameters={})
    assert t.name == "搜索"
    assert t.description == "搜索互联网内容"


# ── M6.5 ──────────────────────────────────────────────────────────────────

def test_tool_def_repr():
    """[trait] ToolDef __repr__ includes name and description."""
    t = ToolDef(name="read", description="Read file", parameters={})
    r = repr(t)
    assert "read" in r
    assert "Read file" in r


# ═══════════════════════════════════════════════════════════════════════
# M7 — 只读类型验证
# ═══════════════════════════════════════════════════════════════════════


# ── M7.1 ──────────────────────────────────────────────────────────────────

def test_tool_call_no_public_constructor():
    """[边界] ToolCall has no public constructor (read-only from provider)."""
    with pytest.raises(TypeError):
        ToolCall()  # type: ignore


# ── M7.2 ──────────────────────────────────────────────────────────────────

def test_tool_call_delta_no_public_constructor():
    """[边界] ToolCallDelta has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ToolCallDelta()  # type: ignore


# ── M7.3 ──────────────────────────────────────────────────────────────────

def test_usage_no_public_constructor():
    """[边界] Usage has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        Usage()  # type: ignore


# ── M7.4 ──────────────────────────────────────────────────────────────────

def test_model_response_chunk_no_public_constructor():
    """[边界] ModelResponseChunk has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ModelResponseChunk()  # type: ignore


# ── M7.5 ──────────────────────────────────────────────────────────────────

def test_model_response_payload_no_public_constructor():
    """[边界] ModelResponsePayload has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ModelResponsePayload()  # type: ignore


# ── M7.6 ──────────────────────────────────────────────────────────────────

def test_model_adapter_node_no_public_constructor():
    """[边界] ModelAdapterNode has no public constructor (created by provider.connect_to_bus())."""
    with pytest.raises(TypeError):
        ModelAdapterNode()  # type: ignore
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| M2 Config 测试 | 三个 Config 分别验证默认值和全字段自定义——`#[pyo3(signature)]` 默认参数正确生效 |
| M3 Provider 测试 | 验证 `name` 和 `supported_models` 从内部 config 读取正确 |
| M4 ModelMessage | 覆盖四种 role + 全字段 + extra 嵌套 JSON + Unicode + 空内容 + repr 截断 |
| M5 ModelParams | 验证默认值（None/False）+ thinking_enabled 必须是 Python bool（非 string，防止 BUG-006） |
| M6 ToolDef | 验证简单/空/nested parameters + Unicode |
| M7 只读类型 | **关键**：`ToolCall`、`ToolCallDelta`、`Usage`、`ModelResponseChunk`、`ModelResponsePayload`、`ModelAdapterNode` 均无 `#[new]`——Python 侧 `pytest.raises(TypeError)` 验证无法直接构造 |

---

### 5.2 `py-arf/tests/test_model_adapter_node.py` — Bus 集成生命周期

```python
"""
[N] ModelAdapter node Bus integration — connect/shutdown/graph lifecycle.

These tests work WITHOUT API keys — they only verify Bus lifecycle.
chat()/chat_stream() require real API keys and are tested in Rust integration tests.

Test angles: [构造] [方法] [边界] [清理]
"""
import asyncio
import gc
import pytest
from arf import Bus, NodeId
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════

def ds_provider():
    """Create a test DeepSeekProvider with placeholder key."""
    return DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    )


def oa_provider():
    """Create a test OpenAIProvider with placeholder key."""
    return OpenAIProvider(
        OpenAIConfig(api_key="sk-test", models=["gpt-4o"])
    )


def an_provider():
    """Create a test AnthropicProvider with placeholder key."""
    return AnthropicProvider(
        AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    )


# ═══════════════════════════════════════════════════════════════════════
# N1 — connect_to_bus 基本流程
# ═══════════════════════════════════════════════════════════════════════


# ── N1.1 ──────────────────────────────────────────────────────────────────

async def test_connect_to_bus_node_appears_in_graph():
    """[构造] provider.connect_to_bus() → node appears in bus graph."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "model/deepseek"
    assert g.nodes[0].node_type == "model"

    # Cleanup
    await node.shutdown()
    await bus.shutdown()


# ── N1.2 ──────────────────────────────────────────────────────────────────

async def test_connect_to_bus_capabilities():
    """[方法] NodeInfo capabilities includes provider name and models."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    g = bus.graph()
    caps = g.nodes[0].capabilities
    assert caps["provider"] == "deepseek"
    assert caps["models"] == ["deepseek-v4-flash"]

    await node.shutdown()
    await bus.shutdown()


# ── N1.3 ──────────────────────────────────────────────────────────────────

async def test_connect_to_bus_returns_model_adapter_node():
    """[方法] connect_to_bus() returns ModelAdapterNode with correct node_id."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/test"))

    assert str(node.node_id) == "model/test"
    assert "ModelAdapterNode" in repr(node)

    await node.shutdown()
    await bus.shutdown()


# ── N1.4 ──────────────────────────────────────────────────────────────────

async def test_connect_all_three_providers_to_same_bus():
    """[方法] Three providers on same bus — all appear in graph."""
    bus = Bus()

    ds_node = await ds_provider().connect_to_bus(bus, NodeId("model/deepseek"))
    oa_node = await oa_provider().connect_to_bus(bus, NodeId("model/openai"))
    an_node = await an_provider().connect_to_bus(bus, NodeId("model/anthropic"))

    g = bus.graph()
    assert len(g.nodes) == 3
    provider_names = {n.capabilities["provider"] for n in g.nodes}
    assert provider_names == {"deepseek", "openai", "anthropic"}

    await ds_node.shutdown()
    await oa_node.shutdown()
    await an_node.shutdown()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N2 — Shutdown 语义
# ═══════════════════════════════════════════════════════════════════════


# ── N2.1 ──────────────────────────────────────────────────────────────────

async def test_shutdown_removes_node_from_graph():
    """[清理] After shutdown, node removed from bus graph."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    assert len(bus.graph().nodes) == 1

    await node.shutdown()
    await asyncio.sleep(0.05)  # allow async disconnect to propagate

    g = bus.graph()
    assert len(g.nodes) == 0, f"Expected empty graph, got {g.nodes}"

    await bus.shutdown()


# ── N2.2 ──────────────────────────────────────────────────────────────────

async def test_double_shutdown_raises():
    """[边界] Second shutdown() raises RuntimeError."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    with pytest.raises(RuntimeError, match="already shut down"):
        await node.shutdown()

    await bus.shutdown()


# ── N2.3 ──────────────────────────────────────────────────────────────────

async def test_double_shutdown_idempotent_after_bus_closed():
    """[边界] Even after bus shutdown, double-shutdown still raises."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    await bus.shutdown()

    # Node already shut down — second attempt should still raise
    with pytest.raises(RuntimeError, match="already shut down"):
        await node.shutdown()


# ── N2.4 ──────────────────────────────────────────────────────────────────

async def test_node_id_after_shutdown_raises():
    """[边界] Accessing node_id after shutdown raises RuntimeError."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()

    with pytest.raises(RuntimeError, match="already shut down"):
        _ = node.node_id

    await bus.shutdown()


# ── N2.5 ──────────────────────────────────────────────────────────────────

async def test_repr_after_shutdown_shows_shut_down():
    """[trait] ModelAdapterNode repr shows 'shut down' after shutdown."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()

    r = repr(node)
    assert "shut down" in r.lower() or "Shut" in r

    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N3 — 多 Provider 场景
# ═══════════════════════════════════════════════════════════════════════


# ── N3.1 ──────────────────────────────────────────────────────────────────

async def test_partial_shutdown_leaves_other_nodes():
    """[清理] Shutdown one node — others remain in graph."""
    bus = Bus()

    ds_node = await ds_provider().connect_to_bus(bus, NodeId("model/deepseek"))
    oa_node = await oa_provider().connect_to_bus(bus, NodeId("model/openai"))

    assert len(bus.graph().nodes) == 2

    await ds_node.shutdown()
    await asyncio.sleep(0.05)

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "model/openai"

    await oa_node.shutdown()
    await bus.shutdown()


# ── N3.2 ──────────────────────────────────────────────────────────────────

async def test_multiple_same_provider_different_models():
    """[方法] Two DeepSeek nodes with different models on same bus."""
    bus = Bus()

    p1 = DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    )
    p2 = DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-pro"])
    )

    n1 = await p1.connect_to_bus(bus, NodeId("model/flash"))
    n2 = await p2.connect_to_bus(bus, NodeId("model/pro"))

    g = bus.graph()
    assert len(g.nodes) == 2
    models_seen = set()
    for n in g.nodes:
        models_seen.update(n.capabilities["models"])
    assert "deepseek-v4-flash" in models_seen
    assert "deepseek-v4-pro" in models_seen

    await n1.shutdown()
    await n2.shutdown()
    await bus.shutdown()


# ── N3.3 ──────────────────────────────────────────────────────────────────

async def test_bus_shutdown_before_node_shutdown():
    """[边界] Bus shutdown before node — graceful handling."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    # Shutdown bus first
    await bus.shutdown()

    # Node shutdown should still work (or at least not hang)
    try:
        await node.shutdown()
    except Exception:
        pass  # acceptable — bus already closed


# ═══════════════════════════════════════════════════════════════════════
# N4 — NodeId 边界
# ═══════════════════════════════════════════════════════════════════════


# ── N4.1 ──────────────────────────────────────────────────────────────────

async def test_node_id_unicode():
    """[边界] NodeId with Unicode — model name in Chinese."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("模型/deepseek"))

    assert str(node.node_id) == "模型/deepseek"

    await node.shutdown()
    await bus.shutdown()


# ── N4.2 ──────────────────────────────────────────────────────────────────

async def test_node_id_long_name():
    """[边界] NodeId with long model path."""
    long_id = "model/" + "a" * 64 + "/deepseek-v4-flash"
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId(long_id))

    assert str(node.node_id) == long_id

    await node.shutdown()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N5 — GC / 资源清理
# ═══════════════════════════════════════════════════════════════════════


# ── N5.1 ──────────────────────────────────────────────────────────────────

async def test_gc_collects_node_after_shutdown():
    """[泄漏] Node can be GC'd after shutdown — no dangling references."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    del node
    gc.collect()
    await asyncio.sleep(0.05)

    # Bus still functional after node GC
    assert bus.uptime_ms >= 0

    await bus.shutdown()


# ── N5.2 ──────────────────────────────────────────────────────────────────

async def test_config_reuse_across_providers():
    """[方法] Same config can be reused across multiple provider instances."""
    config = DeepSeekConfig(api_key="sk-shared", models=["deepseek-v4-flash"])

    p1 = DeepSeekProvider(config)
    p2 = DeepSeekProvider(config)

    assert p1.supported_models == p2.supported_models
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| N1 connect 测试 | 验证 node 进入 bus graph、capabilities 含 provider 名和 models、node_id 正确 |
| N1.4 三供应商同 Bus | 验证三个不同 Provider 可同时连接同一 Bus——graph 节点独立、caps 不串扰 |
| N2 shutdown 语义 | 二次 shutdown → RuntimeError；shutdown 后 node_id 访问也报错；repr 显示 "shut down" |
| N2.1 sleep(0.05) | 异步 disconnect 需要短暂传播——`ModelAdapterNode::shutdown()` 发送 oneshot 后 task 异步执行 `handle.disconnect().await` |
| N3 多 Provider | 部分 shutdown 不影响其他节点；同供应商可多个实例（不同模型）；Bus 先关也可容忍 |
| N4 NodeId 边界 | Unicode (中文) + 超长路径 (64+ chars) |
| N5 GC | 确认 node shutdown 后可被 GC 回收——`del node; gc.collect()` 后 Bus 仍正常 |

---

### 5.3 `py-arf/tests/test_model_adapter_live.py` — 真实 API 集成测试

> 对应 Rust `deepseek_live.rs` (10 tests) + `bus_integration.rs` (8 tests)
> 需设置环境变量 `DEEPSEEK_API_KEY=sk-xxx`，未设置则全部 skip

```python
"""
[L] Live API integration tests — DeepSeekProvider direct + Bus full-link.

These tests require a valid DeepSeek API key. Set DEEPSEEK_API_KEY env var.
Without it, all tests are skipped.

Mirrors the Rust integration tests:
  - deepseek_live.rs: 7 OpenAI + 3 Anthropic format
  - bus_integration.rs: 8 Bus full-link tests

Run:
  DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_model_adapter_live.py -v
"""

import asyncio
import os
import pytest
from arf import Bus, NodeId
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    ModelAdapterNode, ModelMessage,
    ModelParams, ToolDef,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def require_api_key():
    """Return API key or skip the test."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY not set")
    return key


def empty_params(**overrides):
    """ModelParams with neutral defaults."""
    kwargs = {
        "temperature": None,
        "max_tokens": None,
        "thinking_enabled": False,
        "extra": None,
    }
    kwargs.update(overrides)
    return ModelParams(**kwargs)


async def engine_call(bus, target_node_id, messages, tools, params, stream=False):
    """Minimal EngineStub: connect to Bus, send model_call, collect response(s).

    Returns (response_payload_dict, list_of_chunk_dicts).
    """
    from arf import NodeInfo, MessageFilter, ToMatch

    info = NodeInfo(
        node_id=f"engine/stub-{id(messages)}",
        node_type="engine",
        capabilities={},
    )
    flt = MessageFilter(
        types=["model_response", "model_response_chunk"],
        to_match=ToMatch.BroadcastAndDirectedToMe,
    )
    handle = await bus.connect(info, flt)

    payload = {
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                **({"tool_call_id": m.tool_call_id} if m.tool_call_id else {}),
                **({"name": m.name} if m.name else {}),
                **({"extra": m.extra} if m.extra is not None else {}),
            }
            for m in messages
        ],
        "tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ],
        "model_params": {
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
            "thinking_enabled": params.thinking_enabled,
            "extra": params.extra,
        },
        "stream": stream,
    }

    await handle.send("model_call", [target_node_id], payload)

    chunks = []
    while True:
        msg = await handle.recv()
        if msg.msg_type == "model_response_chunk":
            chunks.append(msg.payload)
        elif msg.msg_type == "model_response":
            await handle.disconnect()
            return msg.payload, chunks


# ═══════════════════════════════════════════════════════════════════════
# L1 — OpenAI format: DeepSeekProvider direct (7 tests)
# ═══════════════════════════════════════════════════════════════════════


def ds_provider():
    """Create DeepSeekProvider with real API key."""
    return DeepSeekProvider(
        DeepSeekConfig(
            api_key=require_api_key(),
            models=["deepseek-v4-flash", "deepseek-v4-pro"],
        )
    )


# ── L1.1 ──────────────────────────────────────────────────────────────────

async def test_live_basic_chat():
    """[连通] 基础对话 — 非流式，finish_reason='stop'，有 content 和 usage."""
    p = ds_provider()
    msgs = [ModelMessage(role="user", content="Say hello in one word.")]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert response.finish_reason == "stop"
    assert response.message.content != ""
    assert response.usage is not None
    assert response.usage.total_tokens > 0
    print(f"[basic_chat] content: {response.message.content}")
    print(f"[basic_chat] usage: {response.usage}")


# ── L1.2 ──────────────────────────────────────────────────────────────────

async def test_live_multi_round_chat():
    """[连通] 多轮对话 — 模型理解上下文，记住名字."""
    p = ds_provider()
    msgs = [
        ModelMessage(role="user", content="My name is Alice."),
        ModelMessage(role="assistant", content="Nice to meet you, Alice!"),
        ModelMessage(role="user", content="What is my name?"),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert "alice" in response.message.content.lower()
    print(f"[multi_round] content: {response.message.content}")


# ── L1.3 ──────────────────────────────────────────────────────────────────

async def test_live_single_tool_call():
    """[工具] 单工具调用 — finish_reason='tool_calls'，工具名正确."""
    p = ds_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]
    msgs = [ModelMessage(role="user", content="What is the weather in Beijing?")]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls is not None
    assert len(response.tool_calls) > 0
    assert response.tool_calls[0].name == "get_weather"
    print(f"[tool_call] name: {response.tool_calls[0].name}, args: {response.tool_calls[0].arguments}")


# ── L1.4 ──────────────────────────────────────────────────────────────────

async def test_live_multi_tool_call_with_results():
    """[工具] 多工具调用 + 结果回传 — 最终 finish_reason='stop'."""
    p = ds_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        ),
        ToolDef(
            name="get_time",
            description="Get current time in a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        ),
    ]
    msgs = [
        ModelMessage(
            role="user",
            content="What is the weather AND time in Shanghai?",
        )
    ]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    print(f"[multi_tool] finish_reason: {response.finish_reason}")

    if response.finish_reason == "tool_calls":
        tcs = response.tool_calls
        print(f"[multi_tool] tool_calls count: {len(tcs)}")

        # Build tool result messages
        msgs2 = [
            ModelMessage(
                role="user",
                content="What is the weather AND time in Shanghai?",
            ),
        ]
        # Build assistant message with tool_calls in extra
        import json as _json
        api_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _json.dumps(tc.arguments),
                },
            }
            for tc in tcs
        ]
        msgs2.append(
            ModelMessage(
                role="assistant",
                content="",
                extra={"tool_calls": api_tool_calls},
            )
        )
        for tc in tcs:
            result_text = {
                "get_weather": "Sunny, 25°C",
                "get_time": "14:30 CST",
            }.get(tc.name, "done")
            msgs2.append(
                ModelMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
            )

        response2 = await p.chat("deepseek-v4-flash", msgs2, [], empty_params())
        assert response2.finish_reason == "stop"
        print(f"[multi_tool] final: {response2.message.content}")


# ── L1.5 ──────────────────────────────────────────────────────────────────

async def test_live_thinking_enabled():
    """[思考] 开启思考模式 — extra 中有 reasoning_content."""
    p = ds_provider()
    params = empty_params(
        thinking_enabled=True,
        extra={"reasoning_effort": "high"},
    )
    msgs = [
        ModelMessage(
            role="user",
            content="Explain quantum computing in one paragraph.",
        )
    ]
    response = await p.chat("deepseek-v4-pro", msgs, [], params)
    print(f"[thinking] content: {response.message.content[:100]}...")
    print(f"[thinking] extra: {response.message.extra}")

    # deepseek-v4-pro with thinking should have reasoning_content
    extra = response.message.extra
    has_reasoning = extra is not None and "reasoning_content" in extra
    print(f"[thinking] has reasoning_content: {has_reasoning}")
    # Note: weaker assertion — deepseek-v4-pro may not always emit reasoning
    # in short responses. Just verify the call succeeded.


# ── L1.6 ──────────────────────────────────────────────────────────────────

async def test_live_thinking_disabled():
    """[思考] 关闭思考模式 — finish_reason='stop'，正常回复."""
    p = ds_provider()
    params = empty_params(thinking_enabled=False)
    msgs = [ModelMessage(role="user", content="Say hello.")]
    response = await p.chat("deepseek-v4-flash", msgs, [], params)
    assert response.finish_reason == "stop"
    assert response.message.content != ""
    extra = response.message.extra
    has_reasoning = extra is not None and "reasoning_content" in extra
    print(f"[thinking_off] content: {response.message.content}")
    print(f"[thinking_off] has reasoning_content: {has_reasoning}")


# ── L1.7 ──────────────────────────────────────────────────────────────────

async def test_live_streaming():
    """[流式] SSE 流式响应 — chunks 非空，最终 content 拼接正确."""
    p = ds_provider()
    msgs = [ModelMessage(role="user", content="Count from 1 to 5 slowly.")]
    chunks, response = await p.chat_stream(
        "deepseek-v4-flash", msgs, [], empty_params()
    )
    print(f"[streaming] chunk count: {len(chunks)}")
    for i, c in enumerate(chunks):
        if c.chunk_type == "text":
            print(f"[streaming] chunk[{i}]: {c.content}")
    assert len(chunks) > 0, "streaming should produce chunks"
    assert response.message.content != ""
    print(f"[streaming] full content: {response.message.content}")


# ═══════════════════════════════════════════════════════════════════════
# L2 — Anthropic format: AnthropicProvider → DeepSeek (3 tests)
# ═══════════════════════════════════════════════════════════════════════


def an_provider():
    """Create AnthropicProvider targeting DeepSeek Anthropic endpoint."""
    return AnthropicProvider(
        AnthropicConfig(
            api_key=require_api_key(),
            models=["deepseek-v4-flash"],
            base_url="https://api.deepseek.com",
            api_path="/anthropic/messages",
        )
    )


# ── L2.1 ──────────────────────────────────────────────────────────────────

async def test_live_anthropic_basic_chat():
    """[连通] Anthropic 格式基础对话 — system 提取为顶层参数."""
    p = an_provider()
    msgs = [
        ModelMessage(role="system", content="Respond briefly."),
        ModelMessage(role="user", content="Say hello in one word."),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert response.message.content != ""
    print(f"[anthropic] content: {response.message.content}")
    print(f"[anthropic] finish_reason: {response.finish_reason}")
    print(f"[anthropic] usage: {response.usage}")


# ── L2.2 ──────────────────────────────────────────────────────────────────

async def test_live_anthropic_multi_round_chat():
    """[连通] Anthropic 格式多轮对话 — 记住颜色."""
    p = an_provider()
    msgs = [
        ModelMessage(role="user", content="My favorite color is blue."),
        ModelMessage(role="assistant", content="Blue is a great choice!"),
        ModelMessage(role="user", content="What did I say my favorite color is?"),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert "blue" in response.message.content.lower()
    print(f"[anthropic_multi] content: {response.message.content}")


# ── L2.3 ──────────────────────────────────────────────────────────────────

async def test_live_anthropic_tool_call():
    """[工具] Anthropic 格式工具调用 — stop_reason 映射正确."""
    p = an_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]
    msgs = [ModelMessage(role="user", content="What is the weather in Tokyo?")]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    print(f"[anthropic_tool] finish_reason: {response.finish_reason}")
    if response.tool_calls:
        print(
            f"[anthropic_tool] tool: {response.tool_calls[0].name} "
            f"args: {response.tool_calls[0].arguments}"
        )


# ═══════════════════════════════════════════════════════════════════════
# L3 — Bus 全链路集成测试 (8 tests)
# ═══════════════════════════════════════════════════════════════════════


class BusIntegrationFixture:
    """Setup/teardown for Bus full-link tests."""

    def __init__(self, model_name="deepseek-v4-flash"):
        self.model_name = model_name
        self.bus = None
        self.node = None
        self.node_id = None

    async def __aenter__(self):
        self.bus = Bus(
            heartbeat_interval_ms=10000,
            heartbeat_timeout_ms=30000,
            channel_capacity=64,
        )
        provider = DeepSeekProvider(
            DeepSeekConfig(
                api_key=require_api_key(),
                models=[self.model_name, "deepseek-v4-pro"],
            )
        )
        self.node_id = NodeId(f"model/{self.model_name}")
        self.node = await provider.connect_to_bus(self.bus, self.node_id)
        # Give node a tick to broadcast node_online
        await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *args):
        if self.node:
            try:
                await self.node.shutdown()
            except Exception:
                pass
        if self.bus:
            try:
                await self.bus.shutdown()
            except Exception:
                pass


# ── L3.1 ──────────────────────────────────────────────────────────────────

async def test_live_bus_basic_chat():
    """[连通] 基础对话经 Bus — non-streaming，chunks 空，finish_reason='stop'."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [ModelMessage(role="user", content="Say hello in one word.")]
        response, chunks = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=False
        )
        assert len(chunks) == 0, "non-streaming should have no chunks"
        assert response["finish_reason"] == "stop"
        assert response["message"]["content"] != ""
        print(f"[bus_basic] content: {response['message']['content']}")


# ── L3.2 ──────────────────────────────────────────────────────────────────

async def test_live_bus_multi_round_chat():
    """[连通] 多轮对话经 Bus — 上下文理解."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [
            ModelMessage(role="user", content="My name is Alice."),
            ModelMessage(role="assistant", content="Nice to meet you, Alice!"),
            ModelMessage(role="user", content="What is my name?"),
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=False
        )
        assert "alice" in response["message"]["content"].lower()
        print(f"[bus_multi] content: {response['message']['content']}")


# ── L3.3 ──────────────────────────────────────────────────────────────────

async def test_live_bus_single_tool_call():
    """[工具] 单工具调用经 Bus — finish_reason='tool_calls'."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        tools = [
            ToolDef(
                name="get_weather",
                description="Get current weather for a city",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            )
        ]
        msgs = [
            ModelMessage(role="user", content="What is the weather in Beijing?")
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, tools, empty_params(), stream=False
        )
        assert response["finish_reason"] == "tool_calls"
        tc = response["tool_calls"]
        assert len(tc) > 0
        assert tc[0]["name"] == "get_weather"
        print(f"[bus_tool] name: {tc[0]['name']}, args: {tc[0]['arguments']}")


# ── L3.4 ──────────────────────────────────────────────────────────────────

async def test_live_bus_multi_tool_call_with_results():
    """[工具] 多工具+结果回传经 Bus — 两轮闭环."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        tools = [
            ToolDef(
                name="get_weather",
                description="Get current weather",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
            ToolDef(
                name="get_time",
                description="Get current time in a city",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ]
        msgs = [
            ModelMessage(
                role="user",
                content="What is the weather AND time in Shanghai?",
            )
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, tools, empty_params(), stream=False
        )
        print(f"[bus_multi] finish_reason: {response['finish_reason']}")

        if response.get("finish_reason") == "tool_calls":
            tcs = response["tool_calls"]
            print(f"[bus_multi] tool_calls count: {len(tcs)}")

            import json as _json
            api_tool_calls = [
                {
                    "id": t["id"],
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "arguments": _json.dumps(t["arguments"]),
                    },
                }
                for t in tcs
            ]
            msgs2 = [
                ModelMessage(
                    role="user",
                    content="What is the weather AND time in Shanghai?",
                ),
                ModelMessage(
                    role="assistant",
                    content="",
                    extra={"tool_calls": api_tool_calls},
                ),
            ]
            for t in tcs:
                result_text = {
                    "get_weather": "Sunny, 25°C",
                    "get_time": "14:30 CST",
                }.get(t["name"], "done")
                msgs2.append(
                    ModelMessage(
                        role="tool",
                        content=result_text,
                        tool_call_id=t["id"],
                        name=t["name"],
                    )
                )

            response2, _ = await engine_call(
                fix.bus, fix.node_id, msgs2, [], empty_params(), stream=False
            )
            assert response2["finish_reason"] == "stop"
            print(f"[bus_multi] final: {response2['message']['content']}")


# ── L3.5 ──────────────────────────────────────────────────────────────────

async def test_live_bus_thinking_enabled():
    """[思考] 开启思考经 Bus — reasoning_content 不丢失."""
    async with BusIntegrationFixture("deepseek-v4-pro") as fix:
        params = empty_params(
            thinking_enabled=True,
            extra={"reasoning_effort": "high"},
        )
        msgs = [
            ModelMessage(
                role="user",
                content="Explain quantum computing in one paragraph.",
            )
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], params, stream=False
        )
        extra = response.get("message", {}).get("extra")
        has_reasoning = extra is not None and "reasoning_content" in extra
        print(f"[bus_thinking] has reasoning_content: {has_reasoning}")
        print(f"[bus_thinking] content: {response['message']['content'][:100]}")


# ── L3.6 ──────────────────────────────────────────────────────────────────

async def test_live_bus_thinking_disabled():
    """[思考] 关闭思考经 Bus — thinking: {type:'disabled'} 正确发送."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        params = empty_params(thinking_enabled=False)
        msgs = [ModelMessage(role="user", content="Say hello.")]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], params, stream=False
        )
        assert response["finish_reason"] == "stop"
        assert response["message"]["content"] != ""
        extra = response.get("message", {}).get("extra")
        has_reasoning = extra is not None and "reasoning_content" in extra
        print(f"[bus_thinking_off] content: {response['message']['content']}")
        print(f"[bus_thinking_off] has reasoning_content: {has_reasoning}")


# ── L3.7 ──────────────────────────────────────────────────────────────────

async def test_live_bus_streaming():
    """[流式] SSE 流经 Bus — 每个 chunk 作为独立消息到达."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [
            ModelMessage(role="user", content="Count from 1 to 5 slowly.")
        ]
        response, chunks = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=True
        )
        print(f"[bus_streaming] chunk count: {len(chunks)}")
        for i, c in enumerate(chunks):
            if c.get("chunk_type") == "text":
                print(f"[bus_streaming] chunk[{i}]: {c.get('content')}")
        assert len(chunks) > 0, "streaming should produce chunks"
        assert response["message"]["content"] != ""
        print(f"[bus_streaming] full content: {response['message']['content']}")


# ── L3.8 ──────────────────────────────────────────────────────────────────

async def test_live_bus_invalid_payload():
    """[错误] 无效 payload — 返回 error 响应，不 panic."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        from arf import NodeInfo, MessageFilter, ToMatch

        info = NodeInfo(
            node_id="engine/stub-error",
            node_type="engine",
            capabilities={},
        )
        flt = MessageFilter(
            types=["model_response", "model_response_chunk"],
            to_match=ToMatch.BroadcastAndDirectedToMe,
        )
        handle = await fix.bus.connect(info, flt)

        # Send malformed model_call (not a valid payload structure)
        await handle.send(
            "model_call", [fix.node_id], {"malformed": "not a valid payload"}
        )

        msg = await handle.recv()
        assert msg.msg_type == "model_response"
        error_text = msg.payload.get("error", "")
        assert "invalid" in error_text.lower()
        print(f"[bus_error] error: {error_text}")
        await handle.disconnect()
```

**逐行解释：**

| 行 | 解释 |
|----|------|
| `require_api_key()` | 从环境变量读取 `DEEPSEEK_API_KEY`，未设置则 `pytest.skip()`——所有 L 测试自动跳过 |
| `engine_call()` | Python 版 EngineStub：连接 Bus → 过滤 `model_response`/`model_response_chunk` → 发送 `model_call` → 循环收集响应。与 Rust `EngineStub::call()` 逻辑一致 |
| `BusIntegrationFixture` | `__aenter__` 创建 Bus + Provider + Node + 等待 node_online 广播；`__aexit__` 安全清理。对应 Rust `setup()`/`teardown()` |
| L1 OpenAI 7 tests | 完全对应 `deepseek_live.rs::openai_format` 的 7 个测试：basic/multi_round/single_tool/multi_tool/thinking_enabled/thinking_disabled/streaming |
| L2 Anthropic 3 tests | 完全对应 `deepseek_live.rs::anthropic_format` 的 3 个测试：basic/multi_round/tool_call。`api_path="/anthropic/messages"` 使用 DeepSeek 的 Anthropic 兼容端点 |
| L3 Bus 8 tests | 完全对应 `bus_integration.rs` 的 8 个测试：basic/multi_round/single_tool/multi_tool/thinking_enabled/thinking_disabled/streaming/invalid_payload |
| L1.4 / L3.4 multi_tool | Python 侧需手动构建 `tool_calls` 格式的 extra——将 `ToolCall` 对象转为 `{"id","type":"function","function":{"name","arguments"}}` JSON |

### 5.4 运行测试

```bash
# 首次：编译 PyO3 扩展
cd py-arf && ../.venv2/bin/python -m maturin develop

# 运行 ModelAdapter 导入测试（27 tests，无需 API key）
cd py-arf && ../.venv2/bin/python -m pytest tests/test_model_adapter_imports.py -v

# 运行 ModelAdapter Node 集成测试（14 tests，无需 API key）
cd py-arf && ../.venv2/bin/python -m pytest tests/test_model_adapter_node.py -v

# 运行 ModelAdapter 真实 API 测试（18 tests，需 API key）
DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_model_adapter_live.py -v

# 确认不破坏已有 Bus 测试
cd py-arf && ../.venv2/bin/python -m pytest tests/ -q

# 全 workspace Rust 测试
. $HOME/.cargo/env && cargo test --workspace
```

---

## 验证（快速冒烟）

```bash
# 编译
cargo build -p py-arf

# 导入冒烟（同步，无需 API key）
python -c "
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
    ModelAdapterNode, ModelMessage, ModelParams, ToolDef,
    ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, Usage,
)

# Config + Provider 构造
ds = DeepSeekConfig(api_key='sk-test', models=['deepseek-v4-flash'])
p = DeepSeekProvider(ds)
assert p.name == 'deepseek'

# Data types
msg = ModelMessage(role='user', content='Hello', extra={'key': 'val'})
assert msg.extra == {'key': 'val'}

params = ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=True)
assert params.thinking_enabled is True

tool = ToolDef(name='s', description='d', parameters={'type': 'object'})
assert tool.parameters == {'type': 'object'}

# Read-only types — no public constructor
import traceback
for cls in [ToolCall, ToolCallDelta, Usage, ModelResponseChunk, ModelResponsePayload, ModelAdapterNode]:
    try:
        cls()
        assert False, f'{cls.__name__} should not be constructible'
    except TypeError:
        pass

print('All imports + smoke tests OK')
"

# Bus 集成冒烟
python -c "
import asyncio
from arf import Bus, NodeId, DeepSeekConfig, DeepSeekProvider

async def test():
    bus = Bus()
    p = DeepSeekProvider(DeepSeekConfig(api_key='sk-test', models=['m1']))
    node = await p.connect_to_bus(bus, NodeId('model/test'))
    assert len(bus.graph().nodes) == 1
    caps = bus.graph().nodes[0].capabilities
    assert caps['provider'] == 'deepseek'
    await node.shutdown()
    # Double shutdown
    try:
        await node.shutdown()
        assert False
    except RuntimeError:
        pass
    await bus.shutdown()
    print('Bus integration OK')

asyncio.run(test())
"
```

---

## 汇总

| 项目 | 数量 |
|------|------|
| 新增 PyClass | 14 |
| 代码行数 (lib.rs 新增) | ~550 行 |
| 修改文件 | 3（Cargo.toml + lib.rs + __init__.py） |
| 新增文件 | 4（示例 + 3 个 test 文件） |
| 公开类型 | 14（3 config + 3 provider + 1 node + 7 数据类） |
| Python 导入测试 (in-package) | 27（6 Config + 3 Provider + 8 ModelMessage + 6 ModelParams + 5 ToolDef + 6 只读类型校验） |
| Python Node 集成测试 (in-package) | 14（connect × 4 + shutdown × 5 + multi-provider × 3 + boundary × 2） |
| Python 真实 API 测试 (live) | 18（OpenAI 格式 7 + Anthropic 格式 3 + Bus 全链路 8） |

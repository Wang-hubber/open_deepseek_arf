# 任务 4.5：Anthropic provider 实现

> Phase 4 — ModelAdapter 第五项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：4.1（类型）+ 4.2（Provider trait）+ 4.4（OpenAI 基准），已完成

## 设计思路

`AnthropicProvider` 是 **Anthropic Messages API 的基准实现**。与 OpenAI 格式的关键差异：

| | OpenAI | Anthropic |
|--|--------|-----------|
| 端点 | `/v1/chat/completions` | `/v1/messages` |
| 认证 | `Authorization: Bearer` | `x-api-key` + `anthropic-version` |
| system | `messages[0]` (role=system) | 顶层 `system` 参数 |
| max_tokens | 可选 | **必填** |
| content | 纯字符串 | `[{type: "text", text: "..."}]` 数组 |
| 工具格式 | `{type:"function", function:{...}}` | `{name, description, input_schema}` |
| 工具结果 role | `"tool"` | `"user"` + `{type:"tool_result"}` |
| stop 字段 | `finish_reason` | `stop_reason` |
| 流式 | SSE `data:` 行 | SSE `event:` + `data:` 行 |

Provider trait 接口不变——所有差异封装在 `AnthropicProvider` 内部。

## 消息转换对照

### ARF → Anthropic（请求方向）

| ARF `ModelMessage` | Anthropic API |
|-------------------|---------------|
| `role: "system"`（第一条） | 顶层 `system: "..."` 参数 |
| `role: "system"`（后续，罕见） | `role: "user"`, content: `[{type:"text", text:"<system>...</system>"}]` |
| `role: "user"` | `{role: "user", content: [{type: "text", text: "..."}]}` |
| `role: "assistant"` + text | `{role: "assistant", content: [{type: "text", text: "..."}]}` |
| `role: "assistant"` + tool_calls | `{role: "assistant", content: [{type: "tool_use", id, name, input}]}` |
| `role: "tool"` + result | `{role: "user", content: [{type: "tool_result", tool_use_id, content: "..."}]}` |

### Anthropic → ARF（响应方向）

| Anthropic API | ARF |
|--------------|-----|
| `content[{type:"text", text:"..."}]` | 拼接所有 text block → `message.content` |
| `content[{type:"tool_use", id, name, input}]` | `ModelResponsePayload.tool_calls` |
| `stop_reason: "end_turn"` | `finish_reason: "stop"` |
| `stop_reason: "max_tokens"` | `finish_reason: "length"` |
| `stop_reason: "stop_sequence"` | `finish_reason: "stop"` |
| `stop_reason: "tool_use"` | `finish_reason: "tool_calls"` |
| `usage: {input_tokens, output_tokens}` | `Usage`（total = input + output） |

## 代码实现

### `crates/arf-model-adapter/src/anthropic.rs`（新文件）

完整实现见代码。关键点：

**认证：**
```rust
.header("x-api-key", &self.config.api_key)
.header("anthropic-version", "2023-06-01")
```

**system 提取：**
遍历 messages，找到第一个 `role="system"` 的消息，其 content 作为顶层 `system` 参数传入。后续 system 消息转为 user message。

**max_tokens 必填处理：**
```rust
let max_tokens = params.max_tokens.unwrap_or(4096); // Anthropic requires this
```

**content blocks 构建：**
所有 user/assistant 消息的 content 都包装为 `[{type: "text", text: "..."}]`。tool 角色结果包装为 `[{type: "tool_result", tool_use_id, content}]` 并改 role 为 user。

**响应解析：**
- `content[]` 中 `type="text"` 的 block 拼接为最终 content
- `content[]` 中 `type="tool_use"` 的 block 转为 `ToolCall`
- `stop_reason` 映射为 ARF 标准 `finish_reason`
- `usage.total_tokens` 由 `input_tokens + output_tokens` 计算（Anthropic 不返回此字段）

**流式：**
Anthropic SSE 使用 `event:` + `data:` 双行格式。event 类型有 `message_start`、`content_block_start`、`content_block_delta`、`content_block_stop`、`message_delta`、`message_stop`。

## 测试

### anthropic.rs — 12 tests

| 分类 | 测试数 | 覆盖 |
|------|--------|------|
| 消息转换 | 6 | user/assistant/tool/system提取/text content/tool_use |
| 请求体 | 3 | 最简/含工具/system参数 |
| 响应解析 | 2 | 文本/stop_reason映射 |
| 流式解析 | 1 | SSE event解析 |
| **新增** | **12** | |
| **累计** (4.1–4.5) | **57** | |

---

## 交付标准

- `cargo test --workspace` 全部通过（283 + 12 = 295 tests）
- `cargo clippy` 无警告
- system 提取正确
- content blocks 转换正确
- stop_reason 映射正确
- SSE 流式解析正确

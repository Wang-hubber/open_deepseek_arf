# Memory Pipeline — Design & Implementation

ARF 的记忆系统遵循 OS 文件系统模式：**协议定义接口，默认实现提供完整能力，应用层无需重复造轮子**。

## Architecture

```
用户消息
    │
    ▼
GraphEngine.invoke() / astream()
    │
    ├─ [1] MemoryRetriever.retrieve()           ← 每 turn 开始前
    │      从 memory.json 加载 → 注入 system prompt (## Memory)
    │
    ├─ [2] Model 生成响应
    │      模型在 system prompt 中看到记忆上下文
    │      不需要显式调用 memory_store 工具（框架自动处理）
    │
    └─ [3] MemoryWriter.extract_and_write()     ← 每 turn 结束后
           自动从对话中提取事实/偏好/决策 → 写入 memory.json
```

## 协议层

三个核心协议定义在 `arf/core/protocols/memory.py`：

| 协议 | 职责 | 方法 |
|------|------|------|
| `MemoryStore` | 持久化存储 | `save(entry)`, `load(session_id)`, `delete(entry_id)` |
| `MemoryRetriever` | 语义检索 | `retrieve(store, query, session_id, max_tokens, top_k)` |
| `MemoryWriter` | 自动抽取 | `extract_and_write(store, turn_messages, existing_entries)` |

### MemoryEntry 数据结构

```python
@dataclass
class MemoryEntry:
    id: str              # UUID
    content: str         # 记忆内容
    category: str        # fact | preference | decision | context
    timestamp: float     # Unix 时间戳
    source_turn: int     # 来源 turn
    relevance_score: float = 1.0
    replaces: str | None = None  # 替换的旧条目 ID
```

## 默认实现

### FileMemoryStore (`arf/memory/file_store.py`)

JSON 文件后端，所有条目序列化到 `memory/memory.json`。

- **去重**：按 `id` 覆盖（同 ID 写入替换旧条目）
- **并发**：单文件读写，适用于单用户场景
- **迁移**：旧 `.md` 格式数据可通过 `scripts/migrate_memory_md_to_json.py` 迁移

### LLMMemoryWriter (`arf/memory/llm_writer.py`)

每 turn 结束后调用廉价模型（`deepseek-v4-flash`, temp=0.3, thinking disabled）从最近 4 条消息中自动提取结构化记忆。

- **输入**：最近 turn 消息 + 已有记忆索引
- **输出**：JSON actions — `add`（新增）、`update`（更新，带 `replaces`）、`delete`（删除）
- **去重**：LLM 根据已有记忆索引判断是否新增/更新/删除
- **容错**：JSON 解析失败时跳过该 turn，不影响主流程；支持 markdown 围栏、双花括号等常见 LLM 输出格式
- **日志**：INFO 级别记录每次抽取的 action 数和条目变化（`logger = arf.memory.llm_writer`）

### LLMMemoryRetriever (`arf/memory/llm_retriever.py`)

每 turn 开始前调用廉价模型，从所有记忆中选出与当前 query 最相关的 top_k 条。

- **输入**：用户消息 + 记忆索引（id + category + 前 120 字符）
- **输出**：`{"relevant_ids": [...]}`
- **注入**：相关记忆拼接到 system prompt 的 `## Memory` 部分
- **回退**：LLM 调用失败时自动回退到 `RecentFirstRetriever`（按时间倒序取 top_k）

### RuleBasedMemoryWriter (`arf/memory/writer.py`)

无 LLM 依赖的规则引擎，基于中英文关键词匹配。当 `writer: rule` 时启用。

- **规则**：预定义关键词 → category 映射（如 "偏好"/"prefer" → preference, "记住"/"remember" → fact）
- **限制**：仅匹配 assistant 消息，最多 500 字符

## 配置

在 `agent.yaml` 的 `advanced.memory` 段：

```yaml
advanced:
  memory:
    store: file           # file | sqlite | none
    workspace: ./memory   # 存储目录
    retriever: llm        # llm | recent_first
    writer: llm           # llm | rule
    max_tokens: 2000
    top_k: 5
    model: quick          # 使用哪个模型做记忆操作
    temperature: 0.3
    thinking_enabled: false
```

## 引擎集成

`GraphEngine` 在两个关键节点自动调用记忆系统：

1. **检索**（`graph.py:111-123`）— 每 turn 循环开始，在模型调用之前
2. **写入**（`graph.py:258-263`，文本路径：`390-404`，流式工具路径：`437-442`）— 每 turn 模型响应之后

所有写入点都先 `store.load(session_id)` 获取已有条目，传入 `existing_entries`，使 LLM 能够正确地 dedup/update/delete。

## 数据流

```
User: "我喜欢Python，不喜欢Java"
    │
    ▼
[检索] LLMMemoryRetriever
    → memory.json (已有 "用户偏好FastAPI") 
    → 返回相关: ["偏好FastAPI"]
    → 注入 ## Memory
    │
    ▼
[模型] 看到: "## Memory\n- 偏好FastAPI"
    → 响应: "好的，记住了！喜欢Python，不喜欢Java"
    │
    ▼
[抽取] LLMMemoryWriter
    → 输入: 已有 ["偏好FastAPI"] + 最近消息
    → LLM 输出: {"actions": [{"action": "add", "entry": {"category": "preference", "content": "喜欢Python，不喜欢Java"}}]}
    → 写入 memory.json (去重: 内容不同，新增)
```

## 与旧实现的区别

| | 旧（app 层） | 新（框架层） |
|---|---|---|
| **存储** | `memory/{category}.md` (Markdown) | `memory/memory.json` (JSON) |
| **写入** | LLM 显式调用 `memory_store` 工具 | `LLMMemoryWriter` 自动抽取 |
| **检索** | 无 | `LLMMemoryRetriever` → 注入 system prompt |
| **去重** | 无（纯追加） | LLM 比对已有记忆，支持 add/update/delete |
| **更新** | 重复信息反复写入 | 通过 `replaces` 字段标记取代关系 |

## 迁移

旧 `.md` 格式数据通过 `scripts/migrate_memory_md_to_json.py` 一次性迁移：

```bash
python3 scripts/migrate_memory_md_to_json.py --workspace app/arf_default_assistant/memory
```

脚本解析 `fact.md`、`preference.md`、`decision.md` 的 `## Category (timestamp)` 块，转换为 `MemoryEntry` 写入 `memory.json`，并将原 `.md` 文件重命名为 `.bak`。

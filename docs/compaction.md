# Context Compaction — OOM Prevention via Sliding Window

ARF 通过滑动窗口压缩防止上下文窗口耗尽（OOM）。动态感知上下文大小，在超出阈值时自动触发：保留最近消息，将旧消息折叠为摘要，释放 token 预算。

## Architecture

```
每 turn 循环
    │
    ├─ [1] MemoryRetriever 检索相关记忆 → 写入 context_summary
    │
    ├─ [2] should_compact(state)         ← 动态感知上下文大小
    │       总字符数 > threshold × 3,000,000 ?
    │       │
    │       ├─ No  → 继续正常流程
    │       └─ Yes → compact(state)
    │                  │
    │                  ├─ 保留最近 4 条消息
    │                  ├─ 旧消息 → summarizer (LLM) → 追加到 context_summary
    │                  └─ 返回精简后的 state
    │
    └─ [3] 模型调用（接收精简后的 messages + 含摘要的 context_summary）
```

## 协议

`CompactionStrategy` 定义在 `arf/core/protocols/compaction.py`：

```python
class CompactionStrategy(Protocol):
    def should_compact(self, state: AgentState, threshold: float = 0.75) -> bool: ...
    async def compact(self, state: AgentState) -> AgentState: ...
```

## 实现：SlidingWindowCompactor

`arf/compaction/sliding_window.py` — 滑动窗口压缩器。

### 触发条件

```python
def should_compact(self, state, threshold=None):
    t = threshold or self._threshold          # 默认 0.75
    chars = sum(len(m["content"]) for m in state["messages"])
    return chars > t * 1_000_000 * 3          # 约 750k tokens @ default
```

| threshold | 字符阈值 | 约等于 tokens |
|-----------|---------|---------------|
| 0.25 | 750,000 | ~250k |
| 0.50 | 1,500,000 | ~500k |
| 0.75 (default) | 2,250,000 | ~750k |
| 1.00 | 3,000,000 | ~1M |

### 压缩行为

```python
async def compact(self, state):
    msgs = state["messages"]
    if len(msgs) <= 4:
        return state                          # 太少消息，不压缩

    recent = msgs[-4:]                        # 保留最近 4 条
    old_msgs = msgs[:-4]                      # 旧消息

    summary = state.get("context_summary", "")
    if self._summarize and old_msgs:
        new = await self._summarize(old_msgs) # LLM 摘要
        summary = f"{summary}\n[Earlier]: {new}"

    return {**state, "messages": recent, "context_summary": summary}
```

- 始终保留最近 **4 条消息**（覆盖一个完整的 user/assistant 交互 + 上一轮 context）
- 内存检索的 recall entries 保留在 `context_summary` 中不被覆盖
- 旧消息的 LLM 摘要追加为 `[Earlier]` 段
- 若无 summarizer，旧消息静默丢弃，不生成摘要

### LLM Summarizer

复用 memory model（`deepseek-v4-flash`, temp=0.3, thinking disabled）：

```python
async def _summarize(msgs):
    text = "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in msgs[-20:])
    prompt = (
        "Summarize the key facts, decisions, and context...\n"
        f"{text}\n\nSummary:"
    )
    return await _mem_model_call(prompt)
```

- 最多取最近 **20 条**旧消息用于摘要
- 每条消息截断至 200 字符
- 摘要失败时静默降级为占位文本

## 配置

```yaml
advanced:
  compaction:
    strategy: sliding_window    # sliding_window | summarization | none
    threshold: 0.75             # 0.0-1.0, 触发阈值
```

## 引擎集成

`GraphEngine` 在每次 turn 循环中，**记忆检索之后、模型调用之前**执行压缩检查：

```
invoke() / astream():
    memory retrieval → should_compact? → compact() → build messages → call model
```

两个执行路径（`invoke()` 和 `astream()`）均已接入。

## context_summary 协作

压缩与记忆检索共享 `context_summary` 字段，按以下顺序协作：

1. **检索先行**：每次 turn 开始时，`MemoryRetriever` 将相关记忆写入 `context_summary`
2. **压缩追加**：若触发压缩，旧消息摘要**追加**到已有的 `context_summary` 之后，而非覆盖
3. **模型可见**：system prompt 中的 `## Memory` 段包含检索记忆 + 历史摘要

```
## Memory
- 用户偏好使用 Linux 作为开发环境     ← 检索结果
- 用户是数据科学家，住在杭州            ← 检索结果
[Earlier]: 讨论了 Dijkstra 算法的实现，用户要求完整的类型标注和边界情况处理。  ← 压缩摘要
```

## 与 OS 模式的对应

| OS 概念 | ARF 实现 |
|---------|----------|
| 虚拟内存 | context_summary 保存被换出的"页" |
| 页面换出 | old messages → LLM summary → context_summary |
| 页面换入 | context_summary 注入 system prompt |
| 内存水位线 | should_compact() 的字符计数阈值 |
| 工作集 | 最近 4 条消息始终保留在"内存"中 |

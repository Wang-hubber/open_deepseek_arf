# Context Compaction — Token-Aware Sliding Window

ARF 通过滑动窗口压缩防止上下文窗口耗尽（OOM）。参照 Claude Code 的设计：以上一轮 `call_model` 返回的 token 用量为触发信号，超出窗口 75% 时自动压缩。长工具输出摘要存上下文，原文存硬盘。

## Architecture

```
每 turn 循环
    │
    ├─ [1] MemoryRetriever 检索相关记忆 → 写入 context_summary
    │
    ├─ [2] ModelRouter 路由 → 选定模型（quick / deep）
    │       │
    │       └─ 获取模型的 context_window（如 131072）
    │
    ├─ [3] should_compact(state, window_size)
    │       last_token_usage > threshold × window_size ?
    │       │
    │       ├─ No  → 继续
    │       └─ Yes → compact(state)
    │                  ├─ 保留最近 4 条消息
    │                  ├─ 旧消息 → LLM summarizer → 追加到 context_summary
    │                  └─ 返回精简 state
    │
    └─ [4] 模型调用 → 返回 usage.total_tokens → 存入 last_token_usage
```

## 触发机制

**信号来源**：上一轮 `call_model` 返回的 `usage.total_tokens`（API 报告的实际 token 用量）。

**触发条件**：
```python
def should_compact(self, state, threshold=0.75, window_size=128_000):
    last_usage = state.get("last_token_usage", 0)  # 上一轮用量
    return last_usage > threshold * window_size
```

**窗口跟随模型**：路由先于压缩执行。选定模型后，用该模型的 `context_window` 作为窗口大小。切换到不同窗口的模型时，阈值自动调整。

示例（128k 窗口，75% 阈值 = 96k）：
- `last_token_usage = 50k` → 不触发
- `last_token_usage = 100k` → 触发压缩，下一轮上下文减负
- 切换到 64k 窗口模型时（阈值 = 48k），50k 立即触发

## 压缩行为

```python
async def compact(self, state):
    msgs = state["messages"]
    recent = msgs[-4:]                          # 保留最近 4 条
    old_msgs = msgs[:-4]                         # 旧消息 → LLM 摘要
    summary = state.get("context_summary", "")
    if self._summarize and old_msgs:
        new = await self._summarize(old_msgs)    # LLM 摘要（最多取 20 条）
        summary = f"{summary}\n[Earlier]: {new}"
    return {**state, "messages": recent, "context_summary": summary}
```

- 始终保留 **最近 4 条消息**
- 旧消息摘要追加 `[Earlier]` 标记
- 记忆检索结果不被覆盖（`context_summary` 合并两者）
- 摘要失败时静默降级

## LLM Summarizer

默认使用 `deepseek-v4-flash`，无思考，温度 0.3（与 memory model 共享实例）：

```python
async def _summarize(msgs):
    text = "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in msgs[-20:])
    prompt = "Summarize the key facts, decisions, and context...\n{text}\n\nSummary:"
    return await _mem_model_call(prompt)
```

## 工具输出摘要

参照 Claude Code：长工具输出 → LLM 摘要存上下文，原文写硬盘。

```python
async def summarize_tool_output(self, tool_name, output, turn):
    if len(output) <= 2000:           # 短输出：原样保留
        return output

    # 长输出：写入 disk，LLM 摘要替换上下文
    path = f"memory/tool_outputs/turn_{turn}_{tool_name}.txt"
    write_to_disk(path, output)

    if self._summarize:
        summary = await self._summarize(...)
        return f"[Tool output summarized — full at {path}]\n{summary}"
    return f"[Tool output truncated — full at {path}]\n{output[:2000]}..."
```

## 配置

### 压缩配置（agent.yaml）
```yaml
advanced:
  compaction:
    strategy: sliding_window
    threshold: 0.75               # 触发比例 (0.0-1.0)
```

阈值按选定模型的 `context_window` 计算，无需重复配置窗口大小。

### 模型窗口（agent.yaml）
```yaml
models:
  - name: quick
    model: deepseek-v4-flash
    context_window: 131072        # 模型实际窗口大小
  - name: deep
    model: deepseek-v4-pro
    context_window: 131072
```

## 引擎流转

```
invoke() / astream():
    memory retrieval → routing → should_compact? → compact() → call_model
                                                      ↓
                                              tool_output → summarize_tool_output?
```

路由在前，压缩在后 —— 确保用正确的模型窗口判断是否超限。

## 与 OS 模式的对应

| OS 概念 | ARF 实现 |
|---------|----------|
| 虚拟内存水位线 | `usage.total_tokens` vs `threshold × context_window` |
| 页面换出 | old messages → LLM summary → `[Earlier]` in context_summary |
| 页面换入 | context_summary 注入 system prompt |
| 内存映射文件 | 长工具输出 → disk，保留摘要/指针在内存 |
| big.LITTLE 迁移 | 路由切换模型时自动用新模型的窗口大小 |

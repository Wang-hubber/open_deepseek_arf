# Compaction Plugin — Token 感知上下文压缩

防止会话上下文超出模型上下文窗口。当 token 用量达到阈值时，自动将旧轮次压缩为 LLM 摘要。

---

## 触发条件

`round_end` hook 触发时检查：

```
last_token_usage >= threshold × window_size  →  执行压缩
```

- `threshold`（默认 0.75）：75% 窗口满即触发
- `window_size`（默认 131072）：Context window token 上限
- `keep_count`（默认 8）：压缩后保留的最近消息数量

## 压缩算法

1. 保留最近 `keep_count` 条非工具消息
2. 旧消息 → 通过 sysmodel 生成结构化摘要
3. 追加 `context_summary` 到 state
4. 冷却 2 轮（防连续压缩导致循环）

## 配置

```yaml
plugins:
  - compaction

plugins_config:
  compaction:
    model: deepseek-v4-flash
    threshold: 0.75
    window_size: 131072
    keep_count: 8
```

## 公共方法

- `summarize_tool_output(text)` — 压缩过长的工具输出，避免单个 tool_result 撑爆窗口

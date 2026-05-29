# 高级配置

`agent.yaml` 的 `advanced:` 段控制框架所有子系统的行为。所有字段都有默认值，不配置即可运行。

---

## 记忆

框架自动从对话中提取事实、偏好和决策，存入 `memory/memory.json`。下一轮对话自动检索相关记忆注入 system prompt。

```yaml
advanced:
  memory:
    store: file                 # file | sqlite | none
    workspace: ./memory         # 存储目录
    retriever: llm              # llm | recent_first
    writer: llm                 # llm | rule
    max_tokens: 2000            # 检索记忆的最大 token 数
    top_k: 5                    # 每次检索的记忆条数
```

- `retriever: llm` 用 LLM 判断哪些记忆与当前 query 相关
- `writer: llm` 用 LLM 从对话中提取事实/偏好/决策
- `retriever: recent_first` / `writer: rule` 用简单的规则匹配，不消耗 API 调用

---

## 模型路由

配置多个模型时自动启用。每次用户 query 先用廉价模型分类，再路由到对应模型。

```yaml
advanced:
  routing:
    strategy: two_tier          # two_tier | static
    default: quick              # 默认模型
    classify:
      medium: quick             # 简单任务 → quick
      complex: deep             # 复杂任务 → deep
    fallback:
      deep: quick               # deep 不可用时回退到 quick
```

- `strategy: static` 始终使用默认模型，不自动切换
- 分类器使用 `system_model` 指定的廉价模型，不开启深度推理

---

## 上下文压缩

Token 用量超过模型上下文窗口的 75% 时自动触发。旧轮次被压缩为结构化摘要，保留最近 4 条消息。

```yaml
advanced:
  compaction:
    strategy: sliding_window    # sliding_window | none
    threshold: 0.75             # 触发阈值（占 context_window 的比例）
```

压缩摘要格式：
```
- Completed: 已完成的任务
- In Progress: 当前任务和 TODO
- Files Modified: 文件变更记录
- Decisions: 架构决策
- Facts & Preferences: 用户偏好
- Errors & Debugging: 错误信息和排查
- Next Steps: 下一步计划
```

长工具输出（超过阈值）也会被摘要后落盘，消息中保留摘要链接。

---

## 权限

每次工具调用前执行 deny → ask → allow 检查。

```yaml
advanced:
  guardrails:
    permissions:
      deny: [python_exec, file_deleter]     # 硬阻断
      ask: [file_writer]                    # 审批（60s 超时自动拒绝）
      allow: [file_reader, web_search, web_fetch]  # 自动放行
      deny_patterns:                        # 可配置的危险命令模式
        - "rm -rf"
        - "sudo"
        - "chmod 777"
```

未命中任何列表的工具默认走 `ask`（安全默认）。`deny` 列表优先于 `ask`，`ask` 优先于 `allow`。

---

## 沙箱

```yaml
advanced:
  guardrails:
    input: none                 # none | regex_block | llm_classifier
    output: regex_clean         # none | regex_clean | llm_classifier
    tool_params: path_check     # none | path_check | command_check
```

- `tool_params: path_check` — 每次工具调用前阻断 `..` 路径穿越、绝对路径、工作区逃逸
- `output: regex_clean` — 输出过滤，API key / 手机号替换为 `[REDACTED]`

### 路径沙箱

```yaml
advanced:
  sandbox:
    allow_escape: false           # true = 跳过所有路径检查（调试用）
    writable_dirs: []             # 工作区外的额外可写目录白名单
```

`PathCheckToolGuard` 根据此配置控制路径检查行为。`allow_escape: false` 为默认安全策略。

---

## 工具并发

```yaml
advanced:
  concurrency:
    strategy: parallel            # parallel | sequential
    max_concurrency: 5            # 最大并发工具调用数
```

控制单轮内多工具调用的执行方式。`strategy: sequential` 时工具逐个执行；`max_concurrency` 限制并行上限。

---

## 资源热加载

```yaml
advanced:
  reload:
    watch: true                 # 启用 FileWatcher（默认 true）
    poll_interval: 5            # 轮询间隔秒数（非 Linux 平台）
```

FileWatcher 在 Linux 上使用 inotify（亚秒级响应），在其他平台使用轮询。检测到 `tools/`、`skills/`、`models/` 目录下的文件变更后自动清除缓存，下一轮对话生效。

---

## 完整配置示例

```yaml
advanced:
  max_turns: 50                 # 每轮对话最大工具调用轮次

  memory:
    store: file
    retriever: llm
    writer: llm

  routing:
    strategy: two_tier
    default: quick
    classify: {medium: quick, complex: deep}
    fallback: {deep: quick}

  compaction:
    strategy: sliding_window
    threshold: 0.75

  guardrails:
    input: none
    output: regex_clean
    tool_params: path_check
    permissions:
      deny: []
      ask: [file_writer]
      allow: [file_reader, web_search, web_fetch]

  sandbox:
    allow_escape: false
    writable_dirs: []

  concurrency:
    strategy: parallel
    max_concurrency: 5

  reload:
    watch: true
    poll_interval: 5
```

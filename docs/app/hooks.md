# 生命周期 Hook

Hook 是独立子进程脚本，在 Agent 的六个生命周期事件点触发。框架通过 `SubprocessHookRunner` 以 `asyncio.create_subprocess_shell` 启动，并行执行。

---

## 六个事件点

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `session_start` | 会话开始时 | 初始化日志、加载外部配置 |
| `pre_model_call` | 每次调用模型前 | 消息预处理、敏感词过滤 |
| `post_model_call` | 每次模型响应后 | 响应审计、内容归档 |
| `pre_tool_exec` | 工具执行前 | 参数校验、权限二次检查 |
| `post_tool_exec` | 工具执行后 | 工具调用日志、结果归档 |
| `session_end` | 会话结束时 | 清理临时文件、发送通知 |

---

## 最简 Hook

```yaml
# agent.yaml
hooks:
  - name: log_tool_calls
    type: post_tool_exec
    run: ["python", "./hooks/log_tool_calls.py"]
    timeout: 5s
```

**hook 脚本**：

```python
# hooks/log_tool_calls.py
import sys, json

# 框架通过上下文环境变量传递信息
# 实际实现中，可读取 sys.argv 或标准输入
print(f"Hook executed: {sys.argv[0]}", file=sys.stderr)
sys.exit(0)
```

---

## 退出码约定

| 退出码 | 行为 |
|--------|------|
| `0` | 正常执行，继续 Agent 流程 |
| `1` | 阻断当前操作 |
| `2` | 将脚本的 stdout 作为 system 消息注入对话流，LLM 下一轮可见 |

退出码 2 是最强大的机制——Hook 可以向对话中插入提示、修正或上下文，LLM 在下一轮会看到这些内容：

```python
# hooks/remind_user.py
import sys
print("注意：用户之前提到过偏好 dark mode 界面风格。", file=sys.stdout)
sys.exit(2)
```

---

## Hook 配置完整字段

```yaml
hooks:
  - name: my_hook
    type: post_tool_exec        # 六个事件之一
    run: ["python", "./hooks/my_hook.py"]  # 命令行（支持多参数）
    timeout: 10s                # 超时时间，默认 30s
    env:                        # 可选环境变量
      MY_VAR: "value"
```

---

## 注意事项

- Hook 是**独立子进程**，不能直接访问 Agent 的内存状态。获取会话信息通过 `pre_tool_exec` / `post_tool_exec` 的上下文
- Hook 的 stdout 在退出码为 2 时被注入对话，其余情况被丢弃
- stderr 被记录到框架日志
- 超时的 Hook 被 SIGKILL 强制终止，退出码记录为 -1
- 同一事件类型的多个 Hook 按 `agent.yaml` 声明顺序执行
- `create_subprocess_shell` 意味着 shell 元字符会被解释——Hook 的 `run` 命令来自配置文件（受信输入），不是用户输入

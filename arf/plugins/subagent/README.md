# Subagent Plugin

> `subagent` — 为父 agent 提供上下文隔离的子任务派发能力。

## 解决的问题

当 agent 连续处理复杂任务时，`messages` 越来越长。中间过程（读文件、搜索、跑命令）会永久留在父对话里，让后续问题越来越难回答。

子 agent 把局部任务放进**独立的干净上下文**里执行，做完只把**摘要**带回父 agent。

```
Parent agent
  |  1. 决定把局部任务外包
  v
Subagent (messages=[])
  |  2. 在自己的上下文里读文件 / 搜索 / 执行工具
  v
Summary
  |  3. 只把最终结果带回
  v
Parent agent continues
```

## 启用

在 `agent.yaml` 的 `plugins:` 列表中加入：

```yaml
plugins:
  - subagent
```

框架启动时自动扫描 `arf/plugins/subagent/tools/subagent/`，注册 `subagent` 工具。

## 使用

父 agent 对话中调用：

```
subagent(prompt="列出项目中所有测试文件，说明用的什么测试框架")
```

**参数**：

| 参数 | 必填 | 说明 |
|------|------|------|
| `prompt` | ✓ | 子任务的详细描述 |
| `model` | | 模型名，默认用 system_model |
| `description` | | 简短标签，用于 trace/logging |

## 工作原理

1. 父 engine 遇到 `subagent` 工具调用
2. `function.py::execute()` 创建子 `GraphEngine`：
   - **独立上下文**：`messages = [{"role": "user", "content": prompt}]`
   - **过滤工具集**：只暴露 `read`、`grep`、`glob`、`bash`（不带 `subagent`，防递归）
   - **独立存储**：`InMemoryStateStore`，不污染父状态
   - **10 轮上限**：`ReActStrategy(max_turns=10)` 防无限循环
   - **120 秒超时**：`tool.yaml` 声明，超时自动终止
3. 子 engine 跑完，提取最后一条 assistant 消息作为摘要返回父 agent

## 配置

`arf/plugins/subagent/config.yaml`：

```yaml
max_turns: 10    # 子 agent 最大轮次
timeout: 120     # 超时（秒）
```

`arf/plugins/subagent/tools/subagent/tool.yaml` 的 `execution.allowed_tools` 控制子 agent 可用工具集，默认：

```yaml
allowed_tools:
  - read
  - grep
  - glob
  - bash
```

如需增减，直接编辑此列表即可。

## 与 Handoff 的区别

| | subagent | handoff |
|------|----------|---------|
| 上下文 | 空白 `messages=[]` | 继承父 agent 全部对话 |
| 工具 | 过滤的子集 | 切换 agent 配置的全部工具 |
| 返回 | 摘要（一句话） | 控制权转移，可能需要 handoff 回来 |
| 用途 | 一次性子任务外包 | 委托给不同能力的 agent |

## 测试

```bash
pytest tests/test_subagent_plugin.py -v
```

6 个集成测试覆盖：摘要返回、空 prompt 拦截、上下文隔离、模型错误兜底、工具过滤、工具阻断。

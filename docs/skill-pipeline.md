# Skill Pipeline — 工具执行时序与依赖声明

Skill 不仅是工具列表，还可以声明工具的执行顺序和依赖关系。当任务需要精确可复现的多步操作时，pipeline 提供框架级的强保证。

## Architecture

```
Skill 声明 pipeline:
    file_writer → resource_loader
    (resource_loader depends_on: [file_writer])

LLM 尝试调用 resource_loader（跳过 file_writer）
    │
    ▼
GraphEngine 拦截
    │
    ├─ SkillPipeline.can_execute("resource_loader", completed=[])
    │       → False: depends_on file_writer not completed
    │
    ├─ 返回错误: "Pipeline violation: 'resource_loader' requires ['file_writer']"
    │
    └─ LLM 收到阻塞反馈 → 按正确顺序调用

LLM 按正确顺序: file_writer → resource_loader
    │
    ▼
GraphEngine 放行 + 追踪 completed steps
```

## 配置

Skill 的 `pipeline` 字段声明工具调用顺序：

```yaml
skills:
  - name: resource_scaffold
    description: Generate a new Tool or Skill resource
    tools: [file_writer, resource_loader]
    activation: discoverable
    pipeline:
      - tool: file_writer
        description: "Create tool.yaml and function.py scaffold files"
      - tool: resource_loader
        depends_on: [file_writer]
        description: "Activate the newly created resource"
```

## 实现

### SkillPipeline (`arf/skills/pipeline.py`)

```python
class SkillPipeline:
    def __init__(self, steps: list[dict]):
        self._steps: dict[str, list[str]]  # tool → [depends_on]
        self._order: list[str]              # declared order
        self._validate()                    # check integrity

    def can_execute(self, tool_name, completed_steps) -> bool:
        """Return True if all depends_on tools are in completed_steps."""
        ...

    def next_steps(self, completed_steps) -> list[str]:
        """Return which pipeline steps are ready to execute."""
        ...

    def is_complete(self, completed_steps) -> bool:
        """Return True if all pipeline steps done."""
        ...

    def validation_error(self, tool_name, completed_steps) -> str:
        """Human-readable error for why execution is blocked."""
        ...
```

### 初始化校验

在创建 `SkillPipeline` 时立即校验：
- **缺失依赖**：`tool A depends_on B` 但 `B` 不在 pipeline 中 → `ValueError`
- **循环依赖**：`A → B → A` → `ValueError`
- 校验通过才允许后续使用

### 引擎集成

在 `GraphEngine.invoke()` 和 `astream()` 的工具守卫阶段（路径检查之前）：

```python
# Pipeline order check (hard block — framework guarantee)
if pipeline_data:
    sp = SkillPipeline(pipeline_data["steps"])
    if not sp.can_execute(tool_name, completed):
        denied_calls.append((tool_name, sp.validation_error(...)))
        continue  # BLOCKED — skip this tool call
```

状态追踪存储在 `AgentState` 中：

```python
state["active_pipeline"] = {
    "steps": [{"tool": "file_writer", "depends_on": []},
              {"tool": "resource_loader", "depends_on": ["file_writer"]}],
    "completed": ["file_writer"]  # 动态更新
}
```

### 不会阻塞的情况

- **pipeline 为空的 skill**：所有工具自由调用
- **pipeline 外的工具**：不受限制（如 `web_search` 总是允许）
- **已完成所有步骤**：`is_complete()` 返回 True，后续自由

## 验证案例

`resource_scaffold` skill 的 pipeline 验证：

| 场景 | 结果 |
|------|------|
| 先调 `file_writer` | ✅ 放行（无依赖） |
| `file_writer` 完成后调 `resource_loader` | ✅ 放行（依赖满足） |
| 跳过 `file_writer` 直接调 `resource_loader` | ❌ 阻断：「requires ['file_writer'] to complete first」 |
| 调用 pipeline 外的 `web_search` | ✅ 放行（不受限制） |
| `next_steps()` 指引 | `[] → ["file_writer"] → ["resource_loader"] → []` |

## 设计原则

- **可选增强**：无 pipeline 的 skill 行为不变
- **强保证**：框架引擎强制执行，不是建议
- **声明式**：YAML 配置，人类可读
- **早校验**：循环依赖和缺失依赖在启动时检出
- **清晰反馈**：阻断时返回具体错误和下一步指引

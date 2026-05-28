# ARF Fact-Check Report — 2026-05-28 — Skill Pipeline

## Summary
- **Total tests**: 35
- **Passed**: 35
- **Findings**: 0 (文档与代码高度一致)

## Verified Claims

### §2.4 Skill Pipeline
- [x] `SkillPipeline` 在 `arf/skills/pipeline.py`
- [x] pipeline 外的工具不受限（`can_execute` 对未知工具返回 True）
- [x] 依赖未满足时阻断（`can_execute` 返回 False）
- [x] 依赖满足时放行
- [x] 空 pipeline 时 `is_empty()` 返回 True
- [x] 循环依赖检测：A→B→A 抛出 `ValueError("circular")`
- [x] 缺失依赖检测：A depends_on nonexistent 抛出 `ValueError("not in the pipeline")`
- [x] `next_steps()` 返回就绪步骤
- [x] `is_complete()` 检查所有步骤完成
- [x] `steps` property 返回依赖映射
- [x] `order` property 返回声明顺序
- [x] `validation_error()` 返回人类可读错误信息

### §2.2 ConcurrentToolExecutor
- [x] `ConcurrentToolExecutor` 在 `arf/engine/tool_executor.py`
- [x] 构造函数参数：`tool_resolver`, `strategy="parallel"`, `max_concurrency=5`
- [x] sequential 模式逐个执行
- [x] parallel 模式使用 `asyncio.Semaphore` + `asyncio.gather`
- [x] `execute()` 接受 `agent_mode`, `engine`, `state_store` 参数

### §2.1 并发模型
- [x] `ConcurrencyConfig` 存在，`strategy="parallel"`, `max_concurrency=5`

### §2.6 SequentialScheduler
- [x] `SequentialScheduler` 在 `arf/concurrency/sequential.py` 已定义
- [x] `schedule()` 和 `execute()` 方法存在

### §2.8 Tool Call Closure
- [x] `_close_tool_calls()` 方法存在，注入 `(tool result unavailable)` 占位
- [x] `_step_classify_tool_calls()` 方法存在，处理 `denied_calls` 和 `valid_calls`

### §2.3 Hook 并行触发
- [x] `SubprocessHookRunner` 存在

### 引擎集成
- [x] `GraphEngine.__init__` 接受 `tool_executor` 参数
- [x] `GraphEngine.approve()` 方法存在
- [x] `_step_classify_tool_calls` 中集成 SkillPipeline 检查

## Notes

今回 Skill Pipeline 域的 fact-check **0 发现**，文档与代码高度一致。文档对并发模型的描述（§2.7 的 GIL/协程/进程分析）属于概念性内容，非代码声称，不纳入 fact-check 范围。`SequentialScheduler` 如文档所述确实未在任何地方使用。REACT loop strategy 存在但文档未详细描述——这不影响 skill-pipeline 域的核心声称。

## Test Suite
- **文件**: `tests/fact_check/test_skill_pipeline.py`
- **结构**: 7 个 TestClass，35 个测试方法
- **覆盖**: 文档 §2.1-§2.8 核心声称

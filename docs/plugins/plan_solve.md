# Plan-Solve Plugin — 多步规划与依赖管理

将复杂任务拆解为 DAG 步骤图，每个步骤在隔离子 Agent 中执行，强制执行依赖顺序。

---

## 架构

```
pre_action (工具调用): plan_dispatch → 验证依赖完成 → 子 ControlPlane 执行步骤 → 持久化结果
                       plan_summarize → 验证全部完成 → LLM 汇总 → 标记计划完成

round_start: 检测未完成计划 → 发射 plan_resumable 事件
```

## 工具族

| 工具 | 说明 |
|------|------|
| `plan_create` | 创建计划。验证步骤 DAG（循环检测、索引唯一性、引用有效性），持久化 `plan.json` |
| `plan_dispatch` | 执行单个步骤。验证依赖已完成 → 创建子 ControlPlane（独立 state_store）→ 最多 10 轮 → 提取结果 |
| `plan_status` | 只读查询当前计划状态（哪些完成、哪些阻塞、哪些待执行） |
| `plan_summarize` | 汇总。验证全部步骤完成 → 构建汇总 prompt → 调用模型 → 写入最终输出 → 标记计划完成 |

## 依赖验证

`validation.py` 提供完整的 DAG 验证：
- 索引唯一性检查
- 自引用检测
- 无效引用检测
- 对称性检查（A 阻塞 B ⇔ B 依赖 A）
- Kahn 算法循环检测

## 配置

```yaml
plugins:
  - plan_solve

plugins_config:
  plan_solve:
    model: deepseek-v4
    max_subagent_turns: 10
```

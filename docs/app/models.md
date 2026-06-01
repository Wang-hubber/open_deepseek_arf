# 配置模型

模型在 ARF 中是**声明式配置**——你只需要描述模型的端点、上下文窗口和推理参数，框架自动完成适配器注入、路由决策和 API 调用。

---

## 最简配置

```yaml
models:
  - type: quick
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    context_window: 800000
```

| 字段 | 说明 | 必填 |
|------|------|------|
| `type` | 模型类型：`quick`（快速廉价）或 `deep`（深度推理） | 是 |
| `model` | API 中使用的模型名，如 `deepseek-v4-pro` | 是 |
| `api_base` | OpenAI 兼容 API 地址 | 是 |
| `api_key_env` | 从哪个环境变量读取 API key | 是 |
| `api_type` | `openai` / `anthropic` / `custom`，默认 `openai` | 否 |
| `context_window` | 模型上下文窗口 token 数上限，用于压缩阈值计算 | 是 |
| `max_token` | 每次 API 调用输出 token 上限（映射为 `max_tokens`），默认不限制 | 否 |
| `kwargs` | 传递给 API 的额外参数（temperature、reasoning_effort 等） | 否 |
| `activation` | `kernel` / `discoverable`，默认 `discoverable` | 否 |

---

## 多模型 + 路由

```yaml
models:
  - type: quick
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    context_window: 800000
    kwargs:
      reasoning_effort: high
      temperature: 0.7

  - type: deep
    model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    context_window: 1000000
    kwargs:
      reasoning_effort: max

advanced:
  routing:
    strategy: two_tier
    default: quick
    classify:
      medium: quick
      complex: deep
    fallback:
      deep: quick
```

配置多个模型后，框架自动启用 `TwoTierRouter`——用廉价模型对用户 query 分类（medium → quick，complex → deep），每轮可动态切换。详见 [高级配置](advanced.md)。

---

## 文件系统 vs agent.yaml

模型可以放在两个位置：

| 位置 | 格式 | 用途 |
|------|------|------|
| `models/*.yaml` | 每个文件一个模型 | **源定义**，框架自动扫描 |
| `agent.yaml` 中的 `models:` 段 | YAML 列表 | **可选覆盖**，同名字段覆盖文件系统值 |

**推荐**：模型定义放在 `models/` 目录下，`agent.yaml` 只写覆盖（如果有需要微调的参数）：

```yaml
# models/quick.yaml（源定义）
type: quick
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 800000
activation: kernel
kwargs:
  temperature: 0.7
```

```yaml
# agent.yaml（仅覆盖 temperature）
models:
  - type: quick
    temperature: 0.3
```

合并优先级：**agent.yaml 覆盖 > 文件系统字段 > Pydantic 默认值**。

---

## activation 字段

| 值 | 行为 |
|----|------|
| `kernel` | 框架内置模型，BaseAgent 初始化时加载，之后冻结不可变 |
| `discoverable` | 用户配置的模型，FileWatcher 检测到文件变更后自动热重载 |

---

## 添加新模型

在 `models/` 目录下新建一个 `.yaml` 文件即可。FileWatcher 自动检测，下一轮对话生效。无需重启服务。

```bash
# 用 OpenRouter 的端点替换 deep 模型
cat > models/deep-router.yaml << 'EOF'
type: deep
model: deepseek/deepseek-v4-pro
api_base: https://openrouter.ai/api/v1
api_key_env: OPENROUTER_API_KEY
context_window: 1000000
activation: kernel
EOF
# 两个文件都定义 type: deep 时，后加载的优先（或通过 agent.yaml 精确控制）
```

# 编写工具

工具由两个文件组成，放在 `tools/<name>/` 目录下。框架自动发现，无需手动注册。

---

## 最简工具

```
tools/
└── echo/
    ├── tool.yaml
    └── function.py
```

**tool.yaml** — Schema 定义：

```yaml
name: echo
description: 返回用户输入的内容
parameters:
  type: object
  properties:
    text:
      type: string
      description: 要回显的文本
  required: [text]
activation: kernel
```

**function.py** — 实现：

```python
async def execute(text: str) -> dict:
    return {"result": f"Echo: {text}"}
```

关键约定：
- 函数名必须是 `execute`
- 参数名和类型必须与 `tool.yaml` 的 `properties` 一一对应
- 返回值 `dict`，无固定格式——整个 dict 作为工具结果传给 LLM
- 使用 `async def`（即使函数内部是同步操作）
- `activation: kernel` 的工具始终在 Agent 的工具列表中，`discoverable` 的按需加载

---

## tool.yaml 完整字段

| 字段 | 说明 | 必填 |
|------|------|------|
| `name` | 工具名，LLM 通过此名调用 | 是 |
| `description` | 一句话描述，LLM 据此判断何时使用 | 是 |
| `parameters` | JSON Schema 格式的参数定义 | 否 |
| `activation` | `kernel` / `discoverable` | 否（默认 `discoverable`） |
| `execution.sandbox` | 沙箱模式，当前仅 `inherit` | 否 |
| `execution.timeout` | 超时时间，如 `30s` | 否 |

---

## function.py 编写要点

### 基本模式

```python
async def execute(param1: str, param2: int = 0) -> dict:
    """参数名和类型必须与 tool.yaml 的 properties 一致"""
    # 同步操作用 try/except 包裹
    # 异步操作用 await
    return {"ok": True, "result": ...}
```

### 错误返回

框架不强制错误格式，但推荐返回 `error` 字段：

```python
async def execute(path: str) -> dict:
    try:
        content = open(path).read()
        return {"content": content, "size": len(content)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
```

### 工作区隔离

文件类工具应限制在工作区内。推荐模式：

```python
from pathlib import Path
WORKSPACE = Path("workspaces/default")

async def execute(path: str, content: str) -> dict:
    p = WORKSPACE / path  # 框架的 PathCheckToolGuard 会额外校验
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p)}
```

框架的 `PathCheckToolGuard` 在每次工具调用前自动阻断 `..` 路径穿越和绝对路径。工具本身不需要做路径校验——守卫层已覆盖。

### 异步 vs 同步

使用 `async def`。同步操作（文件 I/O、subprocess）直接写，异步操作（HTTP 请求、数据库）用 `await`。框架通过 `FunctionBackend` 自动检测 `__await__` 并处理。

### Rollback — 数据修改工具的回滚

涉及文件写入、资源创建等数据修改的工具，**应该**在 `function.py` 中同时导出 `rollback` 函数。`rollback` 签名与 `execute` 完全一致，框架在 `execute` 抛出异常后自动调用。

```python
async def execute(path: str, content: str) -> dict:
    p = WORKSPACE / path
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p)}

async def rollback(path: str, content: str) -> dict:
    """撤销文件写入 — execute 失败时框架自动调用"""
    p = WORKSPACE / path
    p.unlink(missing_ok=True)
    return {"ok": True, "action": "deleted", "path": str(p)}
```

**关键约定**：
- `rollback` 与 `execute` 签名相同，接收相同的参数
- 返回值 `{"ok": True, "action": "..."}` 或 `{"ok": False, "error": "..."}`
- 是非强制规范 — 有则回滚，无则跳过
- 只读工具（`file_reader`、`web_search` 等）无需提供

---

## 现有工具参考

参考 app 中内置的 15 个工具实现，位于 `app/arf_default_assistant/tools/`：

| 工具 | 说明 | 模式参考 |
|------|------|---------|
| `file_reader` | 读取文件 / 列出目录 | 路径操作 |
| `file_writer` | 创建或覆盖文件 | 路径操作 |
| `file_deleter` | 软删除（重命名为 .deleted） | 路径操作 |
| `web_search` | DuckDuckGo 搜索 | HTTP + 解析 |
| `web_fetch` | HTTP GET 获取网页 | HTTP 请求 |
| `python_exec` | 执行 Python 片段 | subprocess |
| `resource_scaffold` | 创建工具/技能骨架 | 文件生成 |

---

## 添加新工具

在 `tools/` 下创建目录和两个文件即可。FileWatcher 自动检测，下一轮对话生效。无需重启。

```bash
mkdir -p tools/my_tool
# 编写 tool.yaml + function.py
# 下一轮对话自动可用
```

也可通过对话让 Agent 自己调用 `resource_scaffold` 创建工具骨架。

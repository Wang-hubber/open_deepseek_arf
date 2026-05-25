# 技能与流水线

Skill 将多个工具组合为一个可被 LLM 发现和加载的能力。一个 Skill 就是一组相关工具 + 一段提示词 + 可选的执行依赖声明。

---

## 最简 Skill

```yaml
# skills/file_ops.yaml
name: file_ops
description: 文件的读取、写入、列表、删除和下载操作
tools:
  - file_reader
  - file_writer
  - file_deleter
  - file_download
activation: kernel
```

| 字段 | 说明 | 必填 |
|------|------|------|
| `name` | 技能名 | 是 |
| `description` | 一句话描述，LLM 据此判断何时加载 | 是 |
| `tools` | 此技能包含的工具名列表 | 否 |
| `activation` | `kernel` / `discoverable` / `passive` | 否（默认 `discoverable`） |
| `pipeline` | 工具执行依赖声明（见下文） | 否 |
| `prompt` | 加载此技能时注入 system prompt 的提示词 | 否 |

---

## 渐进式披露

`activation: kernel` 的技能始终在 LLM 的技能列表中。`discoverable` 的技能不在列表中，但 LLM 知道它们的存在——当用户意图匹配时，LLM 会调用 `file_reader` 读取 `skills/<name>.yaml` 获取完整指令。

这就是 ARF 的**渐进式披露**机制：只为核心能力付费，按需扩展。

---

## Pipeline — 工具执行依赖

当 Skill 中的工具需要按特定顺序执行时，声明 `pipeline`：

```yaml
# skills/resource_scaffold.yaml
name: resource_scaffold
description: 创建新的工具或技能资源
tools:
  - file_writer
  - resource_loader
activation: discoverable
pipeline:
  - tool: file_writer
    description: 创建 tool.yaml 和 function.py 骨架文件
  - tool: resource_loader
    depends_on:
      - file_writer
    description: 激活新创建的资源
```

引擎在每次工具调用前检查 pipeline 约束：
- `file_writer` 无依赖，可直接执行
- `resource_loader` 依赖 `file_writer`——如果 `file_writer` 尚未成功执行，引擎硬阻断 `resource_loader`

依赖未满足时，引擎 emit `tool_call_end` 错误事件，LLM 收到错误反馈后可重试。

---

## 文件系统 vs agent.yaml

Skill 可以放在两个位置：

| 位置 | 格式 | 用途 |
|------|------|------|
| `skills/*.yaml` | 每个文件一个 skill | **源定义**，框架自动扫描 |
| `agent.yaml` 中的 `skills:` 段 | YAML 列表 | **可选覆盖**，同名字段覆盖文件系统值 |

推荐：skill 定义放在 `skills/` 目录，`agent.yaml` 只写覆盖。

---

## 添加新 Skill

```bash
# 新建一个 YAML 文件
cat > skills/my_new_skill.yaml << 'EOF'
name: my_new_skill
description: 审查和格式化代码
tools:
  - file_reader
  - file_writer
activation: discoverable
EOF
# 下一轮对话自动可用
```

也可通过对话让 Agent 调用 `resource_scaffold` 创建 skill 骨架。

---

## 现有 Skill 参考

参考 app 中的 9 个 skill：`app/arf_default_assistant/skills/`。

| Skill | 工具 | 说明 |
|-------|------|------|
| `file_ops` | file_reader, file_writer, file_deleter, file_download | 文件操作 |
| `code_review` | file_reader | 代码审查 |
| `debug` | file_reader, file_writer, python_exec, web_search | 调试 |
| `error_handler` | — | 错误自愈 |
| `resource_scaffold` | file_writer, resource_loader | 资源创建 |

# ARF：一个能自己写代码的 AI 智能体框架——文件系统即架构，对话即开发

> 我们开源了一个基于文件系统的自演进 AI 智能体框架。它没有 ORM、没有注册中心、没有 UI 配置向导。一个工具就是两个文件。一次对话就能创造永久能力。系统提示词仅 ~800 token。

---

## 为什么又造了一个轮子？

市面上 AI Agent 框架已经多得数不过来了。LangChain、CrewAI、Dify、AutoGPT……为什么还要再做一个？

坦白说，我们用过其中不少。每次的体验都类似：

- **LangChain** 太重了。为了调用一个 LLM，你需要理解 Chain、Agent、Tool、Callbacks 这一整套抽象。50+ 个工具定义全塞进 system prompt，每次 API 调用都在为 95% 不会用到的工具浪费 token。
- **Dify** 很好用，但是低代码。你想自定义一个工具？打开 Web UI，填表单，点保存。然后你的配置锁在了平台的数据库里。Git diff 看不出来。Code review 没法做。CI/CD 跑不起来。
- **自己从头写** 灵活性最高，但每次都要重新实现会话管理、工具调度、记忆压缩、trace 追踪……这些跟业务无关的"胶水代码"占了一半工作量。

我们的想法很简单：**能不能把文件系统本身作为 Agent 资源的唯一真相来源？**

目录即命名空间。YAML 即声明。Python 即实现。没有数据库。没有注册中心。

这就是 ARF —— Agent Resources & RunTime FrameWork。

> GitHub: [https://github.com/Wang-hubber/open_deepseek_arf](https://github.com/Wang-hubber/open_deepseek_arf)
> Gitee: [https://gitee.com/dalaydata/open_deepseek_arf](https://gitee.com/dalaydata/open_deepseek_arf)

---

## 核心理念：文件系统是最好的资源管理器

大多数 Agent 框架里，工具注册是这样的：

```python
@tool
def web_search(query: str) -> str:
    """Search the web for information."""
    ...

agent = Agent(tools=[web_search, ...])  # 手动注册每个工具
```

ARF 的做法是：

```
tools/
└── web_search/
    ├── tool.yaml      # 声明：这个工具有什么参数，做什么用
    └── function.py    # 实现：具体的 Python 函数
```

两个文件。一个目录。**框架自动发现，你不需要手动注册。**

同样的约定适用于所有资源：

| 实体 | 位置 | 说明 |
|------|------|------|
| Model | `models/<name>/config.yaml` | API 端点、密钥、推理参数 |
| Tool | `tools/<name>/tool.yaml` + `function.py` | 可调用能力 + JSON Schema |
| Skill | `skills/<name>/skill.yaml` | 可复用提示词 + 工具编排 |
| Hook | `.hooks.json` → 子进程脚本 | 生命周期事件拦截器 |

没有装饰器。没有基类。不需要 `__init__.py`。不需要任何注册钩子。

**编辑 YAML，保存，即刻生效。** 文件监听器检测到变更，注册表自动更新。无需重启，无需重新编译，无需部署。

这意味着你的 Git 仓库**就是**你的 Agent 配置。`git diff` 能看出你修改了哪个工具。`git log` 能追溯谁在什么时候改了什么。Code review、CI/CD、回滚——这些 Git 原生能力自然延伸到了 Agent 开发中。

---

## 最特别的能力：Agent 自己会写代码

ARF 与其他框架最大的不同，是 Agent **能够在对话中自己创建新的工具和技能**。

举个例子。你跟 Agent 说：

> "我需要一个能查询公司内部 CRM 的工具，API 地址是 https://crm.internal/api/v2，需要 Bearer Token 认证。"

Agent 会这样做：

1. 用 `resource_scaffold` 在 `tools/crm_query/` 下生成 `tool.yaml` 和 `function.py` 的骨架
2. 用 `file_writer` 把 API 调用逻辑写入 `function.py`
3. 热重载机制自动检测到新文件，注册新的 `crm_query` 工具
4. **在这次对话中，Agent 就可以直接使用这个刚创建的工具了**

一次聊天对话，产生了一个**永久能力**。下次打开新会话，这个工具依然在——它是你工作区里的一个普通目录，Git 可以追踪，你可以手动修改，Agent 也可以继续迭代它。

这种"对话即开发"的模式，让 Agent 从一个"执行者"变成了一个**能够不断增强自己的开发者**。你今天教会它的东西，明天它还记得。

---

## 渐进式能力披露：每次 API 调用只付该付的 token

如果你用过 LangChain，你会知道一个痛点：定义了多少工具，它们就**全部**塞进每一次的 system prompt 里。你有 50 个工具？每次 API 调用的 prompt 里都有 50 个工具定义。

ARF 采用了三层能力披露：

```
┌─────────────────────────────────┐
│  技能层（按需加载）               │  ← 用户/Agent 调用时才加载
│  提示词模板 + 工具编排            │
├─────────────────────────────────┤
│  可发现层（按需激活，用完即停）     │  ← resource_loader 动态管理
│  文件搜索、批处理、代码分析...     │
├─────────────────────────────────┤
│  内核层（始终激活）               │  ← 9 个工具，~800 token
│  文件操作、资源加载、记忆管理...   │
└─────────────────────────────────┘
```

初始 system prompt 只有约 **800 个 token**。当你需要用到某个高级工具时，Agent 通过 `resource_loader` 按需激活它，用完之后可以停用释放上下文。

对比一下：LangChain 的典型用法可能每个请求携带 3000–8000 token 的工具定义。对 DeepSeek 这样按 token 计费的 API 来说，ARF 的节省是显著的——你可能只为 5% 的实际使用的工具付费，而不是 100%。

---

## 执行层与控制层彻底解耦

ARF 的架构分为两层：

**控制层（WHAT）**——纯粹的声明层。YAML 文件描述：存在什么资源、它们的输入输出 Schema、在什么情况下使用。人类可读，Git 可追踪，没有一行代码。

**执行层（HOW）**——纯粹的逻辑层。LangGraph StateGraph 引擎在运行时动态加载函数，按需调用。引擎对具体资源完全无感——它只知道"我需要执行一个工具"，而不知道这个工具是从文件系统哪个目录加载的，是系统内置的还是用户自定义的。

两层之间的**唯一桥梁是文件系统**：

```
控制层（YAML） ──── 文件系统 ──── 执行层（LangGraph）
```

这不是一个技术细节，而是一个**架构承诺**：修改 Agent 的行为不需要穿越任何抽象层。找到那个 YAML 文件，改掉一行，保存。结束。没有 API，没有管理后台，不需要 SDK。

---

## 真实可用的功能

ARF 不是一个概念验证。当前已经实现：

- **Agent 引擎**：完整的 LangGraph StateGraph 实现，classify → call_model → execute_tools/respond → recovery 循环
- **模型路由**：三级分类器（simple/medium/complex），根据任务复杂度自动选择模型，支持自动降级
- **API 服务器**：FastAPI + WebSocket + SSE 流式传输
- **前端界面**：Vue 3 + TypeScript + Vite，8 个视图、13 个组件，6400+ 行代码
- **可观测性**：SQLite Trace 数据库（6 张表），瀑布图可视化每次调用的完整链路
- **记忆系统**：三层文件记忆——会话记忆（短期上下文）、长期记忆（用户画像）、归档记忆（历史会话）。直接用 grep 搜索记忆，用 rsync 备份，完全透明
- **Hook 引擎**：6 个生命周期事件（SessionStart、PreModelCall、PostToolUse、SessionEnd 等），基于子进程的 Hook 执行，退出码契约。Hook 崩溃不会拖垮 Agent
- **热重载**：文件监听器检测资源变更，注册表自动更新，无需重启
- **Docker 支持**：多阶段 Dockerfile + docker-compose，三分钟部署

---

## 快速体验

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

arf init my_workspace
arf start --workspace my_workspace
```

浏览器打开 `http://localhost:5173`，输入 DeepSeek API 密钥，开始对话。

如果你用 Docker：

```bash
docker-compose up -d
```

---

## 与其他方案的对比

| | ARF | LangChain | Dify | 裸 FastAPI + SDK |
|---|---|---|---|---|
| **方法论** | 工作区即代码 | 类库 | 低代码平台 | 自己动手 |
| **Agent 自演进** | ✅ 运行时创建资源 | ❌ 手动 | 部分（插件商店） | ❌ |
| **热重载** | ✅ 内置文件监听 | ❌ | 部分 | ❌ |
| **系统提示词** | ~800 token | 3000–8000+ | 视配置 | 视实现 |
| **Trace 追踪** | ✅ SQLite + 瀑布图 | LangSmith（付费） | 内置 | 自己写 |
| **前端** | ✅ Vue 3 内置 | 无（LangServe） | React | 自己写 |
| **记忆系统** | ✅ 三层文件记忆 | 有限 | 有限 | 自己写 |
| **Hook 系统** | ✅ 6 事件子进程 | Callbacks | 有限 | 自己写 |
| **Git 原生** | ✅ 完全 | 部分（代码层面） | ❌（数据库） | ✅ |
| **自托管** | ✅ 单进程 | ✅ | ✅（Docker） | ✅ |
| **开源协议** | MIT | MIT | Apache 2 | N/A |

---

## 这个项目的取舍

ARF 有明确的取舍。这些不是"还没做"，而是**刻意不做**：

- **不做多供应商抽象层**。ARF 使用 OpenAI 兼容 API。配置好 `base_url`，直接用。没有 provider-specific wrapper，没有 adapter pattern，没有 plugin registry。
- **不做云 SaaS 服务**。自托管是默认设计。无托管服务，无遥测，无账户系统。
- **子进程 Hook，而非进程内回调**。Hook 作为独立进程运行，有自己的超时、环境和故障域。一个崩溃的 Hook 不会拖垮 Agent。退出码契约（0/1/2）是语言无关的——用 Python 写也行，用 bash 写也行。

---

## 欢迎参与

ARF 还很年轻，有大量值得做的事情：

- **P3**：Runtime 权限控制模块
- **P3**：工具审批流程——敏感工具调用需用户介入确认
- **P3**：MCP（Model Context Protocol）支持
- **P3**：插件/扩展系统

如果你对这些方向感兴趣，或者你有更好的想法，欢迎来提 Issue 或 PR。

> GitHub: [https://github.com/Wang-hubber/open_deepseek_arf](https://github.com/Wang-hubber/open_deepseek_arf)
> Gitee: [https://gitee.com/dalaydata/open_deepseek_arf](https://gitee.com/dalaydata/open_deepseek_arf)

如果这个项目对你有启发，给个 Star 是对我们最好的鼓励。

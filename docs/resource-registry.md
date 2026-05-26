# Resource Registration & Discovery — 文件系统即真相源

ARF 将资源（工具/技能/模型）的注册与发现视为操作系统中的注册表与服务管理器——约定优于配置，文件系统是真相源，内核只读、动态热加载。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理资源注册与动态发现，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 Windows 注册表 — 集中式资源数据库

**问题**：应用程序安装后，OS 如何知道它能打开哪些文件类型、注册了哪些 COM 组件、提供了哪些系统服务？

**INI 文件时代**（Windows 3.x）：每个应用在自己的 `.ini` 文件中写配置。全局信息散落在 `win.ini` 和 `system.ini` 中，冲突靠运气。没有查询接口，没有层次结构，没有事务保证。

**注册表**（Windows 95，1995）引入了集中式层次数据库——六个根键（`HKEY_CLASSES_ROOT`、`HKEY_CURRENT_USER`、`HKEY_LOCAL_MACHINE`、`HKEY_USERS`、`HKEY_CURRENT_CONFIG`、`HKEY_PERFORMANCE_DATA`），将文件关联、COM 类注册、驱动配置、用户偏好统一到一棵树中。任何程序都可以通过 `RegOpenKeyEx`/`RegQueryValueEx` API 查询已注册的资源。

**注册表的架构要素**：
- **层次命名空间**：`HKLM\SOFTWARE\Classes\` 下注册所有文件类型和 COM 组件
- **类型化值**：REG_SZ、REG_DWORD、REG_BINARY 等强类型字段
- **事务性**：`RegSaveKey`/`RegRestoreKey` 支持原子备份和恢复
- **ACL 保护**：每个键有独立的访问控制列表

**注册表的问题**：随着时间推移，注册表膨胀为巨大的、难以管理的单体。卸载残留、孤儿键、版本冲突成为常态。这直接启示了 ARF 的文件系统即真相源方案——每个资源是独立文件，删除即卸载，无残留。

**Windows 现代替代**：AppX 清单（UWP 应用）和 MSIX 包声明将注册信息从注册表移回应用包内的声明文件——这正是 ARF 采取的方案：声明文件与实现文件放在同一个目录下。

### 1.2 systemd/launchd — 服务注册与生命周期

**问题**：OS 如何知道系统启动后应该启动哪些服务、服务之间的依赖关系、以及服务崩溃后如何处理？

**SysV init**：`/etc/rc.d/` 下按运行级别排列的 shell 脚本。无依赖管理，无并行启动，无自动重启。服务定义和执行逻辑混在脚本里。

**systemd**（Linux，2010）：将服务定义从执行逻辑中分离出来。每个服务一个声明文件（`/etc/systemd/system/` 下的 `.service` 单元），描述可执行文件路径、依赖关系、重启策略、资源限制。`systemctl` 查询和管理服务状态。`systemctl daemon-reload` 重新扫描单元文件目录——这正是 ARF `reload_dynamic()` 的 OS 等价操作。

| systemd | ARF |
|---------|-----|
| `.service` 单元文件声明服务 | `tool.yaml` / `skill.yaml` / `model.yaml` 声明资源 |
| `systemctl daemon-reload` 重新扫描 | `reload_dynamic()` 清空 dynamic 缓存 |
| `systemctl list-units` 列出已加载服务 | `generate_config()` dump 全部资源 |
| `systemctl enable/disable` 控制激活状态 | `activation: kernel / discoverable` |
| Type=oneshot/simple/notify 控制启动行为 | `backend: function / subprocess` 控制执行方式 |

**launchd**（macOS，2005）：与 systemd 同代诞生，同样使用声明式 plist 文件（`~/Library/LaunchAgents/`、`/Library/LaunchDaemons/`），同样支持按需启动（`KeepAlive`、`RunAtLoad`）、资源限制和依赖声明。

### 1.3 udev — 动态设备发现与规则驱动

**问题**：USB 设备热插拔时，OS 如何识别它、加载正确的驱动、并以可预测的设备文件名呈现？

**devfs**（Linux 2.4）：内核静态维护 `/dev` 目录。所有可能设备的节点预先创建（即使设备不存在），无热插拔支持，命名策略硬编码。

**udev**（Linux 2.6，2003）将设备管理从内核态移到用户态。内核通过 netlink socket 发送 uevent（`add`/`remove`/`change`），用户空间的 `udevd` 守护进程监听事件，根据规则文件（`/etc/udev/rules.d/`）匹配设备属性并执行操作——加载驱动、创建设备节点、设置权限、触发用户脚本。

这直接映射到 ARF 的 FileWatcher 设计：

| udev | ARF |
|------|-----|
| netlink uevent (`add`/`remove`/`change`) | inotify 事件 (`IN_CREATE`/`IN_DELETE`/`IN_CLOSE_WRITE`) |
| `udevd` 守护进程监听 | `FileWatcher` 的 `_inotify_loop` / `_poll_loop` |
| `/etc/udev/rules.d/` 规则文件 | `tool.yaml` + `function.py` 约定目录结构 |
| 规则匹配 → 加载驱动 | 发现变更 → `invalidate_dynamic()` → 惰性重扫描 |

### 1.4 包管理器数据库 — 已安装资源清单

**问题**：OS 如何知道系统上安装了哪些软件包、它们的版本、文件清单和依赖关系？

**dpkg/APT**（Debian）：`/var/lib/dpkg/status` 是一个结构化文本文件，记录每个已安装包的名称、版本、依赖、描述、安装状态。`dpkg -l` 查询，`apt-cache` 搜索可安装但未安装的包。

**RPM/yum**（Red Hat）：`/var/lib/rpm/` 下的 Berkeley DB 数据库，记录每个包的文件校验和、GPG 签名、安装脚本。

**关键设计选择**：包管理器不依赖注册表。每个包的元数据与包文件放在一起（`.deb` 内的 `control` 文件），安装时写入本地数据库，卸载时清理。文件系统是可审计的真相源——`dpkg -S /usr/bin/foo` 可以追溯任意文件到其所属的包。

这直接对应 ARF 的设计：每个资源的声明与实现同目录存放（`tools/{name}/tool.yaml` + `function.py`），资源清单通过文件系统扫描动态生成，删除目录即卸载，无残留。

### 1.5 对 ARF 的启发

Windows 注册表从集中式单体（Windows 95）演进到声明式清单（AppX/MSIX），验证了"文件系统即数据库"优于"单体注册表"的架构选择。systemd 证明了声明式配置 + 按需加载 + 守护进程监控的可行性。udev 证明了基于事件的文件系统监听可以实现亚秒级的热插拔响应。包管理器证明了每个资源自带完整元数据后，集中式注册表是完全多余的。

ARF 的资源注册与发现综合了这四个思想：注册表的集中查询 → `ResourceResolver.generate_config()`；systemd 的声明分离 + `daemon-reload` → `tool.yaml` 约定 + `reload_dynamic()`；udev 的事件驱动发现 → `FileWatcher`；包管理器的文件系统审计 → 目录结构即资源清单。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
文件系统变更（写入 / 删除 / 重命名）
    │
    ▼
FileWatcher（inotify / 轮询） —— 对标 udev
    │
    ▼
ResourceResolver.reload_dynamic() —— 对标 systemctl daemon-reload
    │
    ├─ ToolProvider.invalidate_dynamic()
    ├─ SkillProvider.invalidate_dynamic()
    └─ ModelProvider.invalidate_dynamic()
           │
           ▼
    ResourceCache.dynamic.clear()
           │
           ▼
    下一轮 Engine 调用 get_tool_definitions()
           │
           ▼
    Provider._load_all() 惰性重新扫描文件系统
           │
           ├─ kernel（永不重扫，冻结只读）
           └─ dynamic（重新加载，应用 agent.yaml 覆盖）
```

四个层级——监听、解析、缓存、合并——协同工作：

| 层级 | 组件 | OS 对标 | 职责 |
|------|------|---------|------|
| **监听层** | `FileWatcher` | udev | inotify/轮询双轨检测 tools/skills/models 目录下的文件变更 |
| **解析层** | `ToolProvider` / `SkillProvider` / `ModelProvider` | 规则文件匹配 | 扫描各自目录，解析 YAML，`importlib` 动态加载 function.py |
| **缓存层** | `ResourceCache`（kernel + dynamic 分离） | systemd unit 缓存 | kernel 冻结不可变，dynamic 可失效重载 |
| **合并层** | `ResourceResolver` | 注册表查询 | 合并文件系统定义与 agent.yaml 覆盖，dump 完整配置 |

### 2.2 三个 Provider

每个 Provider 遵循相同接口：`list_kernel()` / `list_dynamic()` / `invalidate_dynamic()`。

**ToolProvider**（`arf/resources/providers/tool_provider.py`）扫描 `tools/{name}/` 目录。每个工具 = `tool.yaml`（Schema）+ `function.py`（逻辑）。`importlib.util.spec_from_file_location` 动态导入 `execute` 函数，执行走 `FunctionBackend`。

**SkillProvider**（`arf/resources/providers/skill_provider.py`）扫描 `skills/*.yaml`。每个文件一个 SkillConfig。纯声明——无函数加载。

**ModelProvider**（`arf/resources/providers/model_provider.py`）扫描 `models/*.yaml`。每个文件一个 ModelConfig。`activation` 字段用于内核/动态分离。

三者并行启动，互不依赖。所有 Provider 接受 `fs_root` 参数，默认 `./`，可在测试中覆盖。

### 2.3 内核/动态分离

`activation: kernel` 标记框架内置资源——对标编译进内核的驱动（`[*]` in menuconfig）或 systemd 的 `WantedBy=multi-user.target` 默认服务。工具如 `file_reader`、`web_search`，技能如 `code_review`、`debug`，模型如 `quick`、`deep`。这些在 BaseAgent 初始化时加载，之后冻结。

```python
# arf/resources/cache.py
class _FrozenDict(dict):
    """对标 systemd 的静态单元缓存——init 时加载，之后不可变。"""
    def __setitem__(self, key, value):
        if self._frozen:
            raise RuntimeError("kernel cache is frozen — cannot modify after init")
        super().__setitem__(key, value)
    # __delitem__ / pop / popitem / clear 同受冻结约束
```

冻结后的内核缓存拒绝任何修改。动态资源（`activation: discoverable`）对标 systemd 的用户单元（`systemctl --user`），可随时刷新：

| 触发方式 | OS 对标 | 生效时机 |
|----------|---------|---------|
| 直接编辑文件 | udev 检测设备热插拔 | 下一轮引擎调用 |
| `resource_scaffold` 创建 | 包管理器安装 .deb → `dpkg --install` | 下一轮引擎调用 |
| `POST /api/resources/reload` | `systemctl daemon-reload` | 即时清缓，下一轮使用新数据 |

### 2.4 ResourceResolver — 覆盖合并与配置生成

`ResourceResolver`（`arf/resources/resolver.py`）封装三个 Provider，是所有资源查询的统一入口——对标注册表的查询 API。

```
优先级：agent.yaml 覆盖 > 文件系统字段 > Pydantic 默认值
```

同名资源合并规则：文件系统是基值，agent.yaml 的同名字段覆盖（浅层）。agent.yaml 可声明文件系统不存在的资源（对标注册表中手动添加的键值，追加到合并结果）。两种来源都空默认返回空列表。

```python
class ResourceResolver:
    def __init__(self, tool_provider, skill_provider, model_provider, agent_yaml_overrides):
        ...

    async def get_tool_definitions(self, ...) -> list[ToolDefinition]:
        tools = self._tool_provider.list_tools()
        return self._merge_configs(tools, overrides, ToolConfig)

    async def reload_dynamic(self):
        """对标 systemctl daemon-reload。"""
        self._tool_provider.invalidate_dynamic()
        self._skill_provider.invalidate_dynamic()
        self._model_provider.invalidate_dynamic()

    def generate_config(self) -> dict:
        """对标 regedit /export 或 dpkg -l。扫描文件系统 dump 完整 agent.yaml。"""
```

向后兼容：`DefaultToolResolver = ResourceResolver` 别名保留旧接口。

### 2.5 FileWatcher — 跨平台自动重载

`FileWatcher`（`arf/resources/file_watcher.py`）是对标 udev 的资源变更检测器，双轨实现：

- **Linux**：ctypes 调用 `inotify_init()` / `inotify_add_watch()`。`select.select` 异步等待。一次读取 4096 字节缓冲区，逐事件解析（wd + mask + cookie + len + name[aligned]）。监听 `IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE`。
- **非 Linux**：`asyncio.sleep` 轮询 `os.stat` mtime 快照对比。

内嵌回退：`inotify_init()` 失败时静默切换轮询。`add_watch()` 在 FileWatcher 已启动后动态注册新目录的 inotify 监听。

变更检测 → `_seed_mtimes()` 快照更新 → 异步回调 `ResourceResolver.reload_dynamic()` → 所有 Provider dynamic 缓存清空 → `_loaded = False` 标记，下一轮惰性重扫描。

### 2.6 配置

agent.yaml 资源段现为可选——省略时完全由文件系统决定。声明时提供字段级覆盖：

```yaml
# 文件系统定义（models/quick.yaml）—— 对标 .service 单元文件
type: quick
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 800000
activation: kernel
kwargs:
  reasoning_effort: high
  temperature: 0.7

# agent.yaml 覆盖（可选单字段微调）—— 对标 systemctl edit 的 drop-in 片段
models:
  - type: quick
    temperature: 0.3  # 仅覆盖此字段，其余保持文件系统值
```

```yaml
advanced:
  reload:
    watch: true          # 启用 FileWatcher（默认 true）
    poll_interval: 5     # 轮询间隔秒数（非 Linux 平台）
```

---

## 3. 演进方向

### 3.1 层次化覆盖合并

当前合并逻辑是浅层 `model_copy(update=override_dict)`——覆盖只影响顶层字段。对于嵌套结构（`kwargs`、`pipeline`），文件的整组值被覆盖而非递归合并。

**目标**：三层优先级——框架默认 < 应用 agent.yaml < per-agent 覆盖。嵌套字段递归合并而非全量替换。对标 systemd 的 drop-in 机制（`systemctl edit` 创建的 `/etc/systemd/system/foo.service.d/override.conf`）。覆盖继承链可追溯。

### 3.2 MCP 多源 Provider

三个 Provider 的文件系统扫描架构天然支持多源扩展。增加 `MCPToolProvider` 后，来自 MCP 服务器的工具与本地文件系统的工具共享同一缓存/失效契约——对标 Windows 注册表中来自不同安装源的程序条目共用同一个查询 API。引擎层守卫检查（PathCheckToolGuard、权限分级）自动覆盖所有来源。

### 3.3 探索性方向

- **资源版本控制与 schema 迁移**：`tool.yaml` 增加 `schema_version`，对标包管理器的版本追踪。Provider 在加载时自动迁移旧格式——对标 `apt upgrade` 时的数据库 schema 升级
- **交叉引用验证**：Skill 引用的 Tool 是否存在，Model 的 `api_key_env` 是否声明——对标 `dpkg` 的依赖解析和 `systemd` 的 `Requires=` 检查
- **按使用模式自动卸载**：长时间未使用的 discoverable 资源从 dynamic 缓存卸载——对标 systemd 的 `StopWhenUnneeded=yes`
- **per-resource 资源配额**：每个 Tool/Skill 可声明最大并发调用数、超时、token 预算——对标 systemd 的 `CPUQuota=`、`MemoryMax=` 和 cgroup 资源限制
- **资源变更事件**：FileWatcher 检测变更后通过 EventBus 发布 `resource_changed` 事件，前端实时刷新——对标 udev 的 uevent 广播

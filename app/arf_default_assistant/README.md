# ARF Default Assistant

基于 `arf/` 框架的双 Agent 自演进助手。

## 快速开始

```bash
# 1. 克隆
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活 (选一个)
#    Windows PowerShell:
.venv\Scripts\activate
#    Windows CMD:
.venv\Scripts\activate.bat
#    Linux/Mac:
source .venv/bin/activate

# 4. 安装
pip install -e ".[dev]"

# 5. 设置 API Key (选一个)
#    Windows PowerShell:
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxx"  # 注意：值必须加引号
#    Windows CMD:
set DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
#    Linux/Mac:
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 6. 环境验证
cd app\arf_default_assistant    # (Linux: cd app/arf_default_assistant)
python test_setup.py

# 7. 启动服务
python cli.py start
# 打开 http://127.0.0.1:8000/docs 查看 API 文档
```

## CLI 命令

| 命令 | 用途 |
|------|------|
| `python cli.py init` | 初始化工作区 |
| `python cli.py start` | 启动 server + 前端 |
| `python cli.py chat "hello"` | 终端对话 |
| `python cli.py stop` | 停止服务 |
| `python cli.py list tools` | 列出工具 |
| `python cli.py validate` | 校验配置 |

## 目录结构

```
app/arf_default_assistant/
├── agent.yaml          # 双 Agent 配置
├── cli.py              # CLI 入口
├── server.py           # FastAPI 服务
├── lazy_persistence.py # 存档/恢复
├── test_setup.py       # 环境验证脚本
├── tools/              # 15 个工具
├── skills/             # 9 个技能
├── hooks/              # 自演进钩子
└── memory/             # 持久化存档
```

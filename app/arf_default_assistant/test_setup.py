#!/usr/bin/env python3
r"""ARF Assistant — 环境验证脚本 | Environment verification script

Windows (PowerShell) 测试流程:
  1. git clone git@gitee.com:dalaydata/open_deepseek_arf.git
  2. cd open_deepseek_arf
  3. python -m venv .venv
  4. .venv\Scripts\activate
  5. pip install -e ".[dev]"
  6. $env:DEEPSEEK_API_KEY = "sk-xxx"
  7. cd app\arf_default_assistant
  8. python test_setup.py          <-- run this script
  9. python cli.py start           <-- start server
 10. python cli.py chat "hello"    <-- test chat

Linux/Mac 测试流程:
  1. git clone git@gitee.com:dalaydata/open_deepseek_arf.git
  2. cd open_deepseek_arf
  3. python -m venv .venv
  4. source .venv/bin/activate
  5. pip install -e ".[dev]"
  6. export DEEPSEEK_API_KEY=sk-xxx
  7. cd app/arf_default_assistant
  8. python test_setup.py          <-- run this script
  9. python cli.py start           <-- start server
 10. python cli.py chat "hello"    <-- test chat
"""

# (Note: PowerShell uses $env:VAR = "value", CMD uses set VAR=value.
#  This script checks os.environ, which both set correctly.)

import sys
import os
from pathlib import Path


def green(s): return f"\033[32m{s}\033[0m"
def red(s): return f"\033[31m{s}\033[0m"
def bold(s): return f"\033[1m{s}\033[0m"


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = green("PASS") if ok else red("FAIL")
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    print(bold("\nARF Assistant — 环境验证 | Environment Check"))
    print("=" * 50)
    all_ok = True

    # 1. Python version
    v = sys.version_info
    all_ok &= check("Python >= 3.11", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}")

    # 2. DEEPSEEK_API_KEY
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    all_ok &= check("DEEPSEEK_API_KEY set", bool(key),
                    "OK" if key else "未设置 | PowerShell: $env:DEEPSEEK_API_KEY = \"sk-xxx\" | CMD: set DEEPSEEK_API_KEY=sk-xxx | Linux: export DEEPSEEK_API_KEY=sk-xxx")

    # 3. Framework imports
    try:
        from arf.core import AgentEvent, AgentState, ModelConfig
        check("arf.core import", True)
    except Exception as e:
        all_ok &= check("arf.core import", False, str(e))

    try:
        from arf.agent import AgentConfig, BaseAgent, create_agent
        check("arf.agent import", True)
    except Exception as e:
        all_ok &= check("arf.agent import", False, str(e))

    # 4. Agent config loading
    try:
        cfg = AgentConfig.from_yaml("agent.yaml")
        check("agent.yaml load", True, f"{cfg.name}: {len(cfg.tools)} tools, {len(cfg.models)} models")
    except Exception as e:
        all_ok &= check("agent.yaml load", False, str(e))

    # 5. Tool verification
    tools_dir = Path("tools")
    tool_dirs = [d for d in tools_dir.iterdir() if d.is_dir()] if tools_dir.exists() else []
    for td in tool_dirs:
        has_yaml = (td / "tool.yaml").exists()
        has_fn = (td / "function.py").exists()
        check(f"tool/{td.name}", has_yaml and has_fn,
              "OK" if (has_yaml and has_fn) else "missing yaml" if not has_yaml else "missing function.py")

    # 6. Skills
    skills_dir = Path("skills")
    skills = list(skills_dir.glob("*.yaml")) if skills_dir.exists() else []
    check(f"Skills", len(skills) > 0, f"{len(skills)} YAML files")

    # 7. Server import
    try:
        sys.path.insert(0, ".")
        from server import app
        check("server.py import", True)
    except Exception as e:
        all_ok &= check("server.py import", False, str(e))

    # 8. Workspace dir
    ws = Path("workspaces/default")
    ws_exists = ws.exists()
    if not ws_exists:
        ws.mkdir(parents=True, exist_ok=True)
    check("workspaces/default/", ws.exists() or ws_exists, str(ws.resolve()))

    print("=" * 50)
    if all_ok:
        print(bold(green("全部通过 | ALL CHECKS PASSED")))
        print("\n下一步 | Next steps:")
        print("  python cli.py start          ← 启动 server + 前端")
        print("  python cli.py chat \"hello\"   ← 终端对话")
        print("  http://127.0.0.1:8000/docs   ← Swagger API 文档")
    else:
        print(bold(red("有失败项 | SOME CHECKS FAILED — 请检查上方红色标记")))
        sys.exit(1)


if __name__ == "__main__":
    main()

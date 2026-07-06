"""Task 18e — Subagent delegation e2e."""

import time

import httpx
import pytest


def test_pm_delegates_to_tool_creator(live_server: str, e2e_guard):
    """POST /delegate/tool_creator_pool → subagent runs task → result returned."""
    with httpx.Client(base_url=live_server, timeout=180) as c:
        r = c.post(
            "/delegate/tool_creator_pool",
            json={
                "message": (
                    "请帮我实现一个把 CSV 转成 JSON 行的工具，"
                    "逻辑用伪代码说明即可，不需要真的写文件。"
                )
            },
        )
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert "output" in result, "TaskResult 应含 output 字段"
        assert result["output"], "output 应非空"

        # 等事件写盘
        time.sleep(2.0)

        # 验证 team rollup 至少 1 个 engine 有 model_calls
        team = c.get("/stats/team/default").json()
        assert team["total_model_calls"] >= 1, (
            f"team 应至少 1 次 model_call，实际 {team['total_model_calls']}"
        )
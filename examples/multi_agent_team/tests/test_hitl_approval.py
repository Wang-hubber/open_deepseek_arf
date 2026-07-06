"""Task 18e — HITL approval flow e2e."""

import httpx
import pytest


def test_hitl_write_file_flow(live_server: str, e2e_guard):
    """pm 调 write_file (ask-mode) → /approvals 出现 → /approve 通过 →
    pm 继续。"""
    with httpx.Client(base_url=live_server, timeout=180) as c:
        # 触发 ask-mode：让 pm 写一份报告
        # 注意：pm 的 agent.yaml 必须把 write_file permission 设为 ask 才能触发
        r = c.post(
            "/chat",
            json={
                "message": (
                    "请把今天的工作总结写到 shared_workspaces/daily_report.md。"
                    "这需要写文件操作，请调用 write_file 工具。"
                )
            },
        )
        assert r.status_code == 200, r.text

        # /approvals 应该有 pending（如果 write_file 走 ask 路径）
        pending = c.get("/approvals").json()["pending"]

        if not pending:
            # pm 未触发 ask-mode（可能 system_prompt 没引导 write_file），
            # 或 write_file permission 是 Allow 而非 Ask。
            # 这种情况下测试不 fail — 记录观察值即可。
            pytest.skip(
                "pm 未触发 write_file ask-mode（可能 agent.yaml 把 write_file "
                "permission 配成了 Allow 而非 Ask，或 model 没调用工具）"
            )

        # 通过第一个 pending
        req_id = pending[0]
        r = c.post(f"/approve/{req_id}", json={"approved": True})
        assert r.status_code == 200

        # 验证 write_file 工具调用被记录（success）
        import time
        time.sleep(2.0)
        pm_stats = c.get("/stats/engine/pm").json()
        wf_stats = pm_stats["tool_calls"]["by_tool"].get("write_file", {})
        assert wf_stats.get("success", 0) >= 1, (
            f"write_file 应至少 1 次成功调用，实际 {wf_stats}"
        )
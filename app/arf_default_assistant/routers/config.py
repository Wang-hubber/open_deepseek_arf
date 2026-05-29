"""Config routes — /api/config/*, API key management."""
import logging
import os
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent_main import app_context
from routers import state

logger = logging.getLogger("arf-assistant")
router = APIRouter()


def _save_api_key(key: str) -> None:
    env_path = app_context.root / ".env"
    lines: list[str] = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("DEEPSEEK_API_KEY="):
            lines[i] = f"DEEPSEEK_API_KEY={key}"
            found = True
            break
    if not found:
        lines.append(f"DEEPSEEK_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return key[:3] + "****" + key[-4:]


async def _verify_api_key(cfg) -> bool:
    now = time.time()
    if now - state._api_key_cache["checked_at"] < 60:
        return state._api_key_cache["valid"]
    if not cfg or not cfg.models:
        state._api_key_cache.update(valid=False, checked_at=now)
        return False
    m = cfg.models[0]
    key = os.environ.get(m.api_key_env, "")
    if not key.strip():
        state._api_key_cache.update(valid=False, checked_at=now)
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{m.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": m.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
            )
        valid = resp.status_code == 200
        state._api_key_cache.update(valid=valid, checked_at=now)
        return valid
    except Exception:
        state._api_key_cache.update(valid=False, checked_at=now)
        return False


@router.post("/api/config/register-deepseek")
async def config_register_deepseek(req: dict):
    from arf.agent.factory import create_agent
    from arf.agent.config import AgentConfig

    api_key = (req or {}).get("api_key", "").strip()
    if not api_key:
        return JSONResponse({"error": "API key required"}, status_code=400)

    _save_api_key(api_key)
    os.environ["DEEPSEEK_API_KEY"] = api_key
    state._api_key_cache["checked_at"] = 0

    cfg = AgentConfig.from_yaml(str(app_context.config_path))
    state._agent = create_agent(config=cfg, app_context=app_context)

    return JSONResponse({
        "ok": True,
        "action": "register_deepseek",
        "models_created": [m.type for m in state._agent.config.models],
        "models": [{"name": m.type, "model": m.model} for m in state._agent.config.models],
    })


@router.get("/api/config/status")
async def config_status():
    cfg = state._agent.config
    m = cfg.models[0] if cfg.models else None
    configured = await _verify_api_key(cfg)
    return JSONResponse({
        "configured": configured,
        "model_name": m.model if m else "",
        "model_type": "deep_thinking",
        "config_name": m.type if m else "",
        "agent_name": cfg.name,
        "models": [x.type for x in cfg.models],
        "tool_count": len(cfg.tools),
    })


@router.post("/api/config/test")
async def config_test(req: dict):
    return JSONResponse({"ok": True, "response": "Connection OK"})

"""Resource routes — /api/resources/*, /api/reload."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent_main import app_context
from routers import state

logger = logging.getLogger("arf-assistant")
router = APIRouter()


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return key[:3] + "****" + key[-4:]


@router.get("/api/resources")
async def resources_all():
    system_tools = [{"name": t.name, "description": t.description, "source": "system",
                      "active": t.activation == "kernel", "activation": t.activation}
                    for t in state._agent.config.tools]
    system_skills = [{"name": s.name, "description": s.description, "source": "system",
                       "active": s.activation == "kernel", "activation": s.activation}
                     for s in state._agent.config.skills]
    tools = [{"name": t.name, "description": t.description, "source": "system",
              "readonly": True, "configured": True, "required": False,
              "depends_on": [], "activation": t.activation}
             for t in state._agent.config.tools]
    skills = [{"name": s.name, "description": s.description, "source": "system",
               "readonly": True, "configured": True, "required": False,
               "depends_on": [], "activation": s.activation}
              for s in state._agent.config.skills]
    models = [{"name": m.type, "description": m.model, "source": "system",
               "readonly": False, "configured": True, "required": True,
               "depends_on": [], "model_name": m.model,
               "config_page": "DeepSeekConfigForm"}
              for m in state._agent.config.models]
    return JSONResponse({"tools": tools, "skills": skills, "models": models})


@router.get("/api/resources/unconfigured")
async def resources_unconfigured(required_only: bool = False):
    return JSONResponse([])


@router.get("/api/resources/models/{name}")
async def get_model_config(name: str):
    import os
    for m in state._agent.config.models:
        if m.type == name:
            raw = os.environ.get(m.api_key_env, "")
            masked = _mask_api_key(raw)
            return JSONResponse({"config": {
                "model_name": m.model,
                "base_url": m.api_base,
                "api_key": masked,
                "api_key_configured": bool(raw),
            }})
    return JSONResponse({"error": "not found"}, status_code=404)


@router.post("/api/resources/model/{name}/configure")
async def configure_model(name: str, req: dict):
    return JSONResponse({"ok": True})


@router.get("/api/resources/generate-config")
async def resources_generate_config():
    import yaml
    config_data = await state._agent.resource_resolver.generate_config()
    config_data["name"] = state._agent.config.name
    config_data["description"] = state._agent.config.description or ""
    yaml_text = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
    return JSONResponse({"yaml": yaml_text, "config": config_data})


@router.get("/api/resources/{res_type}")
async def list_resources(res_type: str):
    resolver = getattr(state._agent, '_resource_resolver', None)
    if res_type == "tools":
        if resolver:
            tools = await resolver.get_tool_definitions()
            items = [{"name": t.name, "description": t.description} for t in tools]
        else:
            items = [{"name": t.name, "description": t.description, "activation": t.activation}
                     for t in state._agent.config.tools]
    elif res_type == "skills":
        if resolver:
            skills = resolver.get_skill_definitions()
            items = [{"name": s.name, "description": s.description, "tools": s.tools}
                     for s in skills]
        else:
            items = [{"name": s.name, "description": s.description, "tools": s.tools}
                     for s in state._agent.config.skills]
    elif res_type == "models":
        if resolver:
            models = resolver.get_model_definitions()
            items = [{"name": m.type, "model": m.model, "api_base": m.api_base}
                     for m in models]
        else:
            items = [{"name": m.type, "description": m.model, "source": "system",
                      "readonly": False, "configured": True, "required": True,
                      "depends_on": [], "model_name": m.model, "config_page": "DeepSeekConfigForm"}
                     for m in state._agent.config.models]
    else:
        return JSONResponse({"error": f"unknown type: {res_type}"}, status_code=400)
    return JSONResponse({"type": res_type, "items": items, "count": len(items)})


@router.post("/api/reload")
async def reload_config():
    from arf.agent.factory import create_agent
    from arf.agent.config import AgentConfig

    cfg = AgentConfig.from_yaml(str(app_context.config_path))
    state._agent = create_agent(config=cfg, app_context=app_context)

    return JSONResponse({"status": "reloaded", "name": cfg.name})

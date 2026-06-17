"""use_skill — kernel tool that loads a Skill's full body on demand."""
import logging
from arf.skills.skill_index import SkillIndex

logger = logging.getLogger("arf.skills.use_skill")

# Set by ControlPlane during engine initialization
_index: SkillIndex | None = None


async def execute(name: str, **kwargs) -> dict:
    """Load and return the full content of skill *name*.

    Called by the model via tool invocation. The returned body
    enters the message stream as a tool result — the model sees
    it naturally in its next turn.
    """
    global _index
    if _index is None:
        return {"ok": False, "error": "Skill index not initialized"}

    entry = _index.resolve(name)
    if entry is None:
        return {"ok": False, "error": f"skill '{name}' not found"}

    body = _index.load_body(name)
    if body is None:
        return {"ok": False, "error": f"skill '{name}' has no body (skill.md missing)"}

    return {
        "ok": True,
        "skill": {
            "name": entry.name,
            "description": entry.description,
            "body": body,
            "tools_sequence": entry.tools_sequence,
        },
    }

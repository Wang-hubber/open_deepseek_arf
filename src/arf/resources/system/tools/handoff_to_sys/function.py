def execute(intent: str, required_actions: list, reason: str = "") -> dict:
    return {
        "ok": True,
        "handoff": True,
        "intent": intent,
        "required_actions": required_actions,
        "reason": reason,
    }

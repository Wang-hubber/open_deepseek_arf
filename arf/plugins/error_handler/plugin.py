"""ErrorHandler — unified exception recovery with 5 actions."""
import logging
import random
from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.error_handler")


class ErrorHandlerPlugin:
    def __init__(self, config: dict | None = None, **kwargs):
        cfg = dict(config or {})
        cfg.update(kwargs)
        self.max_continuation = cfg.get("max_continuation", 3)
        self.max_compaction = cfg.get("max_compaction", 2)
        self.max_transport_retry = cfg.get("max_transport_retry", 3)
        self.backoff_base = cfg.get("backoff_base", 1.0)
        self.backoff_max = cfg.get("backoff_max", 30.0)

    @property
    def name(self) -> str:
        return "error_handler"

    @property
    def hooks(self) -> dict[str, str]:
        return {"error": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        exc = ctx.hook_data.get("exception")
        if exc is None:
            return

        state = ctx.state
        rs = state.setdefault("_recovery", {
            "transport_attempts": 0,
            "compact_attempts": 0,
            "continuation_attempts": 0,
        })

        error_text = str(exc).lower()
        exc_name = type(exc).__name__

        # 1. Context overflow -> compact
        if "context" in error_text and ("too" in error_text or "exceed" in error_text):
            if rs["compact_attempts"] < self.max_compaction:
                rs["compact_attempts"] += 1
                ctx.hook_data["_recovery_decision"] = {
                    "action": "fallback", "reason": "context too large",
                    "params": {"compact": True},
                }
                return
            # Compaction exhausted → explicit abort (known strategy)
            ctx.hook_data["_recovery_decision"] = {
                "action": "abort", "reason": "compaction attempts exhausted",
            }
            return

        # 2. Transient transport -> backoff + retry
        # Check both error message text and exception type name (asyncio.TimeoutError
        # has an empty str(), so type name is the only signal).
        is_transient = any(
            w in error_text for w in ["timeout", "rate", "unavailable", "connection", "timed out"]
        ) or any(
            w in exc_name.lower() for w in ["timeout", "connection"]
        )
        if is_transient:
            if rs["transport_attempts"] < self.max_transport_retry:
                rs["transport_attempts"] += 1
                delay = min(self.backoff_base * (2 ** rs["transport_attempts"]), self.backoff_max)
                delay += random.uniform(0, 1)
                ctx.hook_data["_recovery_decision"] = {
                    "action": "retry", "reason": "transient transport failure",
                    "params": {"delay": delay, "max_retries": self.max_transport_retry},
                }
                return
            # Transport retries exhausted → explicit abort (known strategy)
            ctx.hook_data["_recovery_decision"] = {
                "action": "abort", "reason": "transport retries exhausted",
            }
            return

        # 3. Message contract violation -> repair (fallback)
        if "MessageContract" in exc_name or "contract" in error_text:
            ctx.hook_data["_recovery_decision"] = {
                "action": "fallback", "reason": "message contract violation",
                "params": {"repair_messages": True},
            }
            return

        # 4. Guard/approval denials -> skip (model sees tool_result, responds)
        if exc_name in ("PermissionDenied", "ApprovalDenied",
                        "SandboxViolation", "ApprovalTimeout"):
            ctx.hook_data["_recovery_decision"] = {"action": "skip"}
            return

        # 5. Unknown error — no recovery strategy.
        # Leave _recovery_decision unset so the engine re-raises
        # the original exception. Do NOT swallow unknown errors as
        # a generic "abort" — the caller needs to see the real error.
        return

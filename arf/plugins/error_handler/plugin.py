"""ErrorHandler — unified exception recovery using `recovery` decisions."""
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

        # 1. Context overflow -> persist_state with compact
        if "context" in error_text and ("too" in error_text or "exceed" in error_text):
            if rs["compact_attempts"] < self.max_compaction:
                rs["compact_attempts"] += 1
                ctx.hook_data["_recovery_decision"] = {
                    "recovery": "persist_state",
                    "reason": "context too large",
                    "params": {"compact": True},
                }
                return
            # Compaction exhausted — leave _recovery_decision unset so the
            # engine raises SessionAbortedError.
            return

        # 2. Transient transport -> backoff + retry_turn
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
                    "recovery": "retry_turn",
                    "reason": "transient transport failure",
                    "params": {"delay": delay, "max_retries": self.max_transport_retry},
                }
                return
            # Transport retries exhausted — leave _recovery_decision unset
            return

        # 3. Message contract violation -> persist_state with repair
        if "MessageContract" in exc_name or "contract" in error_text:
            ctx.hook_data["_recovery_decision"] = {
                "recovery": "persist_state",
                "reason": "message contract violation",
                "params": {"repair_messages": True},
            }
            return

        # 4. Guard/approval denials -> noop (model sees tool_result, responds)
        if exc_name in ("PermissionDenied", "ApprovalDenied",
                        "SandboxViolation", "ApprovalTimeout"):
            ctx.hook_data["_recovery_decision"] = {"recovery": "noop"}
            return

        # 5. Tool execution failure -> inject error so model can retry
        phase = ctx.hook_data.get("_error_phase", "")
        if phase == "execute_tools":
            ctx.hook_data["_recovery_decision"] = {
                "recovery": "inject_tool_error",
                "reason": "tool execution failed",
                "params": {"error": str(exc)},
            }
            return

        # 6. Unknown error — no recovery strategy.
        # Leave _recovery_decision unset so the engine re-raises
        # the original exception. Do NOT swallow unknown errors as
        # a generic "abort" — the caller needs to see the real error.
        return

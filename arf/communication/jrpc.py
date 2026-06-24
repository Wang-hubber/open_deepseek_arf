"""JRPC 2.0 envelope for A2A peer messages — transport-layer protocol.

Framework-level concern: request/response pairing via ``id``, method
namespace, structured params.  Full traceability — every bus message is
a self-describing event.

Plugin concern (LLM): only ``content: str``.  The envelope is stripped
before injection — the model sees plain text with a thin type tag
(``[task] ...``, ``[reply] ...``) and sender attribution via the
``name`` field.  The model does NOT see JRPC JSON.
"""


class JrpcEnvelope:
    """JRPC 2.0 envelope factories and content extraction for A2A transport."""

    METHOD_ASSIGN = "task.assign"
    METHOD_CANCEL = "task.cancel"
    METHOD_RESULT = "task.result"

    _LABEL_MAP = {
        "task.assign": "task",
        "task.cancel": "cancel",
        "task.result": "reply",
    }

    # ── factories (tools → JRPC payload) ──────────────────────────

    @staticmethod
    def request(*, method: str, params: dict, id: str) -> dict:
        return {"jsonrpc": "2.0", "method": method, "params": params, "id": id}

    @staticmethod
    def response(*, id: str, result: dict | None = None,
                 error: dict | None = None) -> dict:
        d: dict = {"jsonrpc": "2.0", "id": id}
        if result is not None:
            d["result"] = result
        if error is not None:
            d["error"] = error
        return d

    @staticmethod
    def notification(*, method: str, params: dict) -> dict:
        return {"jsonrpc": "2.0", "method": method, "params": params}

    # ── content extraction (JRPC payload → LLM text) ──────────────

    @staticmethod
    def extract_content(payload: dict) -> str:
        """Extract the human-readable message from a JRPC payload."""
        if "error" in payload:
            return payload["error"].get("message", "Error")
        if "result" in payload:
            return payload["result"].get("summary", "")
        params = payload.get("params", {})
        message = params.get("message") or params.get("description", "")
        if message:
            return message
        # Cancel notifications carry correlation_id, not message
        if payload.get("method") == JrpcEnvelope.METHOD_CANCEL:
            cid = params.get("correlation_id", "unknown")
            return f"Task cancelled: {cid}"
        return ""

    @staticmethod
    def method_to_label(method: str) -> str:
        """Map JRPC method to a minimal display label for the model."""
        return JrpcEnvelope._LABEL_MAP.get(
            method, method.rsplit(".", 1)[-1]
        )

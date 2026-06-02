"""MCP JSON-RPC 2.0 message models and stdio framing."""
from typing import Any
from pydantic import BaseModel, Field


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request."""
    jsonrpc: str = Field(default="2.0", alias="jsonrpc")
    id: int | str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response — either result or error set, not both."""
    jsonrpc: str = Field(default="2.0", alias="jsonrpc")
    id: int | str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}

    def model_dump(self, **kwargs):
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)


class JsonRpcNotification(BaseModel):
    """JSON-RPC 2.0 notification — no id field."""
    jsonrpc: str = Field(default="2.0", alias="jsonrpc")
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class StdioFraming:
    """MCP stdio framing: Content-Length header + CRLF + body."""

    @staticmethod
    def encode(payload: str) -> bytes:
        body = payload.encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n"
        return header.encode("ascii") + body

    @staticmethod
    def decode(buffer: bytes) -> str | None:
        """Try to extract a complete JSON-RPC message from buffer.
        Returns None if incomplete."""
        header_end = buffer.find(b"\r\n\r\n")
        if header_end == -1:
            return None
        header = buffer[:header_end].decode("ascii")
        if not header.startswith("Content-Length: "):
            return None
        try:
            content_length = int(header[len("Content-Length: "):])
        except ValueError:
            return None
        body_start = header_end + 4
        if len(buffer) - body_start < content_length:
            return None
        return buffer[body_start:body_start + content_length].decode("utf-8")

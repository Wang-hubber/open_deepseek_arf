"""Tests for MCP JSON-RPC protocol models and stdio framing."""
import pytest
from arf.mcp.protocol import (
    JsonRpcRequest, JsonRpcResponse, JsonRpcNotification,
    StdioFraming,
)


class TestJsonRpcRequest:
    def test_basic_request(self):
        req = JsonRpcRequest(id=1, method="tools/list", params={})
        d = req.model_dump(by_alias=True)
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert d["method"] == "tools/list"

    def test_request_with_params(self):
        req = JsonRpcRequest(
            id=2,
            method="tools/call",
            params={"name": "user__bash", "arguments": {"command": "ls"}},
        )
        d = req.model_dump(by_alias=True)
        assert d["params"]["name"] == "user__bash"


class TestJsonRpcResponse:
    def test_success_response(self):
        resp = JsonRpcResponse(id=1, result={"tools": []})
        d = resp.model_dump(by_alias=True)
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert d["result"] == {"tools": []}
        assert "error" not in d

    def test_error_response(self):
        resp = JsonRpcResponse(
            id=1, error={"code": -32601, "message": "Method not found"}
        )
        d = resp.model_dump(by_alias=True)
        assert d["error"]["code"] == -32601
        assert "result" not in d


class TestJsonRpcNotification:
    def test_notification(self):
        notif = JsonRpcNotification(
            method="notifications/resources/updated",
            params={"uri": "skills/debug.yaml"},
        )
        d = notif.model_dump(by_alias=True)
        assert d["jsonrpc"] == "2.0"
        assert "id" not in d


class TestStdioFraming:
    def test_encode_message(self):
        payload = '{"jsonrpc":"2.0","id":1,"result":{}}'
        framed = StdioFraming.encode(payload)
        assert framed.startswith(b"Content-Length: ")
        assert b"\r\n\r\n" in framed
        _, body = framed.split(b"\r\n\r\n", 1)
        assert body == payload.encode("utf-8")

    def test_decode_complete_message(self):
        payload = '{"jsonrpc":"2.0","id":1,"result":{}}'
        framed = StdioFraming.encode(payload)
        decoded = StdioFraming.decode(framed)
        assert decoded == payload

    def test_decode_empty_buffer(self):
        assert StdioFraming.decode(b"") is None

    def test_decode_partial_header(self):
        assert StdioFraming.decode(b"Content-Length: 10") is None

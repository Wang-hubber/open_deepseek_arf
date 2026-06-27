"""
[A] Import + type construction — verify all exported types importable and basic construction correct.

Test angles: [覆盖] [trait] [构造]
"""
from arf import (
    __version__,
    Bus, BusGraph, Message, MessageFilter,
    NodeHandle, NodeId, NodeInfo, SendReceipt, ToMatch,
)


# ── A1 ──────────────────────────────────────────────────────────────────

def test_import_all_types():
    """[覆盖] All __all__ exported types importable and __version__ correct."""
    assert __version__ == "1.0.0-alpha.0"
    for cls in [Bus, BusGraph, Message, MessageFilter, NodeHandle, NodeId, NodeInfo, SendReceipt, ToMatch]:
        assert cls is not None


# ── A2 ──────────────────────────────────────────────────────────────────

def test_to_match_class_attrs():
    """[覆盖] ToMatch four class attributes accessible."""
    assert ToMatch.All is not None
    assert ToMatch.BroadcastOnly is not None
    assert ToMatch.DirectedToMe is not None
    assert ToMatch.BroadcastAndDirectedToMe is not None


# ── A3 ──────────────────────────────────────────────────────────────────

def test_node_id_equality_and_hash():
    """[trait] NodeId __eq__/__hash__/__str__/__repr__ correct."""
    a1 = NodeId("engine/a")
    a2 = NodeId("engine/a")
    b = NodeId("engine/b")

    assert a1 == a2
    assert a1 != b
    assert hash(a1) == hash(a2)
    assert str(a1) == "engine/a"
    assert repr(a1) == "NodeId('engine/a')"


# ── A4 ──────────────────────────────────────────────────────────────────

def test_node_info_default_online_since():
    """[构造] online_since defaults to 0."""
    info = NodeInfo("mcp/fs", "mcp", {})
    assert info.online_since == 0
    assert str(info.node_id) == "mcp/fs"
    assert info.capabilities == {}


def test_node_info_full_construction():
    """[构造] NodeInfo all fields correct."""
    info = NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write"]}, online_since=9999)
    assert str(info.node_id) == "mcp/fs"
    assert info.node_type == "mcp"
    assert info.capabilities == {"tools": ["read", "write"]}
    assert info.online_since == 9999


# ── A5 ──────────────────────────────────────────────────────────────────

def test_message_filter_defaults():
    """[构造] MessageFilter defaults: types=None, to_match=BroadcastAndDirectedToMe."""
    f = MessageFilter()
    assert f.types is None
    assert f.to_match == ToMatch.BroadcastAndDirectedToMe


def test_message_filter_custom():
    """[构造] MessageFilter custom params correct."""
    f = MessageFilter(types=["action", "event"], to_match=ToMatch.All)
    assert f.types == ["action", "event"]
    assert f.to_match == ToMatch.All

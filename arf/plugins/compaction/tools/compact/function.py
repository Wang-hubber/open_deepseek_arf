"""compact tool — manually trigger context compaction."""


async def execute(_engine=None, _plugin_provider=None, **kwargs) -> dict:
    """Manually invoke compaction via the compaction plugin."""
    if _plugin_provider is None:
        return {"ok": False, "error": "Plugin provider not available"}

    # Find the compaction plugin
    for plugin in _plugin_provider.list_plugins():
        if plugin.name == "compaction":
            from arf.core.plugin_context import PluginContext
            ctx = PluginContext(
                session_id=kwargs.get("session_id", "default"),
                interaction_round=0,
            )
            result = await plugin.compact_now(ctx, trigger="manual")
            return result

    return {"ok": False, "error": "Compaction plugin not found"}

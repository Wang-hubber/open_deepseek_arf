"""Tool name convention — shared namespace resolution.

Tool names use ``source__local`` format (double-underscore separator):
  user__write_file          — app-level tools (tools/ directory)
  filesystem__read_text_file — plugin tools (arf/plugins/{plugin}/tools/)
  server__search             — remote MCP server tools

Permission lists (allow/ask/deny) use bare names (write_file) or
full namespaced names (filesystem__write_file).  This module provides
the single source of truth for name splitting and matching.
"""

SEPARATOR = "__"


def split_name(name: str) -> tuple[str, str]:
    """Split a namespaced name into (namespace, local_name).

    ``split_name("filesystem__write_file")`` → ``("filesystem", "write_file")``
    ``split_name("write_file")``           → ``("", "write_file")``
    """
    parts = name.split(SEPARATOR, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", name


def join_name(namespace: str, local_name: str) -> str:
    """Combine namespace + local name.

    ``join_name("filesystem", "write_file")`` → ``"filesystem__write_file"``
    ``join_name("", "write_file")``           → ``"write_file"``
    """
    if namespace:
        return f"{namespace}{SEPARATOR}{local_name}"
    return local_name


def matches_perm(tool_name: str, perm_set: set[str]) -> bool:
    """Check if a namespaced tool name matches any entry in *perm_set*.

    Permission lists use bare names (e.g. ``write_file``) while MCP tool
    names are namespaced (``filesystem__write_file``).  This matches both
    the full namespaced name AND the bare suffix.

    ``matches_perm("filesystem__write_file", {"write_file"})`` → True
    ``matches_perm("filesystem__write_file", {"filesystem__write_file"})`` → True
    ``matches_perm("filesystem__write_file", {"read_file"})`` → False
    """
    if not perm_set:
        return False
    if tool_name in perm_set:
        return True
    namespace, bare = split_name(tool_name)
    if namespace and bare in perm_set:
        return True
    return False

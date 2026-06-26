"""Backward-compat shim — merged into arf.guardrails."""
from arf.guardrails.sandbox_manager import SandboxManager, SandboxDiff, FileChange
__all__ = ["SandboxManager", "SandboxDiff", "FileChange"]

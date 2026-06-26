"""Sandbox module — merged into arf.guardrails. Re-exports for backward compatibility."""
from arf.guardrails.directory_boundary import DirectoryBoundary
from arf.guardrails.path_sandbox import PathSandbox
from arf.guardrails.sandbox_manager import FileChange, SandboxDiff, SandboxManager

__all__ = ["DirectoryBoundary", "PathSandbox", "SandboxManager", "SandboxDiff", "FileChange"]

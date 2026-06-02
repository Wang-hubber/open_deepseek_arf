"""ARF Concurrency — DEPRECATED.

TaskScheduler is deferred. Single-agent execution only for now.
Plan-Execute + parallel scheduling will be redesigned when needed.
"""
import warnings
warnings.warn(
    "arf.concurrency is deprecated. TaskScheduler is deferred.",
    DeprecationWarning, stacklevel=2,
)

from arf.action_runner.scheduler import ResourceScheduler

__all__ = ["ResourceScheduler"]

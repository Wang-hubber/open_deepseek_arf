"""RollbackManager — single-unit rollback with downstream cancellation."""

from __future__ import annotations

from arf.core.execution import Executable


class RollbackManager:
    """Handles failure of a single executable: rollback it, cancel dependents.

    Rules:
    - Call rollback() on the failed unit (best-effort, errors suppressed)
    - All units whose dependencies include the failed unit are cancelled
    - Sibling units are unaffected
    - Successful units are never rolled back
    """

    @staticmethod
    async def handle(
        failed: Executable,
        remaining: list[Executable],
    ) -> list[str]:
        """Handle failure of `failed` unit.

        Returns names of cancelled executables (for event emission / result
        assembly).

        Args:
            failed: The executable that failed.
            remaining: Executables that have not yet been executed.

        Returns:
            List of names of executables that should be cancelled because
            they depend on the failed unit.
        """
        cancelled: list[str] = []

        # 1. Rollback the failed unit (best effort)
        try:
            await failed.rollback()
        except Exception:
            pass  # rollback failure is logged but never propagated

        # 2. Cancel all downstream dependents
        for e in remaining:
            if failed.name in e.dependencies:
                cancelled.append(e.name)

        return cancelled

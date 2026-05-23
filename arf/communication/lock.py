"""InMemoryLock — asyncio-based Lock for SharedWorkspace."""
import time


class InMemoryLock:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, float]] = {}

    async def acquire(self, key: str, owner: str, ttl: float = 30.0) -> bool:
        now = time.time()
        if key in self._locks:
            _, expires = self._locks[key]
            if now < expires:
                return False
        self._locks[key] = (owner, now + ttl)
        return True

    async def release(self, key: str, owner: str) -> None:
        if key in self._locks and self._locks[key][0] == owner:
            del self._locks[key]

    def reset(self) -> None:
        self._locks.clear()

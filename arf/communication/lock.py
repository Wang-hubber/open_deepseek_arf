"""InMemoryLock — asyncio-based Lock for SharedWorkspace."""
import asyncio
import time


class InMemoryLock:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, float]] = {}

    async def acquire(self, key: str, owner: str, ttl: float = 30.0,
                      wait: float | None = None) -> bool:
        now = time.time()
        # wait=None → old behaviour: return immediately if held
        if key in self._locks:
            _, expires = self._locks[key]
            if now < expires:
                if wait is None:
                    return False
                deadline = now + wait
                while time.time() < deadline:
                    await asyncio.sleep(0.05)
                    if key not in self._locks or time.time() >= self._locks[key][1]:
                        break
                else:
                    return False
                if key in self._locks and time.time() < self._locks[key][1]:
                    return False
        self._locks[key] = (owner, time.time() + ttl)
        return True

    async def release(self, key: str, owner: str) -> None:
        if key in self._locks and self._locks[key][0] == owner:
            del self._locks[key]

    def reset(self) -> None:
        self._locks.clear()

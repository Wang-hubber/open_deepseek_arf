"""SequentialScheduler — execute tasks one at a time."""


class SequentialScheduler:
    async def schedule(self, tasks: list[dict]) -> list[dict]:
        return tasks

    async def execute(self, tasks: list[dict]) -> list[dict]:
        results = []
        for t in tasks:
            fn = t.get("fn")
            if callable(fn):
                r = fn() if not hasattr(fn, "__await__") else await fn()
                results.append({"id": t.get("id", ""), "result": r})
            else:
                results.append({"id": t.get("id", ""), "result": None})
        return results

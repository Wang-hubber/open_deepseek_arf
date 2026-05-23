"""DictWorkspace — dict-backed shared blackboard for multi-agent collaboration."""


class DictWorkspace:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self.write_history: list[dict] = []

    async def write(self, key: str, value: dict, owner: str) -> None:
        record = {**value, "_owner": owner}
        self._data[key] = record
        self.write_history.append({"key": key, "owner": owner, "value": value})

    async def read(self, key: str) -> dict | None:
        return self._data.get(key)

    def reset(self) -> None:
        self._data.clear()
        self.write_history.clear()

"""RoundRobinSupervisor — cycle through agents for task assignment."""
from arf.core.protocols.communication import AgentInfo


class RoundRobinSupervisor:
    def __init__(self) -> None:
        self._index = 0

    async def route_task(self, task: dict, agents: list[AgentInfo]) -> str:
        if not agents:
            return ""
        agent = agents[self._index % len(agents)]
        self._index += 1
        return agent.name

    async def should_intervene(self, handle_id: str, progress: dict) -> bool:
        return False

    async def synthesize(self, results: list[dict]) -> str:
        return "\n".join(str(r) for r in results)

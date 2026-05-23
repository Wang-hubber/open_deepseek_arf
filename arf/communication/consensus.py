"""MajorityVoteConsensus — simple majority voting for multi-agent decisions."""


class MajorityVoteConsensus:
    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold
        self._proposals: dict[str, dict] = {}
        self._votes: dict[str, dict[str, str]] = {}

    async def propose(self, proposal: dict, voters: list[str]) -> dict:
        import uuid
        pid = str(uuid.uuid4())
        self._proposals[pid] = {"proposal": proposal, "voters": voters}
        self._votes[pid] = {}
        return {"proposal_id": pid, "status": "open"}

    async def vote(self, proposal_id: str, vote: str) -> None:
        if proposal_id in self._votes:
            self._votes[proposal_id][vote] = vote

    def reset(self) -> None:
        self._proposals.clear()
        self._votes.clear()

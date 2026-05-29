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

    async def verdict(self, proposal_id: str) -> dict:
        """Return tally and whether consensus was reached (> threshold)."""
        if proposal_id not in self._proposals:
            return {"proposal_id": proposal_id, "status": "not_found"}
        voters = self._proposals[proposal_id]["voters"]
        votes = self._votes.get(proposal_id, {})
        yes_count = sum(1 for v in votes.values() if v == "yes")
        ratio = yes_count / len(voters) if voters else 0.0
        return {
            "proposal_id": proposal_id,
            "status": "passed" if ratio > self._threshold else "failed",
            "yes": yes_count,
            "total": len(voters),
            "ratio": ratio,
            "threshold": self._threshold,
        }

    def reset(self) -> None:
        self._proposals.clear()
        self._votes.clear()

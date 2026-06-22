"""Content-addressable eval version archives."""
import hashlib
import json
import os
from pathlib import Path

import yaml

_EXCLUDED_HASH_FIELDS = {"data_path", "workspace_dir", "tools_dir", "skills_dir"}


class EvalVersionManager:
    """Manage eval version archives under eval/<benchmark>/<hash>/.

    Usage:
        vm = EvalVersionManager("eval/customer_support/benchmark.json")
        vhash = vm.save(report, agent_config)
        versions = vm.list_versions()
        baseline = vm.find_baseline(exclude_hash=vhash)
    """

    def __init__(self, benchmark_path: str):
        self._benchmark_dir = Path(benchmark_path).parent

    @staticmethod
    def compute_hash(agent_config) -> str:
        """SHA256 of filtered agent config YAML. Pure function.

        Excludes path/environment fields that don't affect agent behavior,
        plus schema_version (matching AgentConfig.to_yaml() behavior).
        """
        config_dict = agent_config.model_dump(
            exclude=_EXCLUDED_HASH_FIELDS | {"schema_version"},
            exclude_none=True,
        )
        yaml_bytes = yaml.dump(config_dict, sort_keys=True, allow_unicode=True)
        return hashlib.sha256(yaml_bytes.encode("utf-8")).hexdigest()

    def save(self, report, agent_config) -> str:
        """Save report + agent config to version directory.

        Idempotent: same config yields same hash/overwrite.
        Returns the version hash (full 64-char SHA256 hex).
        """
        version_hash = self.compute_hash(agent_config)
        version_dir = self._benchmark_dir / version_hash
        version_dir.mkdir(parents=True, exist_ok=True)
        # agent.yaml -- full config including path fields (human-readable)
        agent_config.to_yaml(str(version_dir))
        # report.json
        report.to_json(str(version_dir / "report.json"))
        return version_hash

    def list_versions(self) -> list[dict]:
        """List all saved versions, sorted by modification time descending.

        Each entry: {hash, timestamp, pass_rate, weighted_score, total, passed, failed}
        """
        versions: list[dict] = []
        if not self._benchmark_dir.exists():
            return versions
        entries = [
            (d.stat().st_mtime, d)
            for d in self._benchmark_dir.iterdir()
            if d.is_dir() and (d / "report.json").exists()
        ]
        entries.sort(key=lambda x: x[0], reverse=True)
        for _, d in entries:
            try:
                with open(d / "report.json", encoding="utf-8") as f:
                    data = json.load(f)
                s = data.get("summary", {})
                versions.append({
                    "hash": d.name,
                    "timestamp": data.get("timestamp", 0.0),
                    "pass_rate": s.get("pass_rate", 0.0),
                    "weighted_score": s.get("weighted_score", 0.0),
                    "total": s.get("total", 0),
                    "passed": s.get("passed", 0),
                    "failed": s.get("failed", 0),
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return versions

    def load_version(self, version_hash: str):
        """Load a specific version's EvalReport from disk.

        Raises FileNotFoundError if the version does not exist.
        """
        from arf.plugins.eval.models import EvalReport

        report_path = self._benchmark_dir / version_hash / "report.json"
        if not report_path.exists():
            raise FileNotFoundError(f"Version not found: {version_hash}")
        return EvalReport.from_json(str(report_path))

    def find_baseline(self, exclude_hash: str = ""):
        """Find the most recent version whose hash differs from *exclude_hash*.

        Returns EvalReport or None if no other version exists.
        """
        for v in self.list_versions():
            if v["hash"] != exclude_hash:
                return self.load_version(v["hash"])
        return None

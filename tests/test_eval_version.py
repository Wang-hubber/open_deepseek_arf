"""Unit tests for EvalVersionManager."""
import tempfile
from pathlib import Path

import pytest
import yaml

from arf.agent.config import AgentConfig, SystemPromptConfig
from arf.plugins.eval.version import EvalVersionManager, _EXCLUDED_HASH_FIELDS
from arf.plugins.eval.models import EvalReport, EvalSummary, EvalBenchmark, EvalCase


class TestComputeHash:
    def test_compute_hash_deterministic(self):
        """Same config produces same hash."""
        cfg = AgentConfig(name="test", role="helper")
        h1 = EvalVersionManager.compute_hash(cfg)
        h2 = EvalVersionManager.compute_hash(cfg)
        assert h1 == h2
        assert len(h1) == 64  # full SHA256 hex

    def test_compute_hash_different_configs(self):
        """Different configs produce different hashes."""
        cfg1 = AgentConfig(name="agent-a", role="coder")
        cfg2 = AgentConfig(name="agent-b", role="reviewer")
        assert EvalVersionManager.compute_hash(cfg1) != EvalVersionManager.compute_hash(cfg2)

    def test_compute_hash_excludes_path_fields(self):
        """Path/env fields do not affect hash."""
        cfg1 = AgentConfig(name="test", data_path="/tmp/a", tools_dir="/tmp/t1")
        cfg2 = AgentConfig(name="test", data_path="/tmp/b", tools_dir="/tmp/t2")
        assert EvalVersionManager.compute_hash(cfg1) == EvalVersionManager.compute_hash(cfg2)

    def test_compute_hash_includes_behavior_fields(self):
        """Behavior fields DO affect hash."""
        cfg1 = AgentConfig(name="test", role="coder")
        cfg2 = AgentConfig(name="test", role="reviewer")
        assert EvalVersionManager.compute_hash(cfg1) != EvalVersionManager.compute_hash(cfg2)


class TestSaveAndLoad:
    @pytest.fixture
    def tmp_benchmark_dir(self):
        with tempfile.TemporaryDirectory() as td:
            bm_dir = Path(td) / "eval" / "test_bm"
            bm_dir.mkdir(parents=True)
            # Write a minimal benchmark.json
            bm = EvalBenchmark(name="test_bm", cases=[EvalCase(id="c0", input="hello")])
            bm.to_json(str(bm_dir / "benchmark.json"))
            yield str(bm_dir / "benchmark.json")

    def test_save_creates_version_dir(self, tmp_benchmark_dir):
        vm = EvalVersionManager(tmp_benchmark_dir)
        cfg = AgentConfig(name="test-agent")
        report = EvalReport(
            run_id="r1", benchmark_name="test_bm",
            agent_config_hash="abc", timestamp=1000.0,
        )
        vhash = vm.save(report, cfg)
        version_dir = Path(tmp_benchmark_dir).parent / vhash
        assert version_dir.exists()
        assert (version_dir / "report.json").exists()
        assert (version_dir / "agent.yaml").exists()

    def test_save_is_idempotent(self, tmp_benchmark_dir):
        vm = EvalVersionManager(tmp_benchmark_dir)
        cfg = AgentConfig(name="test-agent")
        report = EvalReport(
            run_id="r1", benchmark_name="test_bm",
            agent_config_hash="abc", timestamp=1000.0,
        )
        vhash1 = vm.save(report, cfg)
        vhash2 = vm.save(report, cfg)
        assert vhash1 == vhash2  # same hash
        # Only one version dir
        versions = vm.list_versions()
        assert len(versions) == 1

    def test_save_different_configs_create_separate_versions(self, tmp_benchmark_dir):
        vm = EvalVersionManager(tmp_benchmark_dir)
        cfg1 = AgentConfig(name="agent-a")
        cfg2 = AgentConfig(name="agent-b")
        report = EvalReport(
            run_id="r1", benchmark_name="test_bm",
            agent_config_hash="abc", timestamp=1000.0,
        )
        vm.save(report, cfg1)
        vm.save(report, cfg2)
        versions = vm.list_versions()
        assert len(versions) == 2

    def test_load_version_roundtrip(self, tmp_benchmark_dir):
        vm = EvalVersionManager(tmp_benchmark_dir)
        cfg = AgentConfig(name="test-agent")
        report = EvalReport(
            run_id="r1", benchmark_name="test_bm",
            agent_config_hash="abc", timestamp=1000.0,
            summary=EvalSummary(total=10, passed=8, failed=2, pass_rate=0.8),
        )
        vhash = vm.save(report, cfg)
        loaded = vm.load_version(vhash)
        assert loaded.run_id == "r1"
        assert loaded.summary.pass_rate == 0.8

    def test_load_version_not_found(self, tmp_benchmark_dir):
        vm = EvalVersionManager(tmp_benchmark_dir)
        with pytest.raises(FileNotFoundError):
            vm.load_version("deadbeef")


class TestListAndBaseline:
    @pytest.fixture
    def vm_with_versions(self):
        with tempfile.TemporaryDirectory() as td:
            bm_dir = Path(td) / "eval" / "test_bm"
            bm_dir.mkdir(parents=True)
            bm = EvalBenchmark(name="test_bm", cases=[EvalCase(id="c0", input="hello")])
            bm.to_json(str(bm_dir / "benchmark.json"))
            vm = EvalVersionManager(str(bm_dir / "benchmark.json"))
            cfg1 = AgentConfig(name="agent-v1")
            cfg2 = AgentConfig(name="agent-v2")
            r1 = EvalReport(
                run_id="r1", benchmark_name="test_bm",
                agent_config_hash="h1", timestamp=1000.0,
                summary=EvalSummary(total=5, passed=3, failed=2, pass_rate=0.6,
                                     weighted_score=0.7),
            )
            r2 = EvalReport(
                run_id="r2", benchmark_name="test_bm",
                agent_config_hash="h2", timestamp=2000.0,
                summary=EvalSummary(total=5, passed=5, failed=0, pass_rate=1.0,
                                     weighted_score=0.95),
            )
            vm.save(r1, cfg1)
            vm.save(r2, cfg2)
            yield vm

    def test_list_versions_returns_all(self, vm_with_versions):
        versions = vm_with_versions.list_versions()
        assert len(versions) == 2

    def test_list_versions_sorted_by_time_desc(self, vm_with_versions):
        versions = vm_with_versions.list_versions()
        assert versions[0]["timestamp"] > versions[1]["timestamp"]

    def test_list_versions_has_summary_fields(self, vm_with_versions):
        v = vm_with_versions.list_versions()[0]
        assert "hash" in v
        assert "timestamp" in v
        assert "pass_rate" in v
        assert "weighted_score" in v
        assert "total" in v
        assert "passed" in v
        assert "failed" in v

    def test_find_baseline_returns_other_version(self, vm_with_versions):
        versions = vm_with_versions.list_versions()
        current_hash = versions[0]["hash"]
        baseline = vm_with_versions.find_baseline(exclude_hash=current_hash)
        assert baseline is not None
        assert baseline.run_id != versions[0].get("run_id")  # different version

    def test_find_baseline_only_version_returns_none(self, vm_with_versions):
        versions = vm_with_versions.list_versions()
        import shutil
        other_hash = versions[1]["hash"]
        bm_dir = Path(vm_with_versions._benchmark_dir)
        shutil.rmtree(str(bm_dir / other_hash))
        baseline = vm_with_versions.find_baseline(exclude_hash=versions[0]["hash"])
        assert baseline is None

    def test_list_versions_no_benchmark_dir(self):
        vm = EvalVersionManager("/tmp/nonexistent/benchmark.json")
        assert vm.list_versions() == []

    def test_list_versions_skips_non_dirs_and_missing_reports(self, vm_with_versions):
        """Non-directory entries and dirs without report.json are skipped."""
        bm_dir = vm_with_versions._benchmark_dir
        (bm_dir / "not_a_version.txt").write_text("junk")
        (bm_dir / "empty_dir").mkdir(exist_ok=True)
        versions = vm_with_versions.list_versions()
        # Should still only have the 2 real versions
        assert len(versions) == 2

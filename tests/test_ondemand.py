"""Tests for the pipeline lock used by on-demand filing preparation and the nightly job."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import ondemand  # noqa: E402


def test_lock_is_exclusive_and_released(tmp_path, monkeypatch):
    monkeypatch.setattr(ondemand, "LOCK_PATH", tmp_path / "pipeline.lock")
    with ondemand.pipeline_lock():
        with pytest.raises(TimeoutError):
            with ondemand.pipeline_lock(wait_seconds=0):
                pass
    assert not (tmp_path / "pipeline.lock").exists()
    with ondemand.pipeline_lock(wait_seconds=0):   # free again after release
        pass


def test_stale_lock_is_taken_over(tmp_path, monkeypatch):
    lock = tmp_path / "pipeline.lock"
    monkeypatch.setattr(ondemand, "LOCK_PATH", lock)
    monkeypatch.setattr(ondemand, "STALE_LOCK_SECONDS", -1)   # any existing lock counts as stale
    lock.write_text("12345 crashed-run")
    with ondemand.pipeline_lock(wait_seconds=0):
        assert lock.read_text() != "12345 crashed-run"

"""A screen recording that stops mid-session is told at the step, not at the review."""
from pathlib import Path
from types import SimpleNamespace

from reel_studio.engine import BrowserSession
from reel_studio.render import RECORDING_STALL_SECONDS, recording_problem


def test_a_growing_recording_is_fine():
    assert recording_problem(True, 2000, 2000, last_growth_at=100.0, now=101.0) is None


def test_a_recording_that_stopped_growing_is_a_problem():
    problem = recording_problem(True, 2000, 2000, last_growth_at=100.0, now=100.0 + RECORDING_STALL_SECONDS)
    assert problem and "not grown" in problem


def test_a_short_pause_is_not_a_stall():
    assert recording_problem(True, 2000, 2000, last_growth_at=100.0, now=100.0 + RECORDING_STALL_SECONDS - 1) is None


def test_an_exited_recorder_is_a_problem():
    assert recording_problem(False, 2000, 1000, last_growth_at=100.0, now=100.5) == "the screen recorder exited"


class _Recorder:
    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


def _session(tmp_path: Path, alive=True):
    fake = SimpleNamespace(
        directory=tmp_path, recorder=_Recorder(alive), t0=0.0,
        recording_size=0, recording_grew_at=0.0, recording_lost_at=None,
    )
    fake.recording_check = BrowserSession.recording_check.__get__(fake)
    return fake


def test_the_step_says_where_the_footage_ends(tmp_path, monkeypatch):
    clock = {"now": 500.0}
    monkeypatch.setattr("reel_studio.engine.time.monotonic", lambda: clock["now"])
    (tmp_path / "screen.mp4").write_bytes(b"x" * 4000)
    session = _session(tmp_path)
    assert session.recording_check() is None
    clock["now"] = 566.0
    (tmp_path / "screen.mp4").write_bytes(b"x" * 9000)
    assert session.recording_check() is None
    clock["now"] = 566.0 + RECORDING_STALL_SECONDS + 5
    lost = session.recording_check()
    assert lost and lost["footage_ends_at_seconds"] == 566.0
    assert "no footage" in lost["message"]


def test_a_dead_recorder_is_reported_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr("reel_studio.engine.time.monotonic", lambda: 42.0)
    (tmp_path / "screen.mp4").write_bytes(b"x" * 100)
    lost = _session(tmp_path, alive=False).recording_check()
    assert lost and lost["reason"] == "the screen recorder exited"

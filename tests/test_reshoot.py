"""One beat of a finished film can be shot again. Sluss take 6's only remedy
for one badly framed beat was a fresh take of all 16, and it broke another."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel_studio import pickup, render
from reel_studio.camera import Camera


@pytest.fixture
def mods(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "reshoot.db"))
    from reel_studio import server, store

    store.init_schema()
    server.sessions.clear()
    server.finish_jobs.clear()
    server.rehearsal_jobs.clear()
    return server, store, tmp_path


class Live:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True)
        (directory / "screen.mp4").write_bytes(b"x")
        self.camera = Camera(1920, 1080, 1.0, [{"t": 2.0, "zoom": 1.1, "cx": 900.0, "cy": 500.0}])
        self.aborted = False
        self.acted: list[str] = []

    async def act(self, action, narration=""):
        self.acted.append(action.type)
        return {"ok": True}, None

    async def abort(self):
        self.aborted = True

    async def observe(self, detail):
        return None

    async def form_fields(self):
        return {"username": "u", "password": "p"}

    async def dismiss_consent(self, *args):
        return None


def beat(number, shot, attempts, **extra):
    return {"number": number, "label": f"b{number}", "shot": shot, "line": f"line {number}",
            "url": "https://e.example/p", "state": {"status": "ok", "attempts": [
                {"steps": steps, "visible": steps, "ok": True, "retake": False} for steps in attempts]}, **extra}


def film(server, store, tmp, beats, sid="s1", steps=3):
    directory = tmp / sid
    directory.mkdir()
    (directory / "screen.mp4").write_bytes(b"x")
    (directory / "video.mp4").write_bytes(b"x")
    store.create_session(sid, "https://e.example", "voice", 1920, 1080, str(directory), "edge",
                         None, None, "", "", "#1f2a44", "", "L", "none", "cuts")
    for index in range(steps):
        store.append_step(sid, "wait", None, "u", "t", f"line {index}", 1.0, 2.0 + index, None, True, None)
    store.update_session_start_args(sid, {"width": 1920, "height": 1080, "show_pointer": False})
    store.finish_session(sid, str(directory / "video.mp4"), None, 9.0)
    store.save_storyboard("plan", beats, [], 60, sid)
    return directory


def stage(monkeypatch, server, store, tmp, ok=True):
    lives: list[Live] = []

    async def create(*args, **kwargs):
        live = Live(tmp / f"pick-{len(lives)}")
        lives.append(live)
        return live

    async def shoot(session_id, plan, target, retake, job=None):
        idx = store.next_step_index(session_id)
        store.append_step(session_id, "wait", None, "u", "t", "again", 1.0, 3.0, None, True, None)
        target["state"]["attempts"].append({"steps": [idx], "visible": [idx], "ok": ok, "retake": retake})
        store.update_storyboard_beats(plan["plan_id"], plan["beats"])
        return {"beat": target["number"], "ok": ok, **({} if ok else {"reason": "text not found"})}

    rendered = []

    async def rerender(job, session, *args):
        rendered.append(session)
        return {"ok": True, "status": "done", "duration": 9.0}

    monkeypatch.setattr(server.BrowserSession, "create", create)
    monkeypatch.setattr(server, "_shoot_beat", shoot)
    monkeypatch.setattr(server, "_rerender", rerender)
    monkeypatch.setattr(server.pickup, "join_recordings", lambda first, second: 30.0)
    monkeypatch.setattr(server, "PICKUP_TAIL_SECONDS", -1.0)  # the fake line is one second long
    return lives, rendered


def test_one_beat_is_shot_again_and_the_rest_stays(mods, monkeypatch):
    server, store, tmp = mods
    film(server, store, tmp, [beat(1, "system", [[0]]), beat(2, "system", [[1]]), beat(3, "system", [[2]])])
    lives, rendered = stage(monkeypatch, server, store, tmp)
    result = asyncio.run(server.reshoot_beat("s1", 2))
    assert result["ok"] and result["beat"] == 2 and result["take"] == 2 and "review_session" in result["next"]
    steps = store.get_session("s1")["steps"]
    assert steps[3]["offset_seconds"] == 33.0  # on the joined recording's clock
    assert steps[1]["offscreen"] and not steps[0]["offscreen"] and not steps[2]["offscreen"]
    assert [step["idx"] for step in store.play_order(steps) if not step["offscreen"]] == [0, 3, 2]
    assert rendered and lives[0].aborted and "s1" not in server.sessions


def test_a_failed_beat_leaves_the_film_as_it_was(mods, monkeypatch):
    server, store, tmp = mods
    film(server, store, tmp, [beat(1, "system", [[0]]), beat(2, "system", [[1]])], steps=2)
    lives, rendered = stage(monkeypatch, server, store, tmp, ok=False)
    result = asyncio.run(server.reshoot_beat("s1", 2))
    assert result["status"] == "failed" and "text not found" in result["error"]["message"]
    assert len(store.get_session("s1")["steps"]) == 2 and not rendered
    assert len(store.session_storyboard("s1")["beats"][1]["state"]["attempts"]) == 1
    assert lives[0].aborted and not lives[0].directory.exists()


def test_a_sign_in_before_the_beat_is_done_first_and_kept_out(mods, monkeypatch):
    server, store, tmp = mods
    login = beat(1, "login", [[0]], username="u", password_secret="DEMO", wait_for_text="in")
    login["line"] = ""
    film(server, store, tmp, [login, beat(2, "system", [[1]])], steps=2)
    lives, _ = stage(monkeypatch, server, store, tmp)
    assert asyncio.run(server.reshoot_beat("s1", 2))["ok"]
    assert "press_key" in lives[0].acted
    assert len(store.get_session("s1")["steps"]) == 3  # the sign-in added no steps


@pytest.mark.parametrize("target,attempts,expected", [
    (9, [[0]], "unknown_beat"),
    (1, [[0], [1], [2]], "reshoot_limit"),
])
def test_what_it_refuses(mods, target, attempts, expected):
    server, store, tmp = mods
    film(server, store, tmp, [beat(1, "system", attempts)])
    assert asyncio.run(server.reshoot_beat("s1", target))["error"]["type"] == expected


def test_a_drawn_card_is_edited_not_shot(mods):
    server, store, tmp = mods
    film(server, store, tmp, [beat(1, "card", [[0]], text="T")])
    result = asyncio.run(server.reshoot_beat("s1", 1))
    assert result["error"]["type"] == "nothing_to_reshoot" and "edit_cards" in result["hint"]


def test_it_waits_behind_a_recording_and_needs_the_recording(mods):
    server, store, tmp = mods
    directory = film(server, store, tmp, [beat(1, "system", [[0]])])
    server.sessions["other"] = object()
    assert asyncio.run(server.reshoot_beat("s1", 1))["error"]["type"] == "busy"
    server.sessions.clear()
    (directory / "screen.mp4").unlink()
    assert asyncio.run(server.reshoot_beat("s1", 1))["error"]["type"] == "recording_missing"


def test_the_verdict_offers_a_reshoot_until_the_beat_is_out_of_takes(mods):
    server, store, tmp = mods
    finding = {"category": "text_cut_at_edge", "severity": "medium", "beat": 1, "label": "b", "fix": "Lower it."}
    film(server, store, tmp, [beat(1, "system", [[0]])], steps=1)
    session = {"status": "finished", "start_args": {"generation": 1}}
    result = server._verdict("s1", session, [finding], [])
    assert result["verdict"] == "fix" and "reshoot_beat" in result["next"][-1]
    plan = store.session_storyboard("s1")
    plan["beats"][0]["state"].update(placed=True, attempts=[{"steps": [i], "visible": [i], "ok": True} for i in range(3)])
    store.update_storyboard_beats("plan", plan["beats"])
    assert server._verdict("s1", session, [finding], [])["verdict"] == "needs_human"


media = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def _clip(path, seconds, source="testsrc2"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{source}=size=320x176:rate=25",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)


@media
def test_two_recordings_join_into_the_first(tmp_path):
    first, second = tmp_path / "screen.mp4", tmp_path / "pick.mp4"
    _clip(first, 3)
    _clip(second, 2)
    assert pickup.join_recordings(first, second) == pytest.approx(3.0, abs=0.1)
    assert render.probe_duration(first) == pytest.approx(5.0, abs=0.2)
    assert not (tmp_path / "screen.joined.mp4").exists()


def test_a_pickups_moves_clips_and_texts_come_in_on_the_sessions_clock(tmp_path):
    directory, picked = tmp_path / "film", tmp_path / "pick"
    directory.mkdir(), picked.mkdir()
    Camera(1920, 1080, 1.0, [{"t": 5.0, "zoom": 1.3, "cx": 1.0, "cy": 1.0}]).save(directory)
    (picked / "narration-a.mp3").write_bytes(b"x")
    (picked / "glide-1.jpg").write_bytes(b"x")
    (picked / render.GLIDES_FILE).write_text(json.dumps([{"offset": 2.0, "image": "glide-1.jpg"}]))
    (picked / "page-text.jsonl").write_text('{"step": 9, "text": "hello"}\n')
    camera = Camera(1920, 1080, 1.0, [{"t": 2.0, "zoom": 1.1, "cx": 900.0, "cy": 500.0, "facts": {"lines": 1}}])
    pickup.take_in(directory, picked, 30.0, camera)
    keys = Camera.load(directory).keys
    assert [key["t"] for key in keys] == [5.0, 30.0, 32.0] and keys[1]["zoom"] == 1.0 and keys[2]["facts"] == {"lines": 1}
    assert (directory / "narration-a.mp3").is_file() and (directory / "glide-1.jpg").is_file()
    assert render.load_glides(directory)[32.0]["image"] == "glide-1.jpg"
    assert "hello" in (directory / "page-text.jsonl").read_text()


def test_the_recording_outlasts_the_line(mods, monkeypatch):
    server, store, tmp = mods
    film(server, store, tmp, [beat(1, "system", [[0]])], steps=1)
    stage(monkeypatch, server, store, tmp)
    waited = []
    real = asyncio.sleep

    async def sleep(seconds, *args):
        if seconds and seconds < 55:
            waited.append(seconds)
        await real(0)

    monkeypatch.setattr(server.asyncio, "sleep", sleep)
    monkeypatch.setattr(server, "PICKUP_TAIL_SECONDS", 1.0)
    assert asyncio.run(server.reshoot_beat("s1", 1))["ok"]
    assert 2.0 in waited  # the one-second line and its tail

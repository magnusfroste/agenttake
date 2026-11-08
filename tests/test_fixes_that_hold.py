"""A correction made after finish has to survive. A rerender cut off at the
client's timeout left Sluss takes 4 and 5 without subtitles; the two lines
corrected in take 5 were never in its storyboard, and its last card could
only be changed by hand in cards.json."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel_studio import render


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hold")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "hold.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store


def _session(store, tmp_path, sid, steps=(("wait", 1.0, "First line."),)):
    directory = tmp_path / sid
    directory.mkdir()
    (directory / "screen.mp4").write_bytes(b"x")
    (directory / "video.mp4").write_bytes(b"x")
    store.create_session(sid, "https://example.com", "en-US-JennyNeural", 320, 176, str(directory),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")
    for index, (kind, offset, line) in enumerate(steps):
        store.append_step(sid, kind, None, "https://example.com", "t", "", 0.0, offset, None, True, None)
        if line:
            store.update_step_narration(sid, index, line, None, narration_duration=1.0, narration_clip=None)
    store.finish_session(sid, str(directory / "video.mp4"), None, 9.0)
    return directory


def _beat(number, shot, line, steps, **extra):
    return {"number": number, "shot": shot, "line": line, "label": f"beat {number}",
            "state": {"status": "ok", "attempts": [
                {"steps": attempt, "visible": attempt, "retake": False, "ok": True} for attempt in steps]},
            **extra}


def _board(server, store, sid, beats):
    store.save_storyboard(f"plan-{sid}", beats, [], 60, sid)
    server._attach_storyboard(f"plan-{sid}", sid, beats)


def _no_media(monkeypatch, server, render_seconds=0.0, burn=True):
    calls = {"renders": 0, "subtitles": 0}

    def fake_render(*args, **kwargs):
        calls["renders"] += 1
        time.sleep(render_seconds)
        return SimpleNamespace(warnings=[], duration=9.0, timeline=[])

    def fake_subtitles(session_id):
        calls["subtitles"] += 1
        return burn

    async def fake_synthesize(text, voice, directory, provider):
        clip = Path(directory) / "clip.mp3"
        clip.write_bytes(b"x")
        return clip

    monkeypatch.setattr(server, "segmented_render_enabled", lambda: True)
    monkeypatch.setattr(server, "segmented_render", fake_render)
    monkeypatch.setattr(server, "apply_subtitles", fake_subtitles)
    monkeypatch.setattr(server, "synthesize", fake_synthesize)
    monkeypatch.setattr(server, "probe_duration", lambda path: 9.0)
    monkeypatch.setattr(server, "write_storyboard", lambda *args, **kwargs: None)
    return calls


def test_a_rerender_cut_off_still_gets_its_subtitles(mods, monkeypatch, tmp_path):
    server, store = mods
    _session(store, tmp_path, "s-cut")
    calls = _no_media(monkeypatch, server, render_seconds=0.2)

    async def scenario():
        call = asyncio.create_task(server.rerender("s-cut"))
        await asyncio.sleep(0.05)
        call.cancel()  # what the client's timeout does
        with pytest.raises(asyncio.CancelledError):
            await call
        await server.finish_jobs["s-cut"].task
        return await server.finish_status("s-cut")

    status = asyncio.run(scenario())
    assert status["ok"] and status["status"] == "done" and status["subtitles"]
    assert calls == {"renders": 1, "subtitles": 1}


def test_a_long_rerender_answers_with_a_job_and_blocks_edits(mods, monkeypatch, tmp_path):
    server, store = mods
    _session(store, tmp_path, "s-long")
    calls = _no_media(monkeypatch, server, render_seconds=0.3)
    monkeypatch.setattr(server, "FINISH_WAIT_SECONDS", 0.01)

    async def scenario():
        first = await server.rerender("s-long")
        second = await server.rerender("s-long")
        edit = await server.update_step_narration("s-long", 0, "Too soon.")
        running = await server.finish_status("s-long")
        await server.finish_jobs["s-long"].task
        return first, second, edit, running, await server.finish_status("s-long")

    first, second, edit, running, done = asyncio.run(scenario())
    assert first["status"] == "rendering" and first["job_id"] == "s-long"
    assert "Do not call rerender again" in first["hint"] and "the review" not in first["hint"]
    assert second["status"] == "rendering"
    assert edit["error"]["type"] == "session_busy"
    assert running["status"] == "rendering" and done["status"] == "done"
    assert calls["renders"] == 1


def test_a_failed_rerender_is_reported(mods, monkeypatch, tmp_path):
    server, store = mods
    _session(store, tmp_path, "s-fail")
    _no_media(monkeypatch, server)

    def broken(*args, **kwargs):
        raise RuntimeError("no such filter")

    monkeypatch.setattr(server, "segmented_render", broken)
    result = asyncio.run(server.rerender("s-fail"))
    assert not result["ok"] and result["status"] == "failed"
    assert result["error"]["type"] == "rerender_failed" and "no such filter" in result["error"]["message"]


def test_a_film_that_should_have_subtitles_says_when_it_got_none(mods, monkeypatch, tmp_path):
    server, store = mods
    _session(store, tmp_path, "s-none")
    store.update_session_subtitles("s-none", True)
    _no_media(monkeypatch, server, burn=False)
    result = asyncio.run(server.rerender("s-none"))
    assert result["ok"] and not result["subtitles"]
    assert any("subtitles on" in warning for warning in result["warnings"])


def test_a_corrected_line_reaches_the_storyboard_and_the_next_take(mods, monkeypatch, tmp_path):
    server, store = mods
    sid = "s-line"
    _session(store, tmp_path, sid, (("goto", 1.0, ""), ("wait", 2.0, "On our own hardware."), ("wait", 4.0, "Second.")))
    _board(server, store, sid, [_beat(1, "system", "On our own hardware.", [[0, 1]]), _beat(2, "system", "Second.", [[2]])])
    store.update_session_start_args(sid, {"start_url": "https://example.com"})

    result = asyncio.run(server.update_step_narration(sid, 1, " On your own infrastructure. "))
    assert result["ok"] and result["in_storyboard"]
    assert store.session_storyboard(sid)["beats"][0]["line"] == "On your own infrastructure."
    assert store.get_session(sid)["beats"][0]["line"] == "On your own infrastructure."

    async def fake_start(**kwargs):
        return {"session_id": "s-line-2"}

    store.create_session("s-line-2", "https://example.com", "en-US-JennyNeural", 320, 176, str(tmp_path),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")
    monkeypatch.setattr(server, "start_session", fake_start)
    fresh = asyncio.run(server.fresh_take(sid))
    assert fresh["beats"][0]["line"] == "On your own infrastructure."


def test_a_line_from_an_earlier_take_of_a_beat_leaves_the_storyboard_alone(mods, tmp_path):
    server, store = mods
    sid = "s-retake"
    _session(store, tmp_path, sid, (("wait", 1.0, "Line."), ("wait", 3.0, "Line.")))
    _board(server, store, sid, [_beat(1, "system", "Line.", [[0], [1]])])
    result = asyncio.run(server.update_step_narration(sid, 0, "Hidden take."))
    assert result["ok"] and not result["in_storyboard"]
    assert store.session_storyboard(sid)["beats"][0]["line"] == "Line."


def test_edit_cards_lists_and_changes_a_card(mods, tmp_path):
    server, store = mods
    sid = "s-cards"
    directory = _session(store, tmp_path, sid, (("wait", 1.0, "Hello."), ("card", 3.0, "Spoken."), ("card", 6.0, "Last.")))
    render.record_card(directory, 3.0, "Old text", "brand")
    render.record_card(directory, 6.0, "Hermes", "brand")
    _board(server, store, sid, [_beat(1, "system", "Hello.", [[0]]), _beat(2, "card", "Spoken.", [[1]], text="Old text"),
                                _beat(3, "card", "Last.", [[2]], text="Hermes")])

    listed = asyncio.run(server.edit_cards(sid))
    assert [(card["card"], card["step"], card["text"]) for card in listed["cards"]] == [(0, 1, "Old text"), (1, 2, "Hermes")]
    assert "rerender" not in listed["hint"]

    changed = asyncio.run(server.edit_cards(sid, card=1, text=" Directed by Hermes\n an AI agent ", background="0B1B2B"))
    assert changed["ok"] and changed["in_storyboard"] and "rerender" in changed["hint"]
    on_disk = {card["offset"]: card for card in json.loads((directory / render.CARDS_FILE).read_text())}
    assert on_disk[6.0]["text"] == "Directed by Hermes\nan AI agent" and on_disk[6.0]["background"] == "#0b1b2b"
    assert on_disk[3.0]["text"] == "Old text"
    beats = store.session_storyboard(sid)["beats"]
    assert beats[2]["text"] == "Directed by Hermes\nan AI agent" and beats[2]["background"] == "#0b1b2b"
    assert beats[2]["line"] == "Last."


@pytest.mark.parametrize("arguments,expected", [
    ({"card": 9, "text": "x"}, "unknown_card"),
    ({"text": "No card named"}, "invalid_edit"),
    ({"card": 0}, "invalid_edit"),
    ({"card": 0, "text": "   "}, "invalid_edit"),
    ({"card": 0, "text": "x" * 141}, "invalid_edit"),
    ({"card": 0, "background": "plaid"}, "invalid_edit"),
    ({"cta_url": "not a url"}, "invalid_edit"),
])
def test_edit_cards_refuses_what_it_cannot_cut(mods, tmp_path, arguments, expected):
    server, store = mods
    sid = f"s-bad-{abs(hash(str(arguments)))}"
    directory = _session(store, tmp_path, sid, (("card", 3.0, "Spoken."),))
    render.record_card(directory, 3.0, "Text", "brand")
    assert asyncio.run(server.edit_cards(sid, **arguments))["error"]["type"] == expected
    assert render.read_cards(directory)[0]["text"] == "Text"


def test_edit_cards_changes_the_closing_card_for_this_film_and_the_next(mods, monkeypatch, tmp_path):
    server, store = mods
    sid = "s-closing"
    _session(store, tmp_path, sid)
    store.update_session_start_args(sid, {"start_url": "https://example.com", "cta_text": "Learn more", "accent": "#1f2a44"})
    result = asyncio.run(server.edit_cards(sid, cta_text="Get it on GitHub", cta_url="https://github.com/x/y", title=""))
    assert result["ok"] and result["cta_text"] == "Get it on GitHub" and result["cta_url"] == "https://github.com/x/y"
    stored = store.get_session(sid)
    assert stored["cta_text"] == "Get it on GitHub" and stored["cta_url"] == "https://github.com/x/y"
    assert stored["start_args"]["cta_text"] == "Get it on GitHub" and stored["start_args"]["accent"] == "#1f2a44"


def test_an_ffmpeg_killed_by_a_signal_runs_once_more(monkeypatch):
    calls = []

    def flaky(command, check, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(-6, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(render.subprocess, "run", flaky)
    assert render.run_media(["ffmpeg"], timeout=5).returncode == 0
    assert calls == [{"timeout": 5}, {"timeout": 5}]


def test_an_ffmpeg_that_fails_by_itself_is_not_run_again(monkeypatch):
    calls = []

    def failing(command, check, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(render.subprocess, "run", failing)
    with pytest.raises(subprocess.CalledProcessError):
        render.run_media(["ffmpeg"])
    assert len(calls) == 1

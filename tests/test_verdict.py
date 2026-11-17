"""An agent has to know from the replies where a film stands. A person read the
contact sheet and decided, or a skill only Hermes has said what came next."""
from __future__ import annotations

import asyncio
import os

import pytest


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("verdict")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "verdict.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store


MEDIUM = {"category": "text_cut_at_edge", "severity": "medium", "beat": 13, "label": "audit", "fix": "Lower the zoom."}
LOW = {"category": "long_still", "severity": "low", "beat": 7, "fix": "Shorten it."}
HIGH = {"category": "blank_flash", "severity": "high", "location": "step[3]", "message": "Empty screen."}


def verdict(mods, findings, status="finished", generation=0, claims=()):
    server, _ = mods
    session = {"status": status, "start_args": {"generation": generation}}
    return server._verdict("no-plan", session, list(findings), list(claims))


def test_a_clean_film_is_ready_for_a_person_to_approve(mods):
    result = verdict(mods, [LOW])
    assert result["verdict"] == "ready_for_approval"
    assert any("approve" in line for line in result["next"]) and any("contact_sheet" in line for line in result["next"])


def test_open_findings_say_what_to_do_about_each(mods):
    result = verdict(mods, [MEDIUM, HIGH, LOW])
    assert result["verdict"] == "fix"
    assert result["next"][0].startswith("Beat 13 (audit), text_cut_at_edge: Lower the zoom.")
    assert "Empty screen." in result["next"][1] and not any("long_still" in line for line in result["next"])
    assert "fresh_take" in result["next"][-1]


def test_after_one_fresh_take_it_goes_to_a_person(mods):
    result = verdict(mods, [MEDIUM], generation=1)
    assert result["verdict"] == "needs_human" and "Lower the zoom." in " ".join(result["next"])
    assert not any("fresh_take(session_id)" in line for line in result["next"])


def test_shares_to_compare_are_in_the_next_step(mods):
    claim = [{"step": 1, "line": "Nearly half.", "on_screen_percentages": [48.0]}]
    assert any("claims_to_check" in line for line in verdict(mods, [], claims=claim)["next"])
    assert any("claims_to_check" in line for line in verdict(mods, [MEDIUM], claims=claim)["next"])


def test_before_finish_it_says_finish(mods):
    assert verdict(mods, [], status="recording") == {
        "verdict": "not_finished", "next": ["finish(session_id), then follow finish_status if it answers with a job_id."]}
    assert "shoot_beat" in verdict(mods, [HIGH], status="recording")["next"][0]


def test_retakes_not_in_place_come_first(mods):
    server, store = mods
    sid = "s-retake"
    store.create_session(sid, "https://e.example", "v", 320, 176, "/tmp", "edge", None, None, "", "", "#1f2a44", "", "L", "none", "cuts")
    beats = [{"number": 1, "label": "x", "line": "l", "state": {"attempts": [
        {"ok": True, "visible": [0], "steps": [0]}, {"ok": True, "visible": [4], "steps": [4]}]}}]
    store.save_storyboard("plan-r", beats, [], 60, sid)
    result = server._verdict(sid, {"status": "finished", "start_args": {}}, [HIGH], [])
    assert result["verdict"] == "fix" and "apply_retakes" in result["next"][0]
    beats[0]["state"]["placed"] = True
    store.update_storyboard_beats("plan-r", beats)
    assert "apply_retakes" not in server._verdict(sid, {"status": "finished", "start_args": {}}, [HIGH], [])["next"][0]


def test_a_fresh_take_counts_and_is_not_passed_on_as_a_setting(mods, monkeypatch):
    server, store = mods
    sid = "s-first"
    store.create_session(sid, "https://e.example", "v", 320, 176, "/tmp", "edge", None, None, "", "", "#1f2a44", "", "L", "none", "cuts")
    store.create_session("s-second", "https://e.example", "v", 320, 176, "/tmp", "edge", None, None, "", "", "#1f2a44", "", "L", "none", "cuts")
    store.save_storyboard("plan-f", [{"number": 1, "label": "x", "line": "l", "shot": "card"}], [], 60, sid)
    store.update_session_start_args(sid, {"start_url": "https://e.example", "signature": {"name": "x"}})
    seen = {}

    async def fake_start(**kwargs):
        seen.update(kwargs)
        return {"session_id": "s-second"}

    monkeypatch.setattr(server, "start_session", fake_start)
    asyncio.run(server.fresh_take(sid))
    assert "generation" not in seen and "signature" not in seen
    assert store.get_session("s-second")["start_args"]["generation"] == 1


def test_the_server_tells_any_client_where_to_start(mods):
    server, _ = mods
    assert "director_guide" in server.mcp.instructions and "verdict" in server.mcp.instructions

"""What the Sluss films taught: test data only, plans an agent can read back,
a person's approval, framing by panels, removing replaced takes, long holds."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from reel_studio import retention, sensitive, server, store
from reel_studio import storyboard as boards
from reel_studio.camera import Camera, panel_is_cut
from reel_studio import review


@pytest.fixture(autouse=True)
def test_list(monkeypatch):
    # The real list is fetched when the image is built; tests carry their own.
    monkeypatch.setattr(sensitive, "test_numbers", lambda: frozenset({"198112182392"}))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "t.db"))
    return tmp_path


# ------------------------------------------------------------- sensitive data

def kinds(text):
    return [(hit["kind"], hit["blocks"]) for hit in sensitive.scan(text)]


def test_a_personal_number_outside_the_test_list_is_stopped():
    assert kinds("customer 811218-9876 complained") == [("personal_number", True)]
    assert kinds("customer 198112189876 complained") == [("personal_number", True)]


def test_a_number_on_the_test_list_passes():
    assert sensitive.scan("customer 811218-2392 complained") == []
    assert sensitive.scan("customer 198112182392 complained") == []


def test_a_wrong_check_digit_is_no_personal_number():
    assert sensitive.scan("order 811218-9877") == []


def test_ten_digits_without_a_separator_are_left_alone():
    assert sensitive.scan("call 8112189876 now") == []


def test_a_samordningsnummer_counts():
    # Born the 18th, day 78. Check digit computed for this number.
    assert kinds("id 811278-" + _with_luhn("811278", "987")) == [("personal_number", True)]


def _with_luhn(date, three):
    for last in "0123456789":
        if sensitive._luhn(date + three + last):
            return three + last
    raise AssertionError


def test_cards_and_secrets_block_and_mail_does_not():
    assert kinds("card 4539 1488 0343 6467") == [("card_number", True)]
    assert kinds("ghp_" + "a" * 36) == [("secret", True)]
    assert kinds("write to anna.svensson@example.se") == [("email", False)]
    assert sensitive.scan("write to info@sluss.eu or a@example.com") == []


def test_without_the_list_every_number_is_stopped(monkeypatch):
    monkeypatch.setattr(sensitive, "test_numbers", lambda: frozenset())
    hits = sensitive.scan("customer 811218-2392")
    assert hits and hits[0]["blocks"] and "no copy" in hits[0]["label"]


def test_the_list_is_fetched_beside_the_code():
    from reel_studio import fetch_test_numbers
    assert fetch_test_numbers.TEST_NUMBERS_FILE == sensitive.TEST_NUMBERS_FILE


def test_a_report_never_repeats_the_value():
    hit = sensitive.scan("811218-9876")[0]
    assert "9876" not in hit["value"]


def test_the_plan_refuses_a_real_looking_number(db):
    beats = [{"shot": "card", "text": "Hi", "line": "Hello", "background": "brand"},
             {"shot": "live_answer", "url": "https://example.com", "line": "A number.",
              "input_text": "Case for 811218-9876"}]

    async def probe(url):
        return {"status": 200, "final_url": url, "title": "x", "login_gate": False, "paywall": False}

    result = asyncio.run(boards.check_storyboard(beats, probe=probe))
    items = [item for item in result["checklist"] if item["category"] == "sensitive_in_storyboard"]
    assert items and items[0]["severity"] == "fix" and not result["ready"]


def test_only_steps_in_the_film_are_counted():
    steps = [{"idx": 1, "offscreen": 1, "ok": 1}, {"idx": 2, "offscreen": 0, "ok": 1}]
    texts = {1: "811218-9876", 2: "811218-2392"}
    assert sensitive.findings(steps, texts) == []
    found = sensitive.findings(steps, {2: "811218-9876"})
    assert found[0]["category"] == "sensitive_on_screen" and found[0]["severity"] == "high"


def test_a_framed_shot_is_judged_by_its_frame():
    steps = [{"idx": 2, "offscreen": 0, "ok": 1}]
    page = {2: "sidebar: customer 811218-9876"}
    assert sensitive.findings(steps, page, {2: "The answer in frame"}) == []
    assert sensitive.findings(steps, page)
    camera = Camera(1920, 1080, 1.0, [{"t": 5.0, "zoom": 1.5, "cx": 900.0, "cy": 500.0, "facts": {"frame_text": "in frame"}}])
    assert review.frame_texts(camera, [{"idx": 2, "offset_seconds": 5.02}]) == {2: "in frame"}


def test_a_finding_stops_the_verdict_at_a_person(db):
    finding = {"category": "sensitive_on_screen", "severity": "high", "message": "m", "fix": "f", "beat": 2}
    verdict = server._verdict("x", {"status": "finished"}, [finding], [])
    assert verdict["verdict"] == "needs_human"


# ----------------------------------------------------------- reading the plan

def _store_plan(session_id=None):
    beats = [{"shot": "card", "line": "One.", "label": "a", "text": "A", "background": "brand", "number": 1,
              "state": {"status": "ok", "attempts": [{"steps": [0], "visible": [0], "ok": True}]}},
             {"shot": "card", "line": "Two.", "label": "b", "text": "B", "background": "brand", "number": 2,
              "state": {"status": "ok", "attempts": []}}]
    store.save_storyboard("p1", beats, [], 60, session_id)
    return "p1"


def test_get_plan_reads_the_beats_back(db):
    _store_plan()
    plan = asyncio.run(server.get_plan(plan_id="p1"))
    assert [beat["line"] for beat in plan["beats"]] == ["One.", "Two."]
    assert plan["beats"][0]["number"] == 1 and plan["beats"][0]["shot_state"]["takes"] == 1


def test_edit_beat_changes_one_field_only(db, monkeypatch):
    _store_plan()

    async def check(beats, **_):
        return {"beats": [dict(beat, number=i + 1) for i, beat in enumerate(beats)], "checklist": [], "ready": True,
                "estimated_seconds": 1.0}

    monkeypatch.setattr(boards, "check_storyboard", check)
    result = asyncio.run(server.edit_beat(2, {"line": "Changed."}, plan_id="p1"))
    assert result["ok"] and result["changed"] == ["line"]
    beats = store.get_storyboard("p1")["beats"]
    assert beats[0]["line"] == "One." and beats[0]["state"]["attempts"]
    assert beats[1]["line"] == "Changed."


def test_edit_beat_refuses_an_unknown_field(db):
    _store_plan()
    result = asyncio.run(server.edit_beat(1, {"nonsense": 1}, plan_id="p1"))
    assert not result["ok"] and result["error"]["type"] == "unknown_field"


def test_there_is_no_tool_to_approve():
    names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]
    assert "get_plan" in names and "edit_beat" in names and "purge_replaced_takes" in names
    assert not [name for name in names if "approve" in name or "publish" in name]


# ------------------------------------------------------------------ framing

CAM = Camera(1920, 1080, 1.0)
PANELS = [{"x": 180, "y": 480, "width": 1160, "height": 530}, {"x": 1370, "y": 480, "width": 520, "height": 530}]
FOCUS = {"x": 1410, "y": 700, "width": 120, "height": 24}


def test_the_focus_panel_is_shown_whole():
    framed = CAM.frame_panels(PANELS, FOCUS, 1.9, keep_zoom=True)
    assert framed["zoom"] == 1.9
    view = CAM.view((1.9, framed["cx"], 540.0))
    assert view["x"] <= PANELS[1]["x"] and PANELS[1]["x"] + PANELS[1]["width"] <= view["x"] + view["w"]


def test_a_default_camera_comes_down_until_the_row_fits():
    framed = CAM.frame_panels(PANELS, FOCUS, 1.3)
    assert framed["zoom"] == pytest.approx(framed["row_camera"], abs=0.01)
    view = CAM.view((framed["zoom"], framed["cx"], 540.0))
    assert not any(panel_is_cut(panel, view["x"], view["x"] + view["w"]) for panel in PANELS)


def test_a_focus_in_no_panel_keeps_the_old_frame():
    assert CAM.frame_panels(PANELS, {"x": 10, "y": 10, "width": 10, "height": 10}, 1.5) == {}


def _key(zoom=1.9, **facts):
    return {"t": 10.0, "zoom": zoom, "cx": 1400.0, "cy": 540.0, "facts": facts}


def _shot_step():
    return [{"idx": 3, "offset_seconds": 10.0, "offscreen": 0, "ok": 1, "narration_text": "x", "action_type": "annotate"}]


def test_a_panel_cut_by_the_frame_is_reported_with_the_camera_that_fits():
    camera = Camera(1920, 1080, 1.0, [_key(panels=PANELS, row_camera=1.12, panel_camera=3.0, cut=[], lines=10, cut_count=0)])
    found = [f for f in review.shot_findings(camera, _shot_step()) if f["category"] == "panel_cut_at_edge"]
    assert found and found[0]["severity"] == "low" and found[0]["measured"]["whole_row_camera"] == 1.12


def test_small_text_is_not_mended_by_cutting_its_panel():
    camera = Camera(1920, 1080, 1.0, [_key(zoom=1.9, font_px=10.0, panels=[], panel_camera=1.8, cut=[], lines=1, cut_count=0)])
    found = [f for f in review.shot_findings(camera, _shot_step()) if f["category"] == "focus_text_small"]
    assert "page zoom" in found[0]["fix"]


def test_the_page_menus_do_not_count_as_cut_text():
    assert "PAGE_CHROME" in review.SHOT_FACTS_JS and "aside" in review.SHOT_FACTS_JS


# ------------------------------------------------------------------ holds

def test_a_long_line_on_a_shot_gets_a_second_move():
    camera = Camera(1920, 1080, 1.0, [{"t": 10.0, "zoom": 1.3, "cx": 900.0, "cy": 500.0}])
    moves = camera.hold_moves([(10.0, 12.0)])
    assert len(moves) == 1 and moves[0]["t"] == 16.0 and moves[0]["zoom"] == pytest.approx(1.391)


def test_short_lines_wide_shots_and_busy_shots_are_left_alone():
    camera = Camera(1920, 1080, 1.0, [{"t": 10.0, "zoom": 1.3, "cx": 900.0, "cy": 500.0}])
    assert camera.hold_moves([(10.0, 5.0)]) == []
    assert Camera(1920, 1080, 1.0, [{"t": 10.0, "zoom": 1.0, "cx": 960.0, "cy": 540.0}]).hold_moves([(10.0, 12.0)]) == []
    busy = Camera(1920, 1080, 1.0, [{"t": 10.0, "zoom": 1.3, "cx": 900.0, "cy": 500.0}, {"t": 15.0, "zoom": 1.0, "cx": 960.0, "cy": 540.0}])
    assert busy.hold_moves([(10.0, 12.0)]) == []


def test_the_move_is_for_the_render_only():
    camera = Camera(1920, 1080, 1.0, [{"t": 10.0, "zoom": 1.3, "cx": 900.0, "cy": 500.0}, {"t": 30.0, "zoom": 1.0, "cx": 960.0, "cy": 540.0}])
    moved = camera.with_hold_moves([(10.0, 12.0)])
    assert len(camera.keys) == 2 and len(moved.keys) == 3 and [k["t"] for k in moved.keys] == sorted(k["t"] for k in moved.keys)
    assert moved.keys[1]["ease"] > 1.0


def test_the_hold_move_is_not_reported_as_another_page():
    step = {"idx": 1, "offset_seconds": 0.0, "offscreen": 0, "ok": 1, "narration_text": "Words here.", "action_type": "annotate"}
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 12.0}]
    bright, dark = bytes([200, 40] * 4800), bytes([40, 200] * 4800)
    frames = [bright] * 24 + [dark] * 24
    assert [f for f in review.film_findings(frames, parts, [step]) if f["category"] == "picture_changes_under_line"]
    assert not [f for f in review.film_findings(frames, parts, [step], holds={1: [5.5]}) if f["category"] == "picture_changes_under_line"]


# ------------------------------------------------------------------ approval

def _finished(sid="abc123"):
    with store._lock, store._connect() as connection:
        connection.execute(
            "INSERT INTO sessions (id, start_url, status, voice, width, height, output_dir, created_at, finished_at, duration_seconds, video_path) "
            "VALUES (?, 'https://e.com', 'finished', 'v', 1280, 720, '/x', '2026-01-01', '2026-01-01', 10.0, '/x/video.mp4')", (sid,))
    return sid


def test_a_film_is_not_public_until_a_person_approves_it(db):
    store.init_schema()
    sid = _finished()
    assert not store.list_finished_sessions(approved=True)
    assert [row["id"] for row in store.list_finished_sessions(approved=False)] == [sid]
    assert store.set_approved(sid, True)
    assert [row["id"] for row in store.list_finished_sessions(approved=True)] == [sid]
    assert store.set_approved(sid, False) and not store.list_finished_sessions(approved=True)
    assert not store.set_approved("nosuch", True)


def test_films_public_before_approval_existed_stay_public(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "old.db"
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(db_path))
    store.init_schema()
    sid = _finished()
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE sessions DROP COLUMN approved_at")
    store.init_schema()
    assert [row["id"] for row in store.list_finished_sessions(approved=True)] == [sid]


# ------------------------------------------------------------------ removal

def test_deleting_a_film_deletes_its_plan(db):
    store.init_schema()
    sid = _finished("dead01")
    _store_plan(sid)
    store.delete_session(sid)
    assert store.get_storyboard("p1") is None


def test_orphan_plans_are_removed(db):
    store.init_schema()
    _store_plan("gone")
    assert store.get_storyboard("p1")["session_id"] == "gone"
    assert store.delete_orphan_storyboards() == 1 and store.get_storyboard("p1") is None


STEPS = [
    {"idx": 0, "offset_seconds": 1.0, "started_seconds": 0.5, "offscreen": 0, "narration_text": "a"},
    {"idx": 1, "offset_seconds": 3.0, "started_seconds": 2.5, "offscreen": 1},
    {"idx": 2, "offset_seconds": 5.0, "started_seconds": 4.5, "offscreen": 1, "narration_text": "old"},
    {"idx": 3, "offset_seconds": 8.0, "started_seconds": 7.5, "offscreen": 1},
    {"idx": 4, "offset_seconds": 10.0, "started_seconds": 9.5, "offscreen": 0, "narration_text": "new"},
]


def _plan_with_retake(placed=True):
    return {"plan_id": "p1", "beats": [{"number": 1, "state": {"status": "ok", "placed": placed, "attempts": [
        {"steps": [1, 2], "visible": [2], "ok": True}, {"steps": [3, 4], "visible": [4], "ok": True}]}}]}


def test_replaced_takes_are_found_and_merged():
    indexes, ranges = retention.replaced_takes(STEPS, _plan_with_retake())
    assert indexes == [1, 2] and ranges == [(3.0, 7.5)]


def test_a_line_running_into_the_next_take_keeps_its_picture():
    steps = [{"idx": 0, "offset_seconds": 1.0, "started_seconds": 0.5, "offscreen": 0, "narration_duration": 3.0},
             *[dict(step) for step in STEPS[1:]]]
    _, ranges = retention.replaced_takes(steps, _plan_with_retake())
    assert ranges == [(6.0, 7.5)]


def test_a_take_not_yet_in_place_is_kept():
    assert retention.replaced_takes(STEPS, _plan_with_retake(placed=False)) == ([], [])


def test_the_purge_blanks_the_take_and_forgets_its_text(db):
    store.init_schema()
    sid = "cafe01"
    with store._lock, store._connect() as connection:
        connection.execute("INSERT INTO sessions (id, start_url, status, voice, width, height, output_dir, created_at) VALUES (?, 'https://e.com', 'finished', 'v', 1280, 720, '/x', 'now')", (sid,))
        for step in STEPS:
            connection.execute(
                "INSERT INTO steps (session_id, idx, action_type, offset_seconds, started_seconds, offscreen, narration_text, ok, created_at) "
                "VALUES (?, ?, 'annotate', ?, ?, ?, ?, 1, 'now')",
                (sid, step["idx"], step["offset_seconds"], step["started_seconds"], step["offscreen"], step.get("narration_text")))
    directory = Path(db) / sid
    directory.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=white:s=320x180:d=12:r=25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(directory / "screen.mp4")], check=True)
    (directory / review.PAGE_TEXT_FILE).write_text(
        "".join(json.dumps({"step": index, "text": f"text {index}"}) + "\n" for index in (0, 2, 4)))
    plan = _plan_with_retake()
    store.save_storyboard("p1", plan["beats"], [], 60, sid)
    session = store.get_session(sid)
    result = retention.purge_replaced_takes(sid, session["steps"], plan, directory)
    assert result["ok"] and result["steps_removed"] == 2
    assert [json.loads(line)["step"] for line in (directory / review.PAGE_TEXT_FILE).read_text().splitlines()] == [0, 4]
    kept = store.get_session(sid)["steps"]
    assert [step["idx"] for step in kept] == [0, 1, 2, 3, 4], "the renderer counts steps by place"
    assert kept[2]["narration_text"] == "" and kept[2]["offscreen"] and kept[4]["narration_text"] == "new"
    attempts = store.get_storyboard("p1")["beats"][0]["state"]["attempts"]
    assert attempts[0]["purged"] and not attempts[1].get("purged")
    assert retention.replaced_takes(kept, store.get_storyboard("p1")) == ([], []), "nothing to do twice"

    def brightness(at):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(directory / "screen.mp4"), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        return sum(raw) / len(raw)

    assert brightness(1.0) > 200 and brightness(5.0) < 40 and brightness(9.0) > 200

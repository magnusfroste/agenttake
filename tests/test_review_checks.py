"""The review catches what a human caught filming "Hermes runs Nordbrygg"
(16 takes, 2026-10-24/25): takes that skipped beats, narrated over a page
that had not loaded and left a menu open scored 95-100. Now the director
hands over its beats, the picture under every line is measured, every step
records the menus on screen, and contact_sheet shows the whole film."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("review")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "review.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store


BEATS = [
    {"line": "Every morning I go through Nordbrygg.", "label": "hook"},
    {"line": "Every visitor gets an answer right away.", "label": "chat"},
    {"line": "Every conversation lands in FlowBox.", "label": "flowbox"},
]


def _new_session(store, session_id, beats=None, output_dir="/tmp"):
    store.create_session(session_id, "https://example.com", "en-US-JennyNeural", 1920, 1080,
                         output_dir, "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none",
                         beats=beats)


def _step(store, session_id, narration, offset, url="https://example.com", ok=True,
          offscreen=False, overlays=None, action="click"):
    store.append_step(session_id, action, "button:x", url, "t", narration, 1.0 if narration else 0.0,
                      offset, None, ok, None if ok else "timeout", "en-US-JennyNeural",
                      offscreen=offscreen, open_overlays=overlays)


def _step_row(idx, narration="", url="https://example.com", overlays=None, offscreen=False):
    return {"idx": idx, "narration_text": narration, "url": url, "ok": True,
            "offscreen": offscreen, "open_overlays": overlays}


def _categories(review):
    return [f["category"] for f in review["findings"]]


# Beats ---------------------------------------------------------------------

def test_a_line_matches_its_beat_whatever_its_case_and_punctuation():
    from reel_studio.review import compare_beats

    steps = [_step_row(0, "every morning, I go through Nordbrygg"),
             _step_row(1, "Every visitor gets an answer — right away!"),
             _step_row(2, "Every conversation lands in FlowBox.")]
    result = compare_beats(BEATS, steps)
    assert result["in_order"] == 3
    assert not result["missing"] and not result["out_of_order"] and not result["repeated"]


def test_a_skipped_beat_is_missing_and_a_swapped_one_out_of_order():
    from reel_studio.review import compare_beats

    swapped = [_step_row(0, BEATS[2]["line"]), _step_row(1, BEATS[0]["line"])]
    result = compare_beats(BEATS, swapped)
    assert [b["label"] for b in result["missing"]] == ["chat"]
    assert [b["label"] for b in result["out_of_order"]] == ["flowbox"]
    assert result["out_of_order"][0]["steps"] == [0]


def test_a_line_that_never_reached_the_video_does_not_count():
    from reel_studio.review import compare_beats

    steps = [_step_row(0, BEATS[0]["line"]),
             {**_step_row(1, BEATS[1]["line"]), "ok": False},
             _step_row(2, BEATS[2]["line"], offscreen=True),
             _step_row(3, "A line nobody planned.")]
    result = compare_beats(BEATS, steps)
    assert [b["label"] for b in result["missing"]] == ["chat", "flowbox"]
    assert result["unplanned_steps"] == [3]


def test_a_beat_spoken_twice_is_repeated():
    from reel_studio.review import compare_beats

    steps = [_step_row(0, BEATS[0]["line"]), _step_row(1, BEATS[0]["line"]),
             _step_row(2, BEATS[1]["line"]), _step_row(3, BEATS[2]["line"])]
    result = compare_beats(BEATS, steps)
    assert result["in_order"] == 3
    assert result["repeated"] == [{"beat": 0, "label": "hook", "line": BEATS[0]["line"], "steps": [0, 1]}]


def test_a_beat_recorded_again_and_moved_into_place_is_in_order():
    from reel_studio.review import compare_beats

    # The chat line went wrong: it is hidden, recorded again at the end and
    # moved to where it belongs (edit_step hidden, then position).
    steps = [_step_row(0, BEATS[0]["line"]),
             _step_row(1, BEATS[1]["line"], offscreen=True),
             _step_row(2, BEATS[2]["line"]),
             {**_step_row(3, BEATS[1]["line"]), "position": 1}]
    steps[0]["position"], steps[1]["position"], steps[2]["position"] = 0, 2, 3
    result = compare_beats(BEATS, steps)
    assert result["in_order"] == 3
    assert not result["missing"] and not result["out_of_order"] and not result["repeated"]


def test_review_flags_a_skipped_beat_and_it_cannot_be_excellent(mods):
    server, store = mods
    _new_session(store, "s-skipped", beats=BEATS)
    _step(store, "s-skipped", BEATS[0]["line"], 0.0)
    _step(store, "s-skipped", BEATS[2]["line"], 2.0)
    review = asyncio.run(server.review_session("s-skipped"))
    missing = [f for f in review["findings"] if f["category"] == "beat_missing"]
    assert len(missing) == 1 and missing[0]["severity"] == "high"
    assert "'chat'" in missing[0]["message"] and BEATS[1]["line"] in missing[0]["message"]
    assert review["quality_status"] != "excellent"
    assert review["beats"]["planned"] == 3 and review["beats"]["in_order"] == 2


def test_review_of_a_take_that_follows_its_beats_finds_nothing_about_them(mods):
    server, store = mods
    _new_session(store, "s-followed", beats=BEATS)
    for offset, beat in enumerate(BEATS):
        _step(store, "s-followed", beat["line"], float(offset))
    review = asyncio.run(server.review_session("s-followed"))
    assert not [c for c in _categories(review) if c.startswith("beat_")]
    assert review["quality_status"] == "excellent"


def test_get_status_lists_the_beats_still_to_come(mods):
    server, store = mods
    _new_session(store, "s-progress", beats=BEATS)
    _step(store, "s-progress", BEATS[0]["line"], 0.0)
    status = asyncio.run(server.get_status("s-progress"))
    assert status["beats"] == {"planned": 3, "recorded_in_order": 1,
                               "to_come": ["chat", "flowbox"], "out_of_order": []}


def test_start_session_refuses_bad_beats_before_it_speaks(mods, monkeypatch):
    server, _ = mods

    async def must_not_speak(*args, **kwargs):
        raise AssertionError("bad beats are refused before the voice check")

    monkeypatch.setattr(server, "synthesize", must_not_speak)
    no_line = asyncio.run(server.start_session("https://example.com", beats=[{"label": "hook"}]))
    assert no_line["error"]["type"] == "invalid_beats"
    blank = asyncio.run(server.start_session("https://example.com", beats=[{"line": "  "}]))
    assert blank["error"]["type"] == "invalid_beats"
    twice = asyncio.run(server.start_session("https://example.com", beats=[BEATS[0], BEATS[0]]))
    assert "repeats an earlier line" in twice["error"]["message"]


def test_start_session_stores_the_beats(mods, monkeypatch, tmp_path):
    server, store = mods

    async def speak(*args, **kwargs):
        return None

    class Recording:
        session_id = "s-planned"
        directory = tmp_path

        def touch(self):
            pass

    async def create(*args, **kwargs):
        return Recording()

    monkeypatch.setattr(server, "synthesize", speak)
    monkeypatch.setattr(server.BrowserSession, "create", create)
    try:
        result = asyncio.run(server.start_session("https://example.com", beats=BEATS))
    finally:
        server.sessions.pop("s-planned", None)
    assert result["beats"] == 3
    assert store.get_session("s-planned")["beats"] == BEATS


def test_the_director_hands_over_its_beats():
    from reel_studio import server

    assert "start_session(beats=" in server.DIRECTOR_PROMPT
    assert "contact_sheet" in server.DIRECTOR_PROMPT
    assert "beats=" in server.build_llms_txt("https://take.example.com")
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    beats = tools["start_session"].inputSchema["properties"]["beats"]
    assert "exact narration" in beats["description"]
    assert "contact_sheet" in tools


# Menus left open -------------------------------------------------------------

def test_a_menu_left_open_under_a_later_line_is_flagged():
    from reel_studio.review import overlay_findings

    steps = [
        _step_row(0, "The dashboard.", overlays={"menu": 1}),           # a nav built as a menu
        _step_row(1, "Open the model menu.", overlays={"menu": 2}),     # opens one: about it
        _step_row(2, "Every order lands here.", overlays={"menu": 2}),  # still open
        _step_row(3, "", overlays={"menu": 1}),                         # Escape
        _step_row(4, "And the shop.", overlays={"menu": 1}),
    ]
    findings = overlay_findings(steps)
    assert [f["location"] for f in findings] == ["step[2]"]
    assert findings[0]["severity"] == "medium" and "menu" in findings[0]["message"]


def test_what_a_page_arrives_with_is_never_left_open():
    from reel_studio.review import overlay_findings

    steps = [
        _step_row(0, "One.", url="https://a.example", overlays={"listbox": 1}),
        _step_row(1, "Two.", url="https://a.example", overlays={"listbox": 1}),
        _step_row(2, "Three.", url="https://b.example", overlays={"popover": 1}),
        _step_row(3, "Four.", url="https://b.example", overlays={"popover": 1}),
        _step_row(4, "Five.", url="https://b.example", overlays=None),  # not measured
    ]
    assert overlay_findings(steps) == []


def test_a_silent_step_under_an_open_menu_is_not_flagged():
    from reel_studio.review import overlay_findings

    steps = [_step_row(0, "Start.", overlays={}),
             _step_row(1, "Open it.", overlays={"popover": 1}),
             _step_row(2, "", overlays={"popover": 1}),
             _step_row(3, "", offscreen=True, overlays={"popover": 1})]
    assert overlay_findings(steps) == []


class Acting:
    """A stand-in session whose steps report the menus on screen."""

    def __init__(self, results):
        self.voice = "en-US-JennyNeural"
        self.refs = {}
        self.refs_stale = False
        self._results = list(results)

    def touch(self):
        pass

    async def act(self, action, narration=""):
        return self._results.pop(0), None


def test_act_stores_the_menus_on_screen_and_the_review_reads_them(mods):
    server, store = mods
    _new_session(store, "s-menu")
    page = {"ok": True, "url": "https://example.com/app", "title": "App", "offset_seconds": 0.0,
            "narration_duration": 1.0}
    server.sessions["s-menu"] = Acting([
        {**page, "open_overlays": {}},
        {**page, "open_overlays": {"menu": 1}},
        {**page, "open_overlays": {"menu": 1}},
    ])
    try:
        for narration in ("The app.", "Open the menu.", "Every order lands here."):
            asyncio.run(server.act("s-menu", {"type": "click", "ref": "button:x"}, narration))
    finally:
        server.sessions.pop("s-menu", None)
    steps = store.get_session("s-menu")["steps"]
    assert [s["open_overlays"] for s in steps] == [{}, {"menu": 1}, {"menu": 1}]
    review = asyncio.run(server.review_session("s-menu"))
    assert "overlay_left_open" in _categories(review)
    # A medium finding alone leaves 85, but a menu on screen is not excellent.
    assert review["director_score"] == 85 and review["quality_status"] == "acceptable"


def test_the_engine_counts_menus_listboxes_and_radix_popovers():
    from reel_studio.review import OPEN_OVERLAYS_JS

    for selector in ("[role=menu]", "[role=listbox]", "[data-radix-popper-content-wrapper]"):
        assert selector in OPEN_OVERLAYS_JS
    engine = Path(__file__).resolve().parent.parent / "reel_studio" / "engine.py"
    assert "OPEN_OVERLAYS_JS" in engine.read_text()


# The picture under a line ----------------------------------------------------

def _frame(path: Path, filters: str, size: str = "1280x720", color: str = "white") -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s={size}:d=1", "-vf", filters or "null",
                    "-frames:v", "1", str(path)], check=True)
    return path


def _text(text: str, x: int, y: int, size: int = 22, color: str = "black") -> str:
    from reel_studio.render import FONT_PATH
    return f"drawtext=fontfile={FONT_PATH}:text='{text}':x={x}:y={y}:fontsize={size}:fontcolor={color}"


@needs_ffmpeg
def test_a_page_that_has_not_loaded_reads_as_nearly_empty(tmp_path):
    from reel_studio.review import NEAR_EMPTY_DETAIL, frame_detail, frame_pixels

    spinner = _frame(tmp_path / "spinner.png",
                     "drawbox=x=0:y=0:w=iw:h=56:color=0x1f2a44:t=fill,"
                     "drawbox=x=628:y=348:w=24:h=24:color=0x2563eb:t=4")
    # A caption, or two lines of burned-in subtitles, over the same blank page.
    subtitled = _frame(tmp_path / "subtitled.png",
                       "drawbox=x=0:y=0:w=iw:h=56:color=0x1f2a44:t=fill,"
                       + _text("Every visitor gets an answer right away, on any AI model,", 220, 610, 30) + ","
                       + _text("from the knowledge the company chooses to share.", 270, 650, 30))
    card_lines = ",".join(_text(line, 520, 210 + 34 * n, 20, "white") for n, line in enumerate(
        ["Nordbrygg AB", "Welcome", "Sign in to continue.", "Email Address", "name@company.com",
         "Password", "Sign In"]))
    sign_in = _frame(tmp_path / "sign-in.png",
                     "drawbox=x=480:y=180:w=320:h=280:color=0x1b2130:t=fill,"
                     "drawbox=x=480:y=180:w=320:h=280:color=0x303a52:t=2," + card_lines,
                     color="0x0d111a")
    assert frame_detail(frame_pixels(spinner)) < NEAR_EMPTY_DETAIL
    assert frame_detail(frame_pixels(subtitled)) < NEAR_EMPTY_DETAIL
    assert frame_detail(frame_pixels(sign_in)) > 2 * NEAR_EMPTY_DETAIL


@needs_ffmpeg
def test_review_flags_a_line_narrated_over_a_blank_page(mods, tmp_path):
    server, store = mods
    from reel_studio import render

    session_dir = tmp_path / "s-blank"
    session_dir.mkdir()
    video = session_dir / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=640x360:r=25:d=3",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
                    "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(video)],
                   check=True)
    _new_session(store, "s-blank", output_dir=str(session_dir))
    _step(store, "s-blank", "", 0.0, offscreen=True, action="goto")
    _step(store, "s-blank", "Here is the dashboard.", 0.5)
    _step(store, "s-blank", "And every order on it.", 3.5)
    store.finish_session("s-blank", str(video), None, 6.0)
    render.write_storyboard(session_dir, video, [
        {"kind": "step", "step": 0, "start": 0.0, "duration": 3.0},
        {"kind": "step", "step": 1, "start": 3.0, "duration": 3.0},
    ])
    review = asyncio.run(server.review_session("s-blank"))
    assert [frame["step"] for frame in review["step_frames"]] == [1, 2]
    assert all(Path(frame["path"]).is_file() for frame in review["step_frames"])
    blank = [f for f in review["findings"] if f["category"] == "blank_frame"]
    assert [f["location"] for f in blank] == ["step[1]"]
    assert blank[0]["severity"] == "high" and Path(blank[0]["frame"]).is_file()
    assert review["quality_status"] != "excellent"

    # Without storyboard stills the frames are cut from the video itself.
    shutil.rmtree(session_dir / render.STORYBOARD_DIR)
    again = asyncio.run(server.review_session("s-blank"))
    assert [f["location"] for f in again["findings"] if f["category"] == "blank_frame"] == ["step[1]"]
    assert all("review_frames" in frame["path"] for frame in again["step_frames"])


# Contact sheet ---------------------------------------------------------------

@needs_ffmpeg
def test_contact_sheet_tiles_the_finished_video(mods, monkeypatch):
    server, store = mods
    session_id = "c0ffee01"
    session_dir = Path(os.environ["REEL_OUTPUT_DIR"]) / session_id
    session_dir.mkdir()
    video = session_dir / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25", "-t", "7", "-pix_fmt", "yuv420p", str(video)],
                   check=True)
    _new_session(store, session_id, output_dir=str(session_dir))
    assert asyncio.run(server.contact_sheet(session_id))["error"]["type"] == "not_finished"
    assert asyncio.run(server.contact_sheet("nope"))["error"]["type"] == "unknown_session"
    # The stored length is short of the file's: the sheet goes by the file.
    store.finish_session(session_id, str(video), None, 5.0)
    monkeypatch.setenv("REEL_PUBLIC_BASE_URL", "https://take.example.com/")

    result = asyncio.run(server.contact_sheet(session_id, every_seconds=1))
    assert result["ok"] and result["every_seconds"] == 1
    assert (result["tiles"], result["columns"], result["rows"]) == (8, 6, 2)
    assert result["url"].startswith(f"https://take.example.com/videos/{session_id}/storyboard/contact-sheet.jpg?v=")
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", result["path"]],
                          capture_output=True, text=True, check=True).stdout.strip()
    # 640x360 scaled to 400 wide is 400x226: -2 keeps the height even.
    assert size == f"{6 * 400 + 5 * 4},{2 * 226 + 4}"

    from starlette.testclient import TestClient
    client = TestClient(server.BearerAuthMiddleware(server.mcp.streamable_http_app(), "secret"))
    response = client.get(f"/videos/{session_id}/storyboard/contact-sheet.jpg")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"


def test_a_long_film_is_spaced_out_on_its_contact_sheet():
    from reel_studio.review import CONTACT_SHEET_MAX_TILES, contact_sheet_interval

    assert contact_sheet_interval(90, 2) == 2
    assert contact_sheet_interval(30, 0.1) == 0.5
    assert 600 / contact_sheet_interval(600, 2) <= CONTACT_SHEET_MAX_TILES

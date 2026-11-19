"""Graphics over a live page: a callout and a lower third.

Magnus, after FlowWink take 2 (2026-11-17): a news programme does not only
frame what it talks about, it flashes a key figure or a quote beside it and
says where and when in a strap. Take had a callout only as a hand-driven
annotation drawn in the page, a lower third only on a still, and quotes only
as full-screen cards. A `system` beat now takes a `callout` and a `caption`,
burned in at render so the camera's moves do not move them.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from reel_studio import graphics
from reel_studio import pickup
from reel_studio import review
from reel_studio import storyboard as boards
from reel_studio.engine import BrowserSession
from reel_studio.graphics import (
    CALLOUT_AFTER_MOVE, CALLOUT_MIN_SECONDS, Layout, ass_events, callout_seconds, cue_share, frame_box,
    graphic_cues, said_at,
)
from reel_studio.render import subtitles_ass
from reel_studio import signature

PAGE = "https://app.example/admin"
LINE = "It qualifies the leads from today."


def system(**fields):
    return {"shot": "system", "url": PAGE, "wait_for_text": "Leads", "line": LINE, **fields}


# When a callout comes in ---------------------------------------------------

def test_a_callout_the_line_says_comes_in_on_its_words():
    assert said_at("the leads", LINE) == pytest.approx(LINE.index("the leads") / len(LINE))
    # Case and punctuation aside, but word for word.
    assert said_at("From today", LINE) is not None
    assert said_at("3 leads qualified", LINE) is None


def test_a_callout_the_line_does_not_say_comes_in_on_a_word_they_share():
    assert cue_share("3 leads qualified", LINE) == pytest.approx(LINE.index("leads") / len(LINE))
    # A short word is no tie: "it" and "the" are in every line.
    assert cue_share("Yours to decide", "It is the morning.") is None


def test_a_callout_waits_for_the_camera_and_leaves_time_to_be_read():
    # On its word, a little early.
    assert callout_seconds("the leads", LINE, 3.2, 0.0, 3.2) == pytest.approx(
        LINE.index("the leads") / len(LINE) * 3.2 - graphics.CALLOUT_LEAD, abs=0.01)
    # Not while the camera moves.
    assert callout_seconds("It qualifies", LINE, 3.2, 1.3, 3.2) == pytest.approx(1.3 + CALLOUT_AFTER_MOVE)
    # A word at the end of the line: early enough to be on screen CALLOUT_MIN_SECONDS.
    assert callout_seconds("today", LINE, 3.2, 0.0, 3.2) == pytest.approx(3.2 - CALLOUT_MIN_SECONDS)
    # Nothing in the line names it: as soon as the frame stands still.
    assert callout_seconds("Out of stock", LINE, 3.2, 0.0, 3.2) == CALLOUT_AFTER_MOVE


# The plan ------------------------------------------------------------------

def categories(beats):
    return [item["category"] for item in boards.parse_storyboard(beats)[1]]


def test_a_callout_and_a_caption_are_short():
    assert categories([system(callout="3 leads qualified", caption="02:14 · Leads")]) == []
    assert "long_callout" in categories([system(callout="Three new leads were qualified overnight by the agent")])
    assert "long_caption" in categories([system(caption="Tuesday night, a quarter past two, in the inbox")])


def test_a_callout_on_a_shot_that_shows_none_is_named():
    beats = [{"shot": "cta", "url": PAGE, "line": "Go there.", "callout": "Start free"}]
    assert categories(beats) == ["callout_not_used"]


def test_the_beat_schema_and_identity_know_the_callout():
    from reel_studio.server import _beat_identity

    assert "callout" in boards.beats_json_schema()["properties"]
    assert _beat_identity(system(callout="3 leads")) != _beat_identity(system(callout="4 leads"))
    assert _beat_identity(system(caption="02:14")) != _beat_identity(system(caption="02:15"))


def test_a_system_beat_hands_its_graphics_to_the_narrated_step():
    ops = boards.recipe(boards.parse_storyboard([system(callout="3 leads qualified", caption="02:14 · Leads")])[0][0])
    kinds = [op["op"] for op in ops]
    assert kinds.index("shot") < kinds.index("graphics") == len(ops) - 2
    assert ops[-2] == {"op": "graphics", "callout": "3 leads qualified", "caption": "02:14 · Leads",
                       "emphasis": None, "target_text": "Leads", "line": LINE}
    # A beat with neither is shot as it always was.
    assert "graphics" not in [op["op"] for op in boards.recipe(boards.parse_storyboard([system()])[0][0])]


# The shoot -----------------------------------------------------------------

class Live:
    def __init__(self, text):
        self.page = SimpleNamespace(url="about:blank")
        self.text = text
        self.graphics: list[tuple] = []
        self.cleared = 0

    async def dismiss_consent(self, *args):
        return None

    async def set_shot(self, *args, **kwargs):
        return {}

    async def page_text(self):
        return self.text

    def set_graphics(self, callout=None, caption=None, target_text=None, emphasis=None):
        if callout or caption:
            self.graphics.append((callout, caption, target_text))
        else:
            self.cleared += 1


def run(live, beat, graphics_result=None):
    async def act(session_id, action, narration):
        if action["type"] == "goto":
            live.page.url = action["url"]
        return {"ok": True, **({"graphics": graphics_result} if narration and graphics_result else {})}, None

    return asyncio.run(boards.run_ops(live, "s", boards.recipe(boards.parse_storyboard([beat])[0][0]), act))


def test_a_callout_shows_what_the_page_says():
    live = Live("Leads\n3 leads qualified\nSales")
    outcome = run(live, system(callout="3 Leads Qualified"))
    assert outcome["ok"] and outcome["notes"] == []
    assert live.graphics == [("3 Leads Qualified", None, "Leads")]


def test_a_beat_starts_without_the_graphics_a_failed_beat_left():
    live = Live("Leads")
    run(live, system())
    assert live.cleared == 1 and live.graphics == []


def test_a_callout_shows_what_the_line_says():
    live = Live("Leads")
    assert run(live, system(callout="the leads from today"))["notes"] == []
    assert live.graphics[0][0] == "the leads from today"


def test_a_callout_with_a_claim_of_its_own_is_left_out_and_said_so():
    live = Live("Leads\n3 leads qualified")
    outcome = run(live, system(callout="40% more leads", caption="02:14 · Leads"))
    assert outcome["ok"]
    assert [note.split(":")[0] for note in outcome["notes"]] == ["callout_not_shown"]
    # The lower third is the director's own label: it stays.
    assert live.graphics == [(None, "02:14 · Leads", "Leads")]


def test_a_callout_whose_words_are_out_of_frame_is_said_in_the_notes():
    live = Live("Leads\n3 leads qualified")
    outcome = run(live, system(callout="3 leads qualified"), graphics_result={"dropped": "not in the frame"})
    assert outcome["notes"] == ["callout_not_shown: not in the frame"]


def test_a_box_is_placed_in_the_frame_the_camera_ends_on():
    view = {"x": 320.0, "y": 180.0, "w": 640.0, "h": 360.0, "zoom": 2.0}
    assert frame_box({"x": 480, "y": 270, "width": 64, "height": 18}, view) == [0.25, 0.25, 0.1, 0.05]
    # Its middle outside the frame: nothing to point at.
    assert frame_box({"x": 100, "y": 270, "width": 64, "height": 18}, view) is None
    assert frame_box(None, view) is None


class Words:
    """A session whose page has the words at a known box."""
    width, height = 1280, 720
    camera = None

    def __init__(self, box, wanted):
        self.box, self.pending_graphics = box, wanted

    async def _visible_text_target(self, text):
        return object() if self.box else None

    async def _text_box(self, target, text):
        return self.box


def entry(box, wanted, line=LINE, seconds=3.2, move=0.0):
    return asyncio.run(BrowserSession._graphic_entry(Words(box, wanted), line, seconds, move))


def test_the_engine_records_where_and_when():
    wanted = {"callout": "3 leads qualified", "caption": "02:14 · Leads", "target_text": "Leads"}
    made = entry({"x": 128, "y": 144, "width": 128, "height": 36}, wanted, move=1.3)
    assert made["callout"] == {"text": "3 leads qualified", "box": [0.1, 0.2, 0.1, 0.05], "at": 1.5}
    assert made["caption"] == {"text": "02:14 · Leads", "box": [0.1, 0.2, 0.1, 0.05]}


def test_the_engine_drops_a_callout_with_nothing_to_point_at():
    made = entry(None, {"callout": "3 leads qualified", "caption": "02:14 · Leads", "target_text": "Leads"})
    assert "callout" not in made and "Leads" in made["dropped"]
    assert made["caption"] == {"text": "02:14 · Leads"}


def test_a_rehearsal_without_a_voice_does_not_say_the_line_misses_the_emphasis():
    class Framed(Words):
        camera = SimpleNamespace(view=lambda: {"x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0, "zoom": 1.0})
    def made(line, seconds):
        session = Framed(None, {"emphasis": "routine work"})
        return asyncio.run(BrowserSession._graphic_entry(session, line, seconds, 0.0))
    said = "Self-hosted open models for the routine work."
    assert "emphasis_dropped" not in made(said, 0.0)
    assert "routine work" in made("Something else entirely.", 0.0)["emphasis_dropped"]
    assert made(said, 4.0)["emphasis"]["at"] > 0


def test_set_graphics_keeps_nothing_when_there_is_nothing_to_show():
    session = SimpleNamespace(pending_graphics={"callout": "old"})
    BrowserSession.set_graphics(session, None, "  ", "Leads")
    assert session.pending_graphics is None
    BrowserSession.set_graphics(session, " 3 leads ", None, "Leads")
    assert session.pending_graphics == {"callout": "3 leads", "caption": "", "target_text": "Leads", "emphasis": ""}


# The record ----------------------------------------------------------------

def test_graphics_are_recorded_by_step_offset_and_follow_a_pickup(tmp_path):
    film, picked = tmp_path / "film", tmp_path / "pickup"
    film.mkdir(), picked.mkdir()
    graphics.record_graphic(film, 4.0, {"caption": {"text": "02:14 · Leads"}})
    graphics.record_graphic(film, 4.0, {"caption": {"text": "02:15 · Leads"}})
    assert [e["caption"]["text"] for e in graphics.load_graphics(film)] == ["02:15 · Leads"]
    graphics.record_graphic(picked, 1.5, {"caption": {"text": "06:30 · My Day"}})
    pickup.take_in(film, picked, 60.0, None)
    assert [(e["offset"], e["caption"]["text"]) for e in graphics.load_graphics(film)] == [
        (4.0, "02:15 · Leads"), (61.5, "06:30 · My Day")]
    (film / graphics.GRAPHICS_FILE).write_text("not json")
    assert graphics.load_graphics(film) == []


# The film ------------------------------------------------------------------

BOX = [0.3, 0.3, 0.2, 0.05]


def parts_and_entries():
    parts = [
        {"kind": "step", "step": 0, "start": 0.0, "duration": 3.0},
        {"kind": "step", "step": 1, "start": 3.0, "duration": 3.0},
        {"kind": "step", "step": 2, "start": 6.0, "duration": 4.0},
    ]
    offsets = {0: 10.0, 1: 20.0, 2: 30.0}
    entries = [
        {"offset": 10.0, "caption": {"text": "02:14 · Inbox", "box": BOX}},
        {"offset": 20.0, "caption": {"text": "02:14 · Inbox"},
         "callout": {"text": "3 leads qualified", "box": BOX, "at": 1.2}},
        {"offset": 30.0, "caption": {"text": "06:30 · My Day"}},
    ]
    return parts, offsets, entries


def test_a_caption_runs_through_beats_that_share_it_and_a_callout_from_its_word():
    cues = graphic_cues(*parts_and_entries())
    assert [(c["kind"], c["start"], c["end"], c["text"]) for c in cues] == [
        ("caption", 0.0, 6.0, "02:14 · Inbox"),
        ("caption", 6.0, 10.0, "06:30 · My Day"),
        ("callout", 4.2, 6.0, "3 leads qualified"),
    ]


def test_a_part_ends_where_the_next_begins_and_a_step_with_no_graphics_shows_none():
    parts, offsets, entries = parts_and_entries()
    parts[1]["duration"] = 5.0          # a dissolve into the next part
    cues = graphic_cues(parts, offsets, entries[1:2])
    assert [(c["kind"], c["start"], c["end"]) for c in cues] == [("caption", 3.0, 6.0), ("callout", 4.2, 6.0)]
    assert graphic_cues(parts, offsets, []) == []


def test_a_callout_sits_over_its_words_clear_of_the_subtitles():
    layout = Layout(1280, 720)
    over = layout.callout("3 leads qualified", BOX)
    assert over["above"] and over["tip"] < 0.3 * 720
    # Words at the top of the frame: under them.
    under = layout.callout("3 leads qualified", [0.3, 0.02, 0.2, 0.05])
    assert not under["above"] and under["tip"] > 0.07 * 720
    # Words low in the frame: never in the subtitles' band.
    low = layout.callout("3 leads qualified", [0.3, 0.7, 0.2, 0.05])
    assert low["above"] and low["edge"] < layout.floor
    # At the frame's edge the label stays inside it and still points at the words.
    edge = layout.callout("3 leads qualified", [0.0, 0.4, 0.05, 0.05])
    assert edge["centre"] - layout.text_width("3 leads qualified", layout.callout_size) / 2 > 0
    assert layout.margin < edge["aim"] < 0.2 * 1280


def test_a_lower_third_moves_to_the_top_rather_than_cover_what_the_camera_frames():
    layout = Layout(1280, 720)
    assert not layout.caption_on_top("02:14 · Inbox", [BOX, None])
    assert layout.caption_on_top("02:14 · Inbox", [BOX, [0.02, 0.68, 0.3, 0.06]])


def test_the_script_draws_them_over_the_subtitles_in_the_accent():
    cues = graphic_cues(*parts_and_entries())
    script = subtitles_ass([(0.0, 3.0, "It qualifies the leads from today.")], 1280, 720, "soft", cues)
    assert "Style: Caption," in script and "Style: Callout," in script
    events = [line for line in script.splitlines() if line.startswith("Dialogue:")]
    assert sum("Caption" in line for line in events) == 2
    assert [line.rsplit("}", 1)[1] for line in events if ",Callout," in line] == ["3 leads qualified"]
    # A film without graphics gets the script it always got.
    plain = subtitles_ass([(0.0, 3.0, "A line.")], 1280, 720)
    assert "Callout" not in plain and plain.count("Dialogue:") == 1


# The look ------------------------------------------------------------------
#
# Magnus, the same evening: an agent should be able to choose between a few,
# and the choice belongs to the profile it runs the assignment in.

def styles_of(script):
    return {line.split(",")[0].removeprefix("Style: "): line.split(",") for line in script.splitlines()
            if line.startswith("Style: ")}


def script_in(style, monkeypatch, house=None):
    monkeypatch.setattr(signature, "current", lambda: house or signature.DEFAULT)
    return subtitles_ass([], 1280, 720, "soft", graphic_cues(*parts_and_entries()), style)


def test_the_default_look_flags_in_the_accent_on_the_ink(monkeypatch):
    callout = styles_of(script_in(None, monkeypatch))["Callout"]
    # Text, then the plate behind it (ASS colours are &HAABBGGRR): ink on the default yellow.
    assert (callout[3], callout[5], callout[15]) == ("&H00271810", "&H0066D1FF", "3")
    assert styles_of(script_in("label", monkeypatch)) == styles_of(script_in(None, monkeypatch))


def test_each_look_sets_its_own_plates_from_the_house_colours(monkeypatch):
    house = signature.parse({"name": "hermes", "accent": "#E3A23B", "ink": "#0B1B2B", "paper": "#F6F1E7"})[0]
    ink, paper, accent = "&H002B1B0B", "&H00E7F1F6", "&H003BA2E3"
    looks = {style: styles_of(script_in(style, monkeypatch, house)) for style in graphics.STYLES}
    assert (looks["label"]["Callout"][5], looks["label"]["Caption"][5]) == (accent, ink)
    assert (looks["ink"]["Callout"][3], looks["ink"]["Callout"][5]) == (paper, ink)
    assert (looks["paper"]["Callout"][3], looks["paper"]["Callout"][5]) == (ink, paper)
    # No plate: an outline, and the callout's text in the accent.
    assert (looks["bare"]["Callout"][3], looks["bare"]["Callout"][15]) == (accent, "1")
    assert len({tuple(look["Callout"]) for look in looks.values()}) == len(graphics.STYLES)
    # The callout's pointer is its plate's colour, the lower third's bar always the accent.
    events = [line for line in script_in("ink", monkeypatch, house).splitlines() if ",Shape," in line]
    assert [("&H2B1B0B&" in line, "&H3BA2E3&" in line) for line in events] == [
        (False, True), (False, True), (True, False)]


def test_the_house_names_its_look_and_a_film_can_take_another(monkeypatch):
    house, problems = signature.parse({"name": "hermes", "ink": "#0B1B2B", "graphics": "ink"})
    assert house.graphics == "ink" and problems == [] and house.to_dict()["graphics"] == "ink"
    assert signature.DEFAULT.graphics == "label"
    kept, problems = signature.parse({"graphics": "neon"})
    assert kept.graphics == "label" and "graphics is label, ink, paper, bare" in problems[0]
    assert styles_of(script_in(None, monkeypatch, house))["Callout"][5] == "&H002B1B0B"
    assert styles_of(script_in("paper", monkeypatch, house))["Callout"][5] == "&H00FFFFFF"


def test_start_session_and_rerender_take_a_look_and_the_session_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "look.db"))
    from reel_studio import server, store

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("start_session", "rerender"):
        described = json.dumps(tools[name].inputSchema["properties"]["graphics_style"])
        assert all(style in described for style in graphics.STYLES)
    assert "graphics_style" in server.DIRECTOR_PROMPT
    store.create_session("s-look", "https://example.com", "v", 320, 176, str(tmp_path / "s-look"))
    assert store.get_session("s-look")["graphics_style"] == ""
    store.update_session_graphics_style("s-look", "bare")
    assert store.get_session("s-look")["graphics_style"] == "bare"


def test_a_brace_in_a_label_cannot_break_the_script():
    lines = ass_events([{"kind": "caption", "start": 0.0, "end": 2.0, "text": "{\\b1}Inbox", "boxes": []}], 1280, 720)
    assert lines[-1].endswith("(\\\\b1)Inbox")


# The review ----------------------------------------------------------------

def test_the_review_names_a_callout_the_cut_left_too_short_to_read():
    found = review.graphic_findings([
        {"kind": "callout", "start": 4.2, "end": 5.0, "text": "3 leads qualified", "box": BOX},
        {"kind": "callout", "start": 7.0, "end": 9.0, "text": "Out of stock", "box": BOX},
        {"kind": "caption", "start": 0.0, "end": 0.5, "text": "02:14 · Inbox", "boxes": []},
    ])
    assert [(f["category"], f["measured"]["text"]) for f in found] == [("callout_too_brief", "3 leads qualified")]

"""A beat can be tried before anything is recorded. The Sluss films needed a
person with a Playwright script to find out what a page behind a sign-in
showed, how long an answer took and whether the frame held it. An agent has
no such script, so the tool does it."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel_studio.camera import Camera


@pytest.fixture
def mods(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "rehearse.db"))
    from reel_studio import server, store

    store.init_schema()
    server.sessions.clear()
    server.rehearsal_jobs.clear()
    server.finish_jobs.clear()
    return server, store, tmp_path


class Live:
    def __init__(self, directory: Path, fail: str | None = None, facts: dict | None = None):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.camera = Camera(1920, 1080, 1.0)
        self.page = SimpleNamespace(url="https://demo.example/page")
        self.acted: list[tuple[str, str, str]] = []
        self.fail = fail
        self.facts = facts
        self.aborted = False
        self.pending_facts = None

    async def act(self, action, narration=""):
        self.acted.append((action.type, action.text or action.url or "", narration))
        if self.fail == action.type:
            return {"ok": False, "error": {"type": "wait_timeout", "message": "did not appear"}}, None
        return {"ok": True}, None

    async def cover_of(self, text):
        return {"points": 10, "covered": 0, "by": None, "text": text}

    async def observe(self, detail):
        return None

    async def form_fields(self):
        return {"message": "e1", "username": "e2", "password": "e3"}

    async def dismiss_consent(self, *args):
        return None

    async def page_text(self):
        return "Where   your data went\n48% stayed"

    async def main_text(self):
        return ""

    async def set_shot(self, framing, zoom, ref, text, move, lift=False, fit=False):
        if self.facts:
            self.camera.keys.append({"t": 1.0, "zoom": zoom, "cx": 960.0, "cy": 540.0, "facts": self.facts})
        return {}

    async def frame_preview(self):
        still = self.directory / "preview.jpg"
        still.write_bytes(_JPEG)
        return still, {"x": 0, "y": 0, "w": 1920, "h": 1080, "zoom": 1.0}

    async def abort(self):
        self.aborted = True


_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e"
    "1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f00000105"
    "01010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411051221"
    "31410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5"
    "b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd0"
    "ffd9"
)


BEATS = [
    {"number": 1, "label": "hook", "shot": "card", "line": "A hook.", "text": "A hook"},
    {"number": 2, "label": "sign in", "shot": "login", "line": "", "url": "https://demo.example/login",
     "username": "hermes", "password_secret": "DEMO", "wait_for_text": "Where your data went"},
    {"number": 3, "label": "flows", "shot": "system", "line": "The flows.", "url": "https://demo.example/flows",
     "wait_for_text": "Where your data went", "zoom": 1.3},
    {"number": 4, "label": "proof", "shot": "live_answer", "line": "It stayed.", "url": "https://demo.example/chat",
     "input_text": "A prompt", "wait_for_text": "Stayed", "timeout": 5},
]


def _plan(store, plan_id="p1", beats=BEATS):
    store.save_storyboard(plan_id, [dict(beat) for beat in beats], [], 60)


def _use(monkeypatch, server, tmp_path, **kwargs):
    made = []

    async def create(*args, **options):
        live = Live(tmp_path / f"live-{len(made)}", **kwargs)
        made.append((live, args, options))
        return live

    monkeypatch.setattr(server.BrowserSession, "create", create)
    return made


def test_a_beat_behind_a_sign_in_is_tried_and_nothing_is_recorded(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)
    made = _use(monkeypatch, server, tmp)
    result = asyncio.run(server.rehearse_beats("p1", [3]))
    body = result.structuredContent
    assert body["status"] == "done"
    assert [(beat["beat"], beat["ok"]) for beat in body["beats"]] == [(2, True), (3, True)]
    report = body["beats"][1]
    assert report["url"] == "https://demo.example/page" and report["page_text"] == "Where your data went 48% stayed"
    assert report["seconds"] >= 0 and report["camera_frame"]["w"] == 1920
    assert [item.type for item in result.content] == ["text", "image"]
    live = made[0][0]
    assert live.aborted and not live.directory.exists()
    assert all(narration == "" for _, _, narration in live.acted)  # no voice is spent
    assert store.list_sessions() == []


def test_the_password_never_reaches_the_reply(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)
    made = _use(monkeypatch, server, tmp)
    result = asyncio.run(server.rehearse_beats("p1", [3]))
    assert any("{{secret:DEMO}}" in text for _, text, _ in made[0][0].acted)
    assert "secret" not in str(result.structuredContent)


def test_a_live_prompt_is_not_sent_unless_asked(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)
    made = _use(monkeypatch, server, tmp)
    body = asyncio.run(server.rehearse_beats("p1", [4])).structuredContent
    report = body["beats"][-1]
    assert report["ok"] and "not sent" in report["not_sent"]
    assert not any(kind == "type" and text == "A prompt" for kind, text, _ in made[0][0].acted)
    # The sign-in's Enter comes first; nothing is pressed once the chat is open.
    kinds = [kind for kind, _, _ in made[0][0].acted]
    opened = len(kinds) - 1 - kinds[::-1].index("goto")
    assert "press_key" not in kinds[opened:]


def test_send_live_really_sends_it(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)
    made = _use(monkeypatch, server, tmp)

    async def answer(self):
        return "A prompt\nStayed in the house"

    monkeypatch.setattr(Live, "page_text", answer)
    monkeypatch.setattr(server.boards, "ANSWER_QUIET_SECONDS", 0.0)
    monkeypatch.setattr(server.boards, "ANSWER_POLL_SECONDS", 0.0)
    asyncio.run(server.rehearse_beats("p1", [4], send_live=True))
    assert any(kind == "type" and text == "A prompt" for kind, text, _ in made[0][0].acted)


def test_a_failed_beat_says_why_and_still_shows_the_page(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store, beats=[BEATS[2]])
    _use(monkeypatch, server, tmp, fail="goto")
    result = asyncio.run(server.rehearse_beats("p1", [3]))
    report = result.structuredContent["beats"][0]
    assert not report["ok"] and "did not appear" in report["reason"]
    assert report["page_text"] and report["still"]


def test_a_frame_that_cuts_text_is_reported_on_the_beat(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store, beats=[BEATS[2]])
    facts = {"focus": {"x": 900.0, "y": 500.0, "width": 100.0, "height": 24.0}, "lines": 20, "cut_count": 1,
             "cut": [{"text": "verify offline", "side": "right", "top": 500.0, "bottom": 522.0}]}
    _use(monkeypatch, server, tmp, facts=facts)
    report = asyncio.run(server.rehearse_beats("p1", [3])).structuredContent["beats"][0]
    assert [finding["category"] for finding in report["findings"]] == ["text_cut_at_edge"]
    assert report["findings"][0]["fix"] and "location" not in report["findings"][0]


def test_a_card_needs_no_browser_work(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)
    made = _use(monkeypatch, server, tmp)
    report = asyncio.run(server.rehearse_beats("p1", [1])).structuredContent["beats"][0]
    assert report["ok"] and "nothing to rehearse" in report["notes"][0]
    assert made[0][0].acted == []


@pytest.mark.parametrize("plan_id,beats,expected", [
    ("nope", [3], "unknown_plan"),
    ("p1", [], "invalid_beats"),
    ("p1", [1, 2, 3, 4, 1, 2, 3], "invalid_beats"),
    ("p1", [9], "unknown_beat"),
])
def test_what_it_refuses(mods, plan_id, beats, expected):
    server, store, tmp = mods
    _plan(store)
    assert asyncio.run(server.rehearse_beats(plan_id, beats)).structuredContent["error"]["type"] == expected


def test_it_waits_its_turn_behind_a_recording(mods):
    server, store, tmp = mods
    _plan(store)
    server.sessions["live"] = object()
    try:
        assert asyncio.run(server.rehearse_beats("p1", [3])).structuredContent["error"]["type"] == "busy"
    finally:
        server.sessions.clear()


def test_a_long_rehearsal_answers_with_a_job(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store, beats=[BEATS[2]])
    _use(monkeypatch, server, tmp)
    real = Live.act

    async def slow(self, action, narration=""):
        await asyncio.sleep(0.05)
        return await real(self, action, narration)

    monkeypatch.setattr(Live, "act", slow)
    monkeypatch.setattr(server, "FINISH_WAIT_SECONDS", 0.001)

    async def scenario():
        first = await server.rehearse_beats("p1", [3])
        job_id = first.structuredContent["job_id"]
        await server.rehearsal_jobs[job_id].task
        return first, await server.rehearse_status(job_id)

    first, done = asyncio.run(scenario())
    assert first.structuredContent["status"] == "rehearsing" and "rehearse_status" in first.structuredContent["hint"]
    assert done.structuredContent["status"] == "done" and [item.type for item in done.content] == ["text", "image"]


def test_a_browser_that_will_not_start_is_reported(mods, monkeypatch):
    server, store, tmp = mods
    _plan(store)

    async def broken(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(server.BrowserSession, "create", broken)
    body = asyncio.run(server.rehearse_beats("p1", [3])).structuredContent
    assert not body["ok"] and body["error"]["type"] == "rehearsal_failed"


NAV = "Main menu Navigation Contents Search Donate " * 40


def test_the_page_is_read_around_the_words_the_beat_is_about(mods):
    server, _, _ = mods
    article = f"{NAV}The bridge reaches Peberholm, an artificial island. {'More of the article. ' * 300}"
    excerpt = server._page_excerpt(article, "peberholm,  an artificial")
    assert "The bridge reaches Peberholm" in excerpt and len(excerpt) == server.REHEARSAL_TEXT_CHARS
    assert excerpt.count("Donate") < 12


def test_without_those_words_the_main_content_is_read(mods):
    server, _, _ = mods
    assert server._page_excerpt(NAV, "not on the page", "The  article itself.") == "The article itself."
    assert server._page_excerpt("Only text.", None, "") == "Only text."

"""Storyboards: the director writes beats, AgentTake checks and shoots them.

The checks run without a browser or a network (the URL probe is a stand-in),
and the shoot runs against a stand-in session that records what it was asked
to do: the recipes are what is under test, not Chromium.
"""
from __future__ import annotations

import asyncio
import urllib.parse

import pytest

from reel_studio import storyboard as boards
from reel_studio.engine import pick_form_fields


def answers(table: dict[str, dict] | None = None):
    """A probe that answers 200 unless the table says otherwise."""
    async def probe(url: str) -> dict:
        return {"url": url, "status": 200, "content_type": "text/html", **(table or {}).get(url, {})}
    return probe


def check(beats, probe=None, secrets=(), **kwargs):
    return asyncio.run(boards.check_storyboard(beats, probe=probe or answers(), secrets=list(secrets), **kwargs))


def categories(report, severity=None):
    return {(item.get("beat"), item["category"]) for item in report["checklist"]
            if severity is None or item["severity"] == severity}


ARTICLE = "https://news.example/shadow-ai"
PRODUCT = "https://sluss.example"


def good_plan():
    return [
        {"shot": "source", "url": ARTICLE, "wait_for_text": "Shadow AI is here",
         "line": "Employees use AI tools their employer never approved."},
        {"shot": "highlight", "text": "the fastest-growing blind spot", "line": "Analysts call it a blind spot."},
        {"shot": "card", "text": "You can't block it.\nYou can govern it.", "line": "Blocking does not work."},
        {"shot": "page_tour", "url": PRODUCT, "line": "One gate between your people and every model."},
        {"shot": "cta", "url": PRODUCT, "line": "Try the demo."},
    ]


def test_a_good_plan_is_ready():
    report = check(good_plan())
    assert report["ready"] is True
    assert [item for item in report["checklist"] if item["severity"] == "fix"] == []
    # The source shot's page is its source, and the highlight on it inherits it.
    assert report["beats"][0]["source"] == ARTICLE
    assert report["beats"][1]["source"] == ARTICLE
    assert report["beats"][1]["page_url"] == ARTICLE and report["beats"][1]["inherits_url"]
    assert 5 < report["estimated_seconds"] < 30


def test_a_claim_without_a_source_must_be_fixed():
    plan = [
        {"shot": "card", "text": "73% of staff use unapproved AI", "line": "Most staff already do."},
        {"shot": "page_tour", "url": PRODUCT, "line": "Here is the product."},
        # On a product page, not an article a source beat opened: no source to inherit.
        {"shot": "highlight", "text": "every model", "line": "It covers every model."},
        {"shot": "card", "text": "There is another way", "line": "There is another way."},
    ]
    report = check(plan)
    assert report["ready"] is False
    fixes = categories(report, "fix")
    assert (1, "missing_source") in fixes
    assert (3, "missing_source") in fixes
    # A card without a number is not a claim.
    assert (4, "missing_source") not in fixes


def test_unreachable_forbidden_and_paywalled_urls_are_flagged():
    dead, locked, walled, guarded = (f"https://{name}.example/" for name in ("dead", "locked", "walled", "guarded"))
    probe = answers({
        dead: {"status": None, "error": "ConnectError: no such host"},
        locked: {"status": 403},
        walled: {"paywall": True},
        guarded: {"status": 403, "challenge": True},
        "https://app.example/inside": {"status": 401},
    })
    plan = [
        {"shot": "source", "url": dead, "wait_for_text": "Headline", "line": "One."},
        {"shot": "source", "url": locked, "wait_for_text": "Headline", "line": "Two."},
        {"shot": "source", "url": walled, "wait_for_text": "Headline", "line": "Three."},
        {"shot": "source", "url": guarded, "wait_for_text": "Headline", "line": "Four."},
        # Behind a sign-in, a 401 is expected.
        {"shot": "system", "url": "https://app.example/inside", "wait_for_text": "Policies", "line": "Five."},
    ]
    report = check(plan, probe)
    assert (1, "unreachable_url") in categories(report, "fix")
    assert (2, "forbidden_url") in categories(report, "fix")
    assert (3, "paywall") in categories(report, "warn")
    assert (4, "bot_check") in categories(report, "warn")
    assert (5, "private_url") in categories(report, "warn")
    assert not any(item.get("beat") == 5 and item["severity"] == "fix" for item in report["checklist"])
    assert report["ready"] is False


def test_an_over_long_plan_is_flagged():
    line = " ".join(["word"] * 30)  # 12 s of narration each
    plan = [{"shot": "card", "text": f"Card {chr(65 + n)}", "line": f"{line} {n}"} for n in range(7)]
    report = check(plan)
    assert report["estimated_seconds"] > 70
    assert (None, "over_length") in categories(report, "warn")
    assert report["ready"] is True  # a warning, not a refusal
    assert (None, "over_length") not in categories(check(plan[:4]))


def test_secrets_named_must_exist():
    plan = [{"shot": "login", "url": "https://app.example/login", "username": "demo",
             "password_secret": "sluss_demo", "wait_for_text": "Dashboard"}]
    assert (1, "unknown_secret") in categories(check(plan, secrets=["OTHER"]), "fix")
    assert check(plan, secrets=["SLUSS_DEMO"])["ready"] is True
    username_secret = [dict(plan[0], username="{{secret:DEMO_USER}}")]
    assert (1, "unknown_secret") in categories(check(username_secret, secrets=["SLUSS_DEMO"]), "fix")


def test_shape_problems_are_listed_together():
    plan = [
        {"shot": "system", "url": "https://app.example", "line": "No wait text."},
        {"shot": "teleport", "line": "Not a shot."},
        {"shot": "card", "text": "Hi"},
        {"shot": "live_answer", "input_text": "Is shadow AI blocked?", "wait_for_text": "shadow AI",
         "line": "Watch it answer."},
        {"shot": "card", "text": "Hi again", "line": "no wait text"},
    ]
    report = check(plan)
    fixes = categories(report, "fix")
    assert (1, "missing_field") in fixes
    assert (2, "invalid_beat") in fixes
    assert (3, "missing_line") in fixes
    assert (4, "ambiguous_wait") in fixes
    assert (5, "repeated_line") in fixes
    # Without url a live_answer stays on the page before: there must be one.
    alone = check([plan[3] | {"wait_for_text": "Blocked"}])
    assert (1, "missing_field") in categories(alone, "fix")


# ---------------------------------------------------------------------------
# Recipes


def acts(ops):
    return [(op["op"], op["action"]["type"] if op["op"] == "act" else None) for op in ops]


def test_source_recipe_opens_offscreen_and_pushes_in_while_the_line_plays():
    beat = check(good_plan())["beats"][0]
    ops = boards.recipe(beat)
    assert acts(ops) == [("act", "goto"), ("consent", None), ("act", "scroll_to_text"), ("shot", None), ("act", "wait")]
    goto, _, scroll, shot, say = ops
    assert goto["action"] == {"type": "goto", "url": ARTICLE, "offscreen": True, "wait_for_text": "Shadow AI is here",
                              "wait_timeout_ms": boards.OPEN_TIMEOUT_MS, "settle_ms": 800}
    assert scroll["action"]["offscreen"] is True
    assert shot["zoom"] > 1 and shot["focus_text"] == "Shadow AI is here" and shot["move_seconds"] >= 2.5
    assert say["narration"] == beat["line"] and say["action"]["wait_for_text"] == "Shadow AI is here"
    assert not say["action"].get("offscreen")


def test_highlight_recipe_marks_the_words_above_the_subtitles():
    beats = check(good_plan())["beats"]
    ops = boards.recipe(beats[1])
    # It stays on the article the source beat opened.
    assert acts(ops) == [("act", "scroll_to_text"), ("shot", None), ("act", "annotate")]
    assert ops[1]["lift"] is True and ops[1]["zoom"] >= 1.5
    mark = ops[2]["action"]
    assert mark["target_text"] == "the fastest-growing blind spot" and "ref" not in mark
    assert mark["style"] == "marker" and mark["text"] == "Source: news.example"
    assert ops[2]["narration"] == "Analysts call it a blind spot."
    # Retaken at the end of the session, it opens the article itself first.
    retake = boards.recipe(beats[1], retake=True)
    assert retake[0]["action"]["type"] == "goto" and retake[0]["action"]["url"] == ARTICLE


def test_page_tour_glides_to_the_footer_for_as_long_as_its_line():
    beat = check(good_plan())["beats"][3]
    ops = boards.recipe(beat)
    glide = ops[-1]
    assert glide["action"]["type"] == "scroll" and glide["action"]["dy"] == 99999
    assert boards.GLIDE_MIN_MS <= glide["action"]["ms"] <= boards.GLIDE_MAX_MS
    assert glide["narration"] == beat["line"]


def test_login_recipe_signs_in_offscreen_with_the_secret():
    beat = check([{"shot": "login", "url": "https://app.example/login", "username": "demo",
                   "password_secret": "DEMO", "wait_for_text": "Dashboard"}], secrets=["DEMO"])["beats"][0]
    ops = boards.recipe(beat)
    assert acts(ops) == [("act", "goto"), ("consent", None), ("observe", None),
                         ("act", "type"), ("act", "type"), ("act", "press_key")]
    assert all(op["action"].get("offscreen") for op in ops if op["op"] == "act")
    assert ops[3]["action"]["ref"] == "@username" and ops[3]["action"]["text"] == "demo"
    assert ops[4]["action"] == {"type": "type", "ref": "@password", "text": "{{secret:DEMO}}", "offscreen": True}
    assert ops[5]["action"]["wait_for_text"] == "Dashboard"
    assert not any(op.get("narration") for op in ops)


def test_live_answer_waits_offscreen_then_shows_the_answer():
    plan = [{"shot": "system", "url": "https://app.example/chat", "wait_for_text": "Ask", "line": "The chat."},
            {"shot": "live_answer", "input_text": "Summarise the customer list", "wait_for_text": "blocked by policy",
             "timeout": 300, "line": "The gate holds."}]
    beat = check(plan)["beats"][1]
    ops = boards.recipe(beat)
    assert acts(ops) == [("observe", None), ("act", "type"), ("act", "press_key"), ("act", "scroll_to_text"),
                         ("shot", None), ("act", "wait")]
    send = ops[2]["action"]
    assert send["offscreen"] and send["wait_timeout_ms"] == 300000 and send["wait_for_text"] == "blocked by policy"
    assert ops[4]["lift"] is True
    assert ops[5]["narration"] == "The gate holds." and not ops[5]["action"].get("offscreen")


def test_card_and_image_are_pages_of_their_own():
    card = boards.card_page("You can't <block> it.\nYou can govern it.", "#0f1420", "https://www.gartner.example/x")
    assert card.startswith("data:text/html")
    page = urllib.parse.unquote(card)
    assert "&lt;block&gt;" in page and "<block>" not in page
    assert "Source: gartner.example" in page and "background:#0f1420" in page
    image = urllib.parse.unquote(boards.image_page("https://img.example/a.jpg", "Stockholm, 2026"))
    assert boards.IMAGE_READY in image and "class=\"lower\"" in image and "@keyframes kb" in image
    # An image behind a card's text is always drawn as a page.
    behind = check([{"shot": "card", "text": "Shadow AI", "background": "https://img.example/b.jpg",
                     "line": "Shadow AI."}])["beats"][0]
    assert boards.recipe(behind)[0]["action"]["url"].startswith("data:text/html")
    beat = check([{"shot": "image", "url": "https://img.example/a.jpg", "line": "The city."}])["beats"][0]
    ops = boards.recipe(beat)
    assert ops[0]["action"]["wait_for_text"] == boards.IMAGE_READY
    assert ops[-1]["narration"] == "The city."


def test_a_card_uses_the_render_card_where_agenttake_has_one(monkeypatch):
    from reel_studio import schema

    beat = check([{"shot": "card", "text": "You can't block it.", "line": "Blocking does not work."}])["beats"][0]
    monkeypatch.setattr(schema, "ACTION_TYPES", (*schema.ACTION_TYPES, "card"))
    assert boards.recipe(beat) == [{"op": "act", "narration": "Blocking does not work.",
                                    "action": {"type": "card", "text": "You can't block it.", "background": "brand"}}]
    monkeypatch.setattr(schema, "ACTION_TYPES", tuple(t for t in schema.ACTION_TYPES if t != "card"))
    ops = boards.recipe(beat)
    assert ops[0]["action"]["type"] == "goto" and ops[0]["action"]["url"].startswith("data:text/html")
    assert ops[-1]["narration"] == "Blocking does not work."


def test_form_fields_are_told_apart():
    login = [
        {"ref": "input:search", "tag": "input", "type": "search", "hints": "search"},
        {"ref": "input:email", "tag": "input", "type": "email", "hints": "email"},
        {"ref": "input:password", "tag": "input", "type": "password", "hints": "password"},
    ]
    assert pick_form_fields(login) == {"password": "input:password", "username": "input:email"}
    chat = [
        {"ref": "input:search", "tag": "input", "type": "text", "hints": "search docs"},
        {"ref": "textarea:ask", "tag": "textarea", "type": "", "hints": "ask anything"},
    ]
    assert pick_form_fields(chat) == {"message": "textarea:ask"}


# ---------------------------------------------------------------------------
# Shooting, against a stand-in session


class StandInSession:
    """Records what the recipes ask of a browser session."""

    def __init__(self, missing=()):
        self.voice = "en-US-JennyNeural"
        self.refs: dict[str, str] = {}
        self.refs_stale = False
        self.missing = set(missing)
        self.calls: list[tuple] = []

    def touch(self):
        pass

    async def error_result(self, error_type, message):
        return {"ok": False, "error": {"type": error_type, "message": message}}, None

    async def act(self, action, narration=""):
        self.calls.append(("act", action.type, action.offscreen, narration))
        for text in (action.wait_for_text, action.text if action.type == "scroll_to_text" else None):
            if text and text in self.missing:
                return {"ok": False, "error": {"type": "text_not_found", "message": f"Visible text not found: {text}"}}, None
        return {"ok": True, "url": action.url or "https://page.example", "narration_duration": 1.0 if narration else 0}, None

    async def set_shot(self, framing, zoom=None, focus_ref=None, focus_text=None, move_seconds=None, lift=False):
        self.calls.append(("shot", zoom, focus_text, lift))
        return {"camera_zoom": zoom}

    async def observe(self, detail="full"):
        self.calls.append(("observe",))
        self.refs = {"input:email": "#email", "input:password": "#password"}
        return {}, None

    async def form_fields(self):
        return {"username": "input:email", "password": "input:password"}

    async def dismiss_consent(self, labels):
        return None


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "storyboard.db"))
    monkeypatch.setenv("REEL_SECRET_DEMO", "hunter2")
    monkeypatch.setattr(boards, "probe_url", answers())
    from reel_studio import server as module, store

    store.init_schema()
    yield module
    module.sessions.clear()


def start(server, session_id, session):
    from reel_studio import store

    store.create_session(session_id, ARTICLE, "en-US-JennyNeural", 1920, 1080, "/tmp", "edge")
    server.sessions[session_id] = session


def test_a_plan_with_fixes_is_not_shot(server):
    start(server, "s-unready", StandInSession())
    plan = asyncio.run(server.plan_storyboard(
        [{"shot": "card", "text": "9 in 10 firms", "line": "Nine in ten."}], session_id="s-unready"))
    assert plan["ok"] and plan["ready"] is False and "plan_storyboard" in plan["next"]
    shot = asyncio.run(server.shoot_storyboard("s-unready"))
    assert shot["ok"] is False and shot["error"]["type"] == "storyboard_not_ready"
    assert shot["checklist"][0]["category"] == "missing_source"
    assert server.sessions["s-unready"].calls == []


def test_shoot_reports_each_beat_and_a_retake_goes_where_it_belongs(server, tmp_path):
    from reel_studio import review, store

    session = StandInSession(missing={"the fastest-growing blind spot"})
    start(server, "s-film", session)
    plan = asyncio.run(server.plan_storyboard(good_plan()))
    assert plan["ready"] and plan["beats"] == 5

    shot = asyncio.run(server.shoot_storyboard("s-film", plan_id=plan["plan_id"]))
    assert [(beat["beat"], beat["ok"]) for beat in shot["beats"]] == [(1, True), (2, False), (3, True), (4, True), (5, True)]
    assert "text_not_found" in shot["beats"][1]["reason"]
    assert shot["failed"] == [2] and shot["to_come"] == []
    assert "shoot_beat" in shot["next"]
    # The plan's lines are what the review holds the take against.
    assert [beat["line"] for beat in store.get_session("s-film")["beats"]] == [b["line"] for b in good_plan()]

    # Every narrated step is the beat's own line, visible; everything else offscreen.
    narrated = [call for call in session.calls if call[0] == "act" and call[3]]
    assert [call[3] for call in narrated] == [good_plan()[i]["line"] for i in (0, 2, 3, 4)]
    assert not any(call[2] for call in narrated)
    assert session.calls[:4] == [
        ("act", "goto", True, ""), ("act", "scroll_to_text", True, ""),
        ("shot", 1.25, "Shadow AI is here", False), ("act", "wait", False, good_plan()[0]["line"]),
    ]

    # A second call has nothing left to shoot.
    again = asyncio.run(server.shoot_storyboard("s-film"))
    assert again["beats"] == [] and again["failed"] == [2]

    # The quote is on the page now: retake beat 2. It reopens the article.
    session.missing.clear()
    session.calls.clear()
    retake = asyncio.run(server.shoot_beat("s-film", beat=2))
    assert retake["ok"] and retake["retake"] is True
    assert session.calls[0] == ("act", "goto", True, "")
    assert session.calls[-1] == ("act", "annotate", False, good_plan()[1]["line"])

    video = tmp_path / "video.mp4"
    video.write_bytes(b"\0")
    store.finish_session("s-film", str(video), None, 30.0)
    placed = asyncio.run(server.apply_retakes("s-film"))
    assert placed["ok"] and [p["beat"] for p in placed["placed"]] == [2]
    assert "rerender" in placed["hint"]
    stored = store.get_session("s-film")
    comparison = review.compare_beats(stored["beats"], stored["steps"])
    assert comparison["missing"] == [] and comparison["out_of_order"] == []
    assert comparison["in_order"] == 5
    # Applied once: a second call places nothing.
    assert asyncio.run(server.apply_retakes("s-film"))["placed"] == []


def test_a_failed_sign_in_stops_the_shoot(server):
    session = StandInSession(missing={"Dashboard"})
    start(server, "s-login", session)
    beats = [
        {"shot": "login", "url": "https://app.example/login", "username": "demo",
         "password_secret": "DEMO", "wait_for_text": "Dashboard"},
        {"shot": "system", "url": "https://app.example/policies", "wait_for_text": "Policies", "line": "The policies."},
    ]
    plan = asyncio.run(server.plan_storyboard(beats, session_id="s-login"))
    assert plan["ready"], plan["checklist"]
    shot = asyncio.run(server.shoot_storyboard("s-login"))
    assert shot["ok"] is False
    assert [beat["beat"] for beat in shot["beats"]] == [1]
    assert shot["to_come"] == [2] and "sign-in failed" in shot["next"]
    # The password went in as the placeholder AgentTake resolves itself.
    assert ("act", "type", True, "") in session.calls


def test_shoot_beat_keeps_the_order(server):
    start(server, "s-order", StandInSession())
    asyncio.run(server.plan_storyboard(good_plan(), session_id="s-order"))
    early = asyncio.run(server.shoot_beat("s-order", beat=3))
    assert early["ok"] is False and early["error"]["type"] == "beat_not_reached"
    first = asyncio.run(server.shoot_beat("s-order", beat=1))
    assert first["ok"] and "retake" not in first


def test_a_shoot_stops_when_its_time_is_up(server, monkeypatch):
    start(server, "s-budget", StandInSession())
    asyncio.run(server.plan_storyboard(good_plan(), session_id="s-budget"))
    monkeypatch.setattr(server, "shoot_budget_seconds", lambda: -1.0)
    shot = asyncio.run(server.shoot_storyboard("s-budget"))
    assert [beat["beat"] for beat in shot["beats"]] == [1]
    assert shot["to_come"] == [2, 3, 4, 5] and "again" in shot["next"]


def test_director_guide_is_the_handbook(server):
    guide = asyncio.run(server.director_guide())
    assert "## The shot menu" in guide and "## A 60-second story" in guide and "## The rules" in guide
    for shot in boards.SHOT_TYPES:
        assert f"`{shot}`" in guide
    assert server.storyboard_prompt() == guide

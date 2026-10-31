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


def test_a_hidden_content_gate_is_not_a_paywall():
    # TechTarget ships its gate's form on every article, hidden and off; the
    # article reads in full (2026-10-28).
    gate = """<article><p>The whole story.</p></article>
    <form id="content_gate_signup" class="js-form-email-validate" name="signup" style="display: none">
      <h3>Continue Reading</h3><p class="email-check">Already a subscriber? Sign in with your email above.</p>
    </form><script>window.gateEnabled = 'False';</script>"""
    assert boards.looks_paywalled(gate) is False
    # Shown, the same prompt still is one.
    assert boards.looks_paywalled(gate.replace(' style="display: none"', "")) is True
    assert boards.looks_paywalled('<script type="application/ld+json">{"isAccessibleForFree": false}</script>')
    assert boards.looks_paywalled("<div style='display:none'>x</div><p>Subscribe to continue reading</p>")


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


def test_a_page_toured_twice_must_be_fixed():
    # The first storyboarded film toured one site from four anchors; each tour
    # glided to the footer, and the footer played under four lines.
    beats = good_plan()[:4] + [
        {"shot": "page_tour", "url": "https://www.sluss.example/#features", "line": "Every policy in one place."},
        {"shot": "page_tour", "url": PRODUCT + "/pricing", "line": "Priced per seat."},
        {"shot": "system", "url": PRODUCT + "/#features", "wait_for_text": "Policies",
         "line": "Each policy names who may use which model."},
    ]
    report = check(beats)
    assert categories(report, "fix") == {(5, "repeated_tour")}
    assert "system shot" in next(item["message"] for item in report["checklist"]
                                 if item["category"] == "repeated_tour")
    # Another page of the site, and a section held with a system shot, are fine.
    assert not {6, 7} & {beat for beat, _ in categories(report)}


def test_an_escaped_newline_on_a_card_is_a_line_break():
    # Hermes sent "server—\\nand" over MCP while its own file had a newline,
    # and the card showed the backslash and the n (2026-10-28).
    from reel_studio.render import card_text_lines

    beats = good_plan()
    beats[2]["text"] = "What if your agent could live on your server—\\nand leave with its memory?"
    report = check(beats)
    assert report["beats"][2]["text"] == "What if your agent could live on your server—\nand leave with its memory?"
    assert card_text_lines("one\\ntwo\nthree") == ["one", "two", "three"]


def test_a_film_that_opens_on_a_card_has_no_lead_in():
    # Deepseek's take opened on a card, and the start page played for a second
    # between the title and it (2026-10-28).
    from reel_studio.render import wants_lead_in

    assert wants_lead_in([{"action_type": "goto"}])
    assert not wants_lead_in([{"action_type": "goto", "offscreen": True}])
    assert not wants_lead_in([{"action_type": "card"}, {"action_type": "goto"}])
    assert wants_lead_in([])


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
    # Never closer than the whole quote (TechTarget's standfirst, 2026-10-28).
    assert ops[1]["fit"] is True
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
    assert acts(ops) == [("observe", None), ("act", "type"), ("answer_start", None), ("act", "press_key"),
                         ("answer", None), ("act", "scroll_to_text"), ("shot", None), ("act", "wait")]
    send = ops[3]["action"]
    # Sent offscreen, and the answer op does the waiting, not the key press.
    assert send["offscreen"] and "wait_for_text" not in send
    assert ops[4] == {"op": "answer", "timeout": 300.0, "expect": "blocked by policy",
                      "question": "Summarise the customer list"}
    assert ops[6]["lift"] is True and ops[6]["focus_text"] == "@answer"
    assert ops[7]["narration"] == "The gate holds." and not ops[7]["action"].get("offscreen")


def test_a_live_answer_needs_no_predicted_words():
    plan = [{"shot": "system", "url": "https://app.example/chat", "wait_for_text": "Ask", "line": "The chat."},
            {"shot": "live_answer", "input_text": "Summarise the customer list", "line": "The gate holds."}]
    report = check(plan)
    assert report["ready"], report["checklist"]
    assert boards.recipe(report["beats"][1])[4]["expect"] is None


# A live answer: the page's new text, read until it stops growing ------------

def test_new_text_is_what_the_page_added_without_the_question():
    before = "Sluss demo\nAsk the gateway\nSend\nFooter"
    now = ("Sluss demo\nAsk the gateway\nYou: Summarise customer 81121\n"
           "Blocked: the prompt holds personal data (PII), so it stays local.\nSend\nFooter")
    assert boards.new_text(before, now, "Summarise customer 81121") == (
        "You:\nBlocked: the prompt holds personal data (PII), so it stays local.")
    assert boards.new_text(before, before, "x") == ""
    assert boards.answer_focus("You:\nBlocked: the prompt holds personal data (PII), so it stays local.") == (
        "Blocked: the prompt holds personal data (PII), so")


def watch(pages, timeout=60.0, quiet=3.0):
    """watch_answer over a scripted page, one text per poll, on a fake clock."""
    clock = {"now": 0.0}
    texts = iter(pages)
    last = {"text": ""}

    async def read():
        last["text"] = next(texts, last["text"])
        return last["text"]

    async def sleep(seconds):
        clock["now"] += seconds

    return asyncio.run(boards.watch_answer(read, "Chat\nSend", "Hi?", timeout, quiet=quiet, poll=1.0,
                                           clock=lambda: clock["now"], sleep=sleep))


def test_an_answer_has_landed_when_it_stops_growing():
    thinking = "Chat\nHi?\nThinking\nSend"
    pages = [thinking] * 5 + [
        "Chat\nHi?\nThe gateway keeps\nSend",
        "Chat\nHi?\nThe gateway keeps personal data local.\nSend",
    ]
    watched = watch(pages)
    # "Thinking" stays put for five seconds and is not taken for the answer.
    assert watched["settled"] and watched["text"] == "The gateway keeps personal data local."
    assert watched["seconds"] == 9.0


def test_an_answer_still_growing_at_the_timeout_is_reported_as_such():
    pages = [f"Chat\nHi?\n{'word ' * n}\nSend" for n in range(1, 40)]
    watched = watch(pages, timeout=10.0)
    assert not watched["settled"] and watched["seconds"] == 10.0 and watched["text"].startswith("word word")


def test_an_answer_is_judged_ok_or_failed_with_what_it_said():
    said = {"text": "The prompt was routed to the local model.", "settled": True, "seconds": 7.0}
    assert boards.answer_verdict(said, None, 120) == ("The prompt was routed to the local model.", None, None)
    assert boards.answer_verdict(said, "local model", 120) == ("local model", None, None)
    focus, reason, _ = boards.answer_verdict(said, "blocked", 120)
    assert focus is None and reason.startswith("answer_without_text: the answer does not contain 'blocked'")
    assert "it said: 'The prompt was routed to the local model.'" in reason
    focus, reason, _ = boards.answer_verdict({"text": "", "settled": False, "seconds": 120.0}, None, 120)
    assert reason == "no_answer: nothing new appeared on the page in 120 s"
    focus, reason, note = boards.answer_verdict({**said, "settled": False, "seconds": 120.0}, None, 120)
    assert reason is None and note.startswith("the answer was still growing after 120 s")


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


def test_a_source_card_quotes_its_source_with_the_publication_date_and_address():
    beat = check([{"shot": "source_card", "source": "https://www.nytimes.example/2026/10/shadow-ai.html?smid=x",
                   "text": "Nearly 40% of uploaded files contain <PII>", "date": "28 October 2026",
                   "line": "Two in five uploads carry personal data."}])["beats"][0]
    ops = boards.recipe(beat)
    goto = ops[0]["action"]
    assert goto["type"] == "goto" and goto["offscreen"] and goto["wait_for_text"] == "Nearly 40% of uploaded files contain"
    assert ops[-1]["narration"] == "Two in five uploads carry personal data."
    page = urllib.parse.unquote(goto["url"])
    assert "nytimes.example" in page and "28 October 2026" in page
    assert "nytimes.example/2026/10/shadow-ai.html" in page and "smid" not in page
    assert "&lt;PII&gt;" in page and "<PII>" not in page
    # The quote marks are CSS, so the quote's words are what the step waits for.
    assert '<p class="quote">Nearly 40%' in page
    named = boards.source_card_page("Quoted.", "https://x.example/a", publication="The Register")
    assert "The Register" in urllib.parse.unquote(named)


def test_a_source_card_needs_its_source_and_a_quote_that_is_on_the_page():
    article = "https://news.example/a"
    page = boards.loose_text(boards.html_text(
        "<html><script>var x='Nearly 40%'</script><p>Nearly&nbsp;40% of uploaded files contain "
        "<em>personally</em> identifiable information, the report says.</p></html>"))
    probe = answers({
        article: {"text": page},
        "https://paywalled.example/a": {"status": 403},
        "https://gone.example/a": {"status": 404},
    })
    plan = [
        {"shot": "source_card", "text": "Nearly 40% of uploaded files contain personally identifiable information",
         "source": article, "line": "One."},
        {"shot": "source_card", "text": "Nearly 40% \u2026 identifiable information", "source": article,
         "line": "Two."},
        {"shot": "source_card", "text": "Nearly half of all files", "source": article, "line": "Three."},
        {"shot": "source_card", "text": "Behind the wall", "source": "https://paywalled.example/a", "line": "Four."},
        {"shot": "source_card", "text": "Gone", "source": "https://gone.example/a", "line": "Five."},
        {"shot": "source_card", "text": "No source", "line": "Six."},
    ]
    report = check(plan, probe)
    flagged = categories(report)
    assert not any(beat in (1, 2) for beat, _ in flagged)
    assert (3, "quote_not_on_page") in categories(report, "warn")
    # A 403 is why a card is shown: AgentTake says it could not check the quote.
    assert (4, "unverified_quote") in categories(report, "warn")
    assert (4, "forbidden_url") not in flagged
    assert (5, "broken_url") in categories(report, "fix")
    assert (6, "missing_source") in categories(report, "fix")


def test_a_forbidden_source_suggests_a_source_card():
    locked = "https://locked.example/a"
    report = check([{"shot": "source", "url": locked, "wait_for_text": "Headline", "line": "One."}],
                   answers({locked: {"status": 403}}))
    assert any("source_card" in item["message"] for item in report["checklist"]
               if item["category"] == "forbidden_url")


def test_a_beat_after_a_source_card_names_its_page():
    report = check([
        {"shot": "source_card", "text": "A quote", "source": "https://news.example/a", "line": "One."},
        {"shot": "highlight", "text": "a quote", "source": "https://news.example/a", "line": "Two."},
    ])
    assert (2, "missing_field") in categories(report, "fix")


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


def test_a_consent_banner_is_never_accepted():
    """A banner is refused, hidden or acknowledged, never agreed to."""
    agreeing = ("accept", "agree", "allow", "godkänn", "acceptera", "akzeptieren", "consent")
    for label in boards.CONSENT_REFUSE + boards.CONSENT_NOTICE:
        lowered = label.lower()
        if any(word in lowered for word in ("not ", "without", "sans ")):
            continue
        assert not any(word in lowered for word in agreeing), label
    assert ".fc-consent-root" in boards.CONSENT_MANAGERS


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

    def __init__(self, missing=(), covered=(), pages=(), slow=0.0):
        self.voice = "en-US-JennyNeural"
        self.refs: dict[str, str] = {}
        self.refs_stale = False
        self.missing = set(missing)
        self.covered = set(covered)
        self.pages = list(pages)
        self.page = ""
        self.url = ""
        self.gotos: list[str] = []
        self.slow = slow
        self.calls: list[tuple] = []
        self.fits: list[bool] = []
        self.left_out: list[float] = []

    def touch(self):
        pass

    async def error_result(self, error_type, message):
        return {"ok": False, "error": {"type": error_type, "message": message}}, None

    async def page_text(self):
        if self.pages:
            self.page = self.pages.pop(0)
        return self.page

    async def act(self, action, narration=""):
        self.calls.append(("act", action.type, action.offscreen, narration))
        if action.type == "goto":
            self.url = action.url
            self.gotos.append(action.url)
        if self.slow and narration:
            await asyncio.sleep(self.slow)
        for text in (action.wait_for_text, action.text if action.type == "scroll_to_text" else None):
            if text and text in self.missing:
                return {"ok": False, "error": {"type": "text_not_found", "message": f"Visible text not found: {text}"}}, None
        result = {"ok": True, "url": action.url or "https://page.example", "narration_duration": 1.0 if narration else 0,
                  "offset_seconds": float(len(self.calls))}
        # Only a site covers its text: AgentTake's own cards have no dialogs.
        if narration and action.wait_for_text in self.covered and not self.url.startswith("data:"):
            result["focus_cover"] = {"points": 15, "covered": 12, "text": action.wait_for_text,
                                     "by": {"element": "div.fc-consent-root", "position": "fixed",
                                            "text": "The Register asks for your consent"}}
        return result, None

    async def set_shot(self, framing, zoom=None, focus_ref=None, focus_text=None, move_seconds=None, lift=False,
                       fit=False):
        self.calls.append(("shot", zoom, focus_text, lift))
        self.fits.append(fit)
        return {"camera_zoom": zoom}

    async def observe(self, detail="full"):
        self.calls.append(("observe",))
        self.refs = {"input:email": "#email", "input:password": "#password"}
        return {}, None

    async def form_fields(self):
        return {"username": "input:email", "password": "input:password", "message": "textarea:ask"}

    def leave_out(self, offsets):
        self.left_out.extend(offsets)

    async def dismiss_consent(self, refuse, managers=(), notice=()):
        self.consent_asked = (refuse, managers, notice)
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


def test_a_shoot_the_voice_quota_cannot_finish_is_refused(server, monkeypatch):
    """A free ElevenLabs account ran dry two beats into the Agentanbud film:
    seven beats unspoken, the take lost (2026-10-30)."""
    from reel_studio import store

    store.create_session("s-quota", ARTICLE, "voice-x", 1920, 1080, "/tmp", "elevenlabs")
    server.sessions["s-quota"] = StandInSession()
    plan = asyncio.run(server.plan_storyboard(good_plan(), session_id="s-quota"))
    assert plan["ready"]

    async def left_ten():
        return 10

    monkeypatch.setattr(server, "elevenlabs_characters_left", left_ten)
    shot = asyncio.run(server.shoot_storyboard("s-quota"))
    assert shot["ok"] is False and shot["error"]["type"] == "voice_quota"
    assert "10 characters left" in shot["error"]["message"] and server.sessions["s-quota"].calls == []

    async def plenty():
        return 1_000_000

    monkeypatch.setattr(server, "elevenlabs_characters_left", plenty)
    assert asyncio.run(server._voice_quota_shortfall("s-quota", store.get_storyboard(plan["plan_id"]))) is None


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
    # A consent banner is refused, hidden or acknowledged: never accepted.
    assert session.consent_asked == (boards.CONSENT_REFUSE, boards.CONSENT_MANAGERS, boards.CONSENT_NOTICE)
    # The plan's lines are what the review holds the take against.
    assert [beat["line"] for beat in store.get_session("s-film")["beats"]] == [b["line"] for b in good_plan()]

    # Every narrated step is the beat's own line, visible; everything else offscreen.
    narrated = [call for call in session.calls if call[0] == "act" and call[3]]
    assert [call[3] for call in narrated] == [good_plan()[i]["line"] for i in (0, 2, 3, 4)]
    assert not any(call[2] for call in narrated)
    assert session.calls[:4] == [
        ("act", "goto", True, ""), ("act", "scroll_to_text", True, ""),
        ("shot", 1.12, "Shadow AI is here", False), ("act", "wait", False, good_plan()[0]["line"]),
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
    # The highlight's close shot reaches the camera with fit.
    assert session.fits[-1] is True

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


def test_a_beat_whose_page_is_covered_is_shown_as_a_source_card(server):
    """The Sluss film's source beats played under Google's consent dialog for
    22 s, and every beat was reported ok (2026-10-28). Now the covered take is
    left out and the beat quotes its source on a card."""
    from reel_studio import store

    session = StandInSession(covered={"Shadow AI is here"})
    start(server, "s-covered", session)
    plan = asyncio.run(server.plan_storyboard(good_plan()))
    shot = asyncio.run(server.shoot_storyboard("s-covered", plan_id=plan["plan_id"]))
    first = shot["beats"][0]
    assert first["ok"] and first["fallback"] == "source_card" and 1 not in shot["failed"]
    assert "covered" in first["notes"][0] and "div.fc-consent-root (fixed)" in first["notes"][0]
    line = good_plan()[0]["line"]
    spoken = [step for step in store.get_session("s-covered")["steps"] if step["narration_text"] == line]
    # Spoken twice, played once: over the card, the covered take left out.
    assert [step["offscreen"] for step in spoken] == [1, 0]
    assert first["steps"] == [spoken[1]["idx"]]
    # finish renders from the live timeline: the covered take leaves it too
    # (Agentanbud take 4 still played it before the card, 2026-10-30).
    assert session.left_out == [spoken[0]["offset_seconds"]]
    cards = [url for url in session.gotos if url.startswith("data:")]
    assert "news.example/shadow-ai" in urllib.parse.unquote(cards[0])
    assert [beat["ok"] for beat in shot["beats"][1:]] == [True, True, True, True]


def test_a_covered_beat_without_a_card_to_fall_back_on_fails(server):
    """A system shot is the product, not a source: covered, it fails."""
    session = StandInSession(covered={"Dashboard"})
    start(server, "s-covered-system", session)
    plan = asyncio.run(server.plan_storyboard(
        [{"shot": "system", "url": PRODUCT, "wait_for_text": "Dashboard", "line": "The dashboard."}]))
    shot = asyncio.run(server.shoot_storyboard("s-covered-system", plan_id=plan["plan_id"]))
    first = shot["beats"][0]
    assert first["ok"] is False and "fallback" not in first
    assert first["reason"].startswith("focus_covered: ") and "12 of 15" in first["reason"]


CHAT_PLAN = [
    {"shot": "system", "url": "https://demo.example/chat", "wait_for_text": "Ask", "line": "The demo's chat."},
    {"shot": "live_answer", "input_text": "Summarise customer 81121", "line": "Personal data stays in the house."},
]


def test_a_live_answer_is_framed_by_its_first_words(server, monkeypatch):
    monkeypatch.setattr(boards, "ANSWER_QUIET_SECONDS", 0.0)
    monkeypatch.setattr(boards, "ANSWER_POLL_SECONDS", 0.0)
    answered = "Chat\nYou: Summarise customer 81121\nRouted to the local model: it holds personal data.\nSend"
    session = StandInSession(pages=["Chat\nSend", answered])
    start(server, "s-answer", session)
    plan = asyncio.run(server.plan_storyboard(CHAT_PLAN))
    shot = asyncio.run(server.shoot_storyboard("s-answer", plan_id=plan["plan_id"]))
    assert shot["status"] == "done" and [beat["ok"] for beat in shot["beats"]] == [True, True]
    focus = "Routed to the local model: it holds personal"
    assert ("shot", 1.5, focus, True) in session.calls
    assert session.calls[-1] == ("act", "wait", False, "Personal data stays in the house.")


def test_a_live_answer_that_never_comes_fails_with_a_verdict(server, monkeypatch):
    monkeypatch.setattr(boards, "ANSWER_POLL_SECONDS", 0.0)
    plan_beats = [CHAT_PLAN[0], {**CHAT_PLAN[1], "timeout": 1}]
    start(server, "s-silent", StandInSession(pages=["Chat\nSend"]))
    plan = asyncio.run(server.plan_storyboard(plan_beats))
    shot = asyncio.run(server.shoot_storyboard("s-silent", plan_id=plan["plan_id"]))
    assert shot["beats"][1]["ok"] is False
    assert shot["beats"][1]["reason"] == "no_answer: nothing new appeared on the page in 1 s"


def test_a_slow_shoot_answers_in_time_and_shoot_status_follows_it(server, monkeypatch):
    """The Sluss film's live beat answered the director with a bare client
    timeout twice: no verdict, so it was cut (2026-10-28)."""
    monkeypatch.setenv("REEL_SHOOT_WAIT_SECONDS", "1")
    session = StandInSession(slow=0.8)
    start(server, "s-slow", session)
    plan = asyncio.run(server.plan_storyboard(good_plan()))

    async def scenario():
        first = await server.shoot_storyboard("s-slow", plan_id=plan["plan_id"])
        # Nothing else drives the browser meanwhile.
        refused = await server.finish("s-slow")
        joined = await server.shoot_beat("s-slow", beat=1)
        done = first
        while done.get("status") == "shooting":
            done = await server.shoot_status("s-slow")
        return first, refused, joined, done

    first, refused, joined, done = asyncio.run(scenario())
    assert first["status"] == "shooting" and first["shooting"] == "storyboard"
    assert first["beat"]["beat"] == 2 and [beat["beat"] for beat in first["beats"]] == [1]
    assert "shoot_status(session_id)" in first["hint"]
    assert refused["error"]["type"] == "session_shooting"
    assert joined["status"] == "shooting" and joined["shooting"] == "storyboard"
    assert done["status"] == "done" and [beat["ok"] for beat in done["beats"]] == [True] * 5


def test_shoot_status_without_a_shoot_says_how_to_start_one(server):
    assert asyncio.run(server.shoot_status("s-none"))["error"]["type"] == "no_shoot"


STREAMING_CHAT = """<body style="font:16px sans-serif">
  <div id="log"><p>Ask the gateway anything.</p></div>
  <textarea id="ask"></textarea>
  <script>
    document.getElementById('ask').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const log = document.getElementById('log');
      log.insertAdjacentHTML('beforeend', '<p>' + e.target.value + '</p><p id="a">Thinking…</p>');
      e.target.value = '';
      const words = 'Blocked: the prompt holds personal data, so it stays on the local model.'.split(' ');
      setTimeout(() => {
        const answer = document.getElementById('a');
        answer.textContent = '';
        let n = 0;
        const timer = setInterval(() => {
          answer.textContent += (n ? ' ' : '') + words[n++];
          if (n === words.length) clearInterval(timer);
        }, 150);
      }, 1500);
    });
  </script>
</body>"""


def test_a_streamed_answer_is_read_once_it_has_landed():
    """In a real browser: a 'Thinking…' line, then the answer word by word."""
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:  # no browser in this environment
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page()
            await page.set_content(STREAMING_CHAT)

            async def read():
                return await page.evaluate("() => document.body.innerText")

            await page.fill("#ask", "Summarise customer 81121")
            before = await read()
            await page.press("#ask", "Enter")
            watched = await boards.watch_answer(read, before, "Summarise customer 81121", 20, quiet=1.0, poll=0.25)
            await browser.close()
            return watched

    watched = asyncio.run(run())
    assert watched["settled"]
    assert watched["text"] == "Blocked: the prompt holds personal data, so it stays on the local model."
    assert boards.answer_verdict(watched, "personal data", 20)[0] == "personal data"


# Signing in before the pages behind it ---------------------------------------

CONSOLE = "https://demo.example/router/dashboard"
SIGN_IN = "https://demo.example/router/login"


def console_plan(login_first):
    login = {"shot": "login", "url": SIGN_IN, "username": "hermes@example.com", "password_secret": "DEMO",
             "wait_for_text": "Dashboard"}
    console = {"shot": "system", "url": CONSOLE, "wait_for_text": "Active policy", "line": "Policy decides."}
    hook = {"shot": "card", "text": "Where did the data go?", "line": "Where did the data go?"}
    return [hook, login, console] if login_first else [hook, console, login]


def test_a_page_behind_the_sign_in_must_come_after_the_login():
    """The Sluss film's console beat came before its login beat and failed on
    the sign-in page (2026-10-28)."""
    gated = answers({CONSOLE: {"final_url": SIGN_IN + "?next=/router/dashboard"}})
    report = check(console_plan(login_first=False), probe=gated, secrets=["DEMO"])
    assert (2, "before_sign_in") in categories(report, "fix")
    item = next(item for item in report["checklist"] if item["category"] == "before_sign_in")
    assert "login beat 3 comes after this one" in item["message"]
    assert check(console_plan(login_first=True), probe=gated, secrets=["DEMO"])["ready"]
    # No login at all: say so.
    alone = check([console_plan(True)[0], console_plan(True)[2]], probe=gated, secrets=["DEMO"])
    assert (2, "behind_sign_in") in categories(alone, "warn") and alone["ready"]
    # A public page is not taken for a sign-in.
    assert check(console_plan(login_first=False), secrets=["DEMO"])["ready"]


# A fresh take, from AgentTake's own copy of the storyboard -------------------

def test_a_fresh_take_starts_like_the_first_and_shoots_the_same_plan(server, monkeypatch):
    from reel_studio import store

    created = []

    class Recording(StandInSession):
        def __init__(self, number):
            super().__init__()
            self.session_id = f"s-take-{number}"
            self.directory = "/tmp"

    async def create(*args, **kwargs):
        created.append((args, kwargs))
        return Recording(len(created))

    async def speak(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "synthesize", speak)
    monkeypatch.setattr(server.BrowserSession, "create", create)
    first = asyncio.run(server.start_session(ARTICLE, width=1280, height=720, title="Shadow AI",
                                             subtitles=True, mask=["[data-secret]"], color_scheme="dark"))
    assert first["session_id"] == "s-take-1"
    plan = asyncio.run(server.plan_storyboard(good_plan(), session_id="s-take-1"))
    shot = asyncio.run(server.shoot_storyboard("s-take-1"))
    assert shot["status"] == "done"

    again = asyncio.run(server.fresh_take("s-take-1"))
    assert again["ok"] and again["session_id"] == "s-take-2" and again["from_session"] == "s-take-1"
    assert again["plan_id"] != plan["plan_id"]
    # Started exactly as the first was.
    assert created[1] == created[0]
    stored = store.get_session("s-take-2")
    assert stored["subtitles"] and stored["title"] == "Shadow AI"
    assert stored["start_args"]["color_scheme"] == "dark" and stored["start_args"]["mask"] == ["[data-secret]"]
    # The beats come back as the director wrote them, ready to edit and plan again.
    assert [beat["line"] for beat in again["beats"]] == [beat["line"] for beat in good_plan()]
    assert "state" not in again["beats"][0] and again["beats"][0]["wait_for_text"] == "Shadow AI is here"
    retaken = asyncio.run(server.shoot_storyboard("s-take-2"))
    assert retaken["status"] == "done" and [beat["ok"] for beat in retaken["beats"]] == [True] * 5
    # The first session's plan is untouched.
    assert all(beat["state"]["status"] == "ok" for beat in store.get_storyboard(plan["plan_id"])["beats"])
    for session_id in ("s-take-1", "s-take-2"):
        server.sessions.pop(session_id, None)


def test_a_fresh_take_needs_a_storyboard(server):
    start(server, "s-acted", StandInSession())
    assert asyncio.run(server.fresh_take("s-acted"))["error"]["type"] == "no_storyboard"


def test_leave_out_drops_a_recorded_step_from_the_live_timeline():
    from types import SimpleNamespace
    from reel_studio.engine import BrowserSession

    live = SimpleNamespace(
        timeline=[(1.0, None, 2.0), (5.5, None, 3.0), (9.25, None, 2.0)],
        timeline_pages=["a", "b", "c"], timeline_floors=[1.0, 1.0, 1.0], timeline_speeds=[1.0, 1.0, 1.0],
        timeline_glides=[None, None, None], timeline_cards=[False, False, False],
        narrations=[(1.0, "one.mp3", "one"), (5.5, "two.mp3", "two")], offscreen_first=False,
    )
    BrowserSession.leave_out(live, [5.5])
    assert [entry[0] for entry in live.timeline] == [1.0, 9.25]
    assert live.timeline_pages == ["a", "c"] and len(live.timeline_cards) == 2
    assert live.narrations == [(1.0, "one.mp3", "one")] and live.offscreen_first is False
    BrowserSession.leave_out(live, [1.0])
    assert live.timeline_pages == ["c"] and live.offscreen_first is True


def test_cookie_information_is_a_known_consent_manager():
    """upphandlingsmyndigheten.se's banner covered a source beat (2026-10-30)."""
    assert "#cookie-information-template-wrapper" in boards.CONSENT_MANAGERS


def test_a_swedish_film_is_marked_and_timed_in_swedish():
    """"Source:" over a Swedish film, and a plan of 55 s that cut at 72 s
    (Agentanbud, 2026-10-30)."""
    swedish = [{"shot": "highlight", "line": "Och den hittar det du missar.", "text": "60 inget",
                "url": "https://news.example/vvs", "source": "https://news.example/vvs"},
               {"shot": "card", "line": "Förut var upphandling för den som hade råd.", "text": "Förut",
                "source": "https://news.example/vvs"}]
    english = [{"shot": "card", "line": "You cannot approve what you cannot see.", "text": "See"}]
    assert boards.film_language(swedish) == "sv" and boards.film_language(english) == "en"
    assert boards.line_seconds("ett två tre fyra fem sex sju åtta nio tio", "sv") > \
        boards.line_seconds("one two three four five six seven eight nine ten", "en")
    planned = check(swedish)["beats"]
    assert {beat["language"] for beat in planned} == {"sv"}
    mark = next(op for op in boards.recipe(planned[0]) if op.get("action", {}).get("type") == "annotate")
    assert "Källa: news.example" in str(mark)
    page = urllib.parse.unquote(boards.card_page("Förut", source="https://news.example/vvs", language="sv"))
    assert "Källa: news.example" in page and "Source:" not in page


def test_draw_mouse_is_an_input_option(monkeypatch, tmp_path):
    """After -i, ffmpeg read -draw_mouse as an output option and ignored it:
    show_pointer=false still recorded the pointer (Sluss, 2026-10-31)."""
    from reel_studio import render

    seen = []
    monkeypatch.setattr(render.subprocess, "Popen", lambda args, **kwargs: seen.append(args))
    render.start_recording(":99", 640, 360, tmp_path / "screen.mp4", draw_mouse=False)
    args = seen[0]
    assert args.index("-draw_mouse") < args.index("-i") and args[args.index("-draw_mouse") + 1] == "0"

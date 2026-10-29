"""Storyboards: the director writes the film, AgentTake shoots it.

Sixteen takes of the FlowWink film failed on procedure, never on writing: an
intro skipped, a menu left open, a glide reported as moving when it did not
(2026-10-25). A director model is good at research and at writing lines; it is
not good at issuing seventy low-level acts in the right order. So the director
hands over beats — {shot, line, source?, plus the shot's fields} — and each
shot type is a recipe of engine actions that has been shot before.

This module is the part that needs no browser: the beat model, the checks
plan_storyboard runs before anything records, the recipes (pure functions from
a beat to a list of ops) and the runner that plays ops against a session.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
import urllib.parse
from typing import Any, Awaitable, Callable, Literal, get_args

from pydantic import BaseModel, Field, ValidationError

from .review import describe_cover, focus_covered, normalize_line
from .schema import OFFSCREEN_WAIT_MAX_MS, secret_names


ShotType = Literal[
    "source", "highlight", "card", "image", "page_tour", "login", "system", "live_answer", "cta",
]
SHOT_TYPES: tuple[str, ...] = get_args(ShotType)

# What each shot needs besides its line, and what it does with it: the menu
# the director picks from, stated once for the tool schema, the checks and
# the handbook.
SHOT_FIELDS: dict[str, str] = {
    "source": "url, wait_for_text (the headline): opens the article, closes a consent banner offscreen without accepting it, "
              "settles, then a slow push-in on the headline",
    "highlight": "text (a short quote on the page), url optional (default: the page before), source: "
                 "scrolls to the words, marks them, close shot kept above the subtitle band",
    "card": "text, background optional (auto, brand, blur, a colour, or an https image), source when it states a number",
    "image": "url (an https image), caption optional (a lower third): a slow Ken Burns drift over the still",
    "page_tour": "url, wait_for_text optional: a glide from the hero to the footer, once per page",
    "login": "url (the sign-in page), username, password_secret (a secret's NAME), wait_for_text (what the "
             "page after sign-in shows): signs in offscreen, line optional",
    "system": "url, wait_for_text, zoom optional (page zoom 0.5-2, for readable text): a page, or one "
              "section of a page, held with a push-in on wait_for_text",
    "live_answer": "input_text, wait_for_text optional (words the answer must have), timeout optional (seconds, "
                   "default 120), url optional (default: the page before): sends offscreen, waits until the "
                   "answer stops growing, shows it",
    "cta": "url, wait_for_text optional: the page the viewer should go to",
}

# Shots that state something about the world: the viewer is owed where it
# comes from. A card counts when it carries a number.
CLAIM_SHOTS = {"source", "highlight"}
# Behind a sign-in: a 401 or a redirect to a login form is expected there.
PRIVATE_SHOTS = {"system", "live_answer"}

# 150 words a minute, the pace of the voices AgentTake uses.
WORDS_PER_SECOND = 2.5
# The least a shot holds however short its line: a glide needs time to move,
# a card time to be read.
SHOT_MIN_SECONDS = {"card": 2.5, "image": 3.0, "page_tour": 6.0}
DEFAULT_MIN_SECONDS = 1.5
# A "one minute" film may run a little over; past this it is not one minute.
LENGTH_TOLERANCE_SECONDS = 10
LONG_LINE_WORDS = 35
LONG_QUOTE_CHARS = 200

GLIDE_MIN_MS = 6000
GLIDE_MAX_MS = 20000
OPEN_TIMEOUT_MS = 30000
# A live answer has landed when the page's new text, the question left out,
# has stopped growing this long; under ANSWER_MIN_CHARS it is a "thinking"
# indicator, not an answer yet.
ANSWER_QUIET_SECONDS = 3.0
ANSWER_MIN_CHARS = 20
ANSWER_POLL_SECONDS = 0.5
SIGN_IN_TIMEOUT_MS = 30000
IMAGE_READY = "image ready"
IMAGE_FAILED = "image failed"

# A cookie banner is closed without agreeing to anything: a button that
# refuses, else the consent manager is hidden from the page, else a button
# that only acknowledges a notice. There is no accept label on purpose.
CONSENT_REFUSE = (
    "Reject all", "Reject all cookies", "Reject", "Decline all", "Decline", "Refuse all", "Deny all", "Deny",
    "Do not consent", "Continue without accepting", "Only essential", "Essential only",
    "Only essential cookies", "Only necessary", "Necessary only", "Necessary cookies only",
    "Use necessary cookies only", "Endast nödvändiga", "Neka alla", "Avvisa alla",
    "Alle ablehnen", "Nur notwendige", "Tout refuser", "Continuer sans accepter",
)
CONSENT_MANAGERS = (
    ".fc-consent-root",                                   # Google Funding Choices (The Register)
    "#onetrust-consent-sdk", "#CybotCookiebotDialog", "#usercentrics-root", "#usercentrics-cmp-ui",
    "#didomi-host", ".qc-cmp2-container", "[id^='sp_message_container']",
    "#truste-consent-track", ".truste_box_overlay", "#cmpbox", "#cmpbox2",
    "iframe[title*='consent' i]",
)
CONSENT_NOTICE = ("Got it", "OK", "Okay", "I understand", "Dismiss")


class StoryBeat(BaseModel):
    shot: ShotType = Field(description="The shot type: " + "; ".join(f"{k}: {v}" for k, v in SHOT_FIELDS.items()))
    line: str = Field(default="", description="The narration for this beat, spoken once over the shot.")
    label: str = Field(default="", description="A short name for the beat, used in reports.")
    source: str | None = Field(default=None, description="The URL a claim comes from.")
    url: str | None = None
    wait_for_text: str | None = None
    text: str | None = None
    background: str | None = None
    caption: str | None = None
    username: str | None = None
    password_secret: str | None = None
    zoom: float | None = Field(default=None, ge=0.5, le=2.0)
    input_text: str | None = None
    timeout: float = Field(default=120, ge=1, le=OFFSCREEN_WAIT_MAX_MS / 1000)


def beats_json_schema() -> dict[str, Any]:
    """The beat object as inline JSON Schema (no $ref, as for actions)."""
    properties: dict[str, Any] = {
        "shot": {"type": "string", "enum": list(SHOT_TYPES),
                 "description": "; ".join(f"{k}: {v}" for k, v in SHOT_FIELDS.items())},
        "line": {"type": "string", "description": "The narration, spoken once over the shot"},
        "label": {"type": "string"},
        "source": {"type": "string", "description": "The URL a claim comes from"},
        "zoom": {"type": "number", "minimum": 0.5, "maximum": 2.0},
        "timeout": {"type": "number", "minimum": 1, "maximum": OFFSCREEN_WAIT_MAX_MS / 1000},
    }
    for name in ("url", "wait_for_text", "text", "background", "caption", "username",
                 "password_secret", "input_text"):
        properties[name] = {"type": "string"}
    return {"type": "object", "required": ["shot"], "properties": properties}


REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "source": ("url", "wait_for_text"),
    "highlight": ("text",),
    "card": ("text",),
    "image": ("url",),
    "page_tour": ("url",),
    "login": ("url", "password_secret"),
    "system": ("url", "wait_for_text"),
    "live_answer": ("input_text",),
    "cta": ("url",),
}
# The shots that may leave url out and stay on the page the beat before opened.
INHERITS_URL = {"highlight", "live_answer"}


def _item(beat: int | None, label: str, severity: str, category: str, message: str) -> dict:
    item = {"severity": severity, "category": category, "message": message}
    if beat is not None:
        item = {"beat": beat, "label": label, **item}
    return item


def _is_http(url: str | None) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def domain(url: str | None) -> str:
    try:
        host = urllib.parse.urlsplit(url or "").hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def tour_page(url: str) -> str:
    """One page whatever anchor or trailing slash names it: www.x.org/#features is x.org."""
    parsed = urllib.parse.urlsplit(url)
    return f"{domain(url)}{parsed.path.rstrip('/')}?{parsed.query}"


def has_statistic(text: str | None) -> bool:
    return bool(re.search(r"\d", text or ""))


def line_seconds(line: str) -> float:
    words = len((line or "").split())
    return words / WORDS_PER_SECOND + 0.3 if words else 0.0


def beat_seconds(beat: dict) -> float:
    return max(line_seconds(beat.get("line", "")),
               SHOT_MIN_SECONDS.get(beat["shot"], DEFAULT_MIN_SECONDS if beat.get("line") else 0.0))


def parse_storyboard(raw: list | None) -> tuple[list[dict], list[dict]]:
    """The beats, normalized, and what is wrong with their shape.

    Every beat comes back, broken ones included, so the checklist can name
    them all at once; a beat that does not parse at all is left out. A beat
    that may stay on the page before gets that page's url as ``page_url``,
    and a highlight on a page a source beat opened inherits its source.
    """
    beats: list[dict] = []
    items: list[dict] = []
    seen_lines: dict[str, int] = {}
    seen_tours: dict[str, int] = {}
    page_url: str | None = None
    page_source: str | None = None
    for number, entry in enumerate(raw or [], start=1):
        try:
            parsed = entry if isinstance(entry, StoryBeat) else StoryBeat.model_validate(entry)
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(part) for part in error.get("loc", ())) or "beat"
            items.append(_item(number, str((entry or {}).get("label") or "") if isinstance(entry, dict) else "",
                               "fix", "invalid_beat", f"{where}: {error['msg']}"))
            continue
        beat = {k: (v.strip() if isinstance(v, str) else v)
                for k, v in parsed.model_dump().items()}
        for name in ("text", "caption"):
            if beat.get(name):
                # An escaped newline is a newline (see render.card_text_lines).
                beat[name] = beat[name].replace("\\n", "\n")
        beat["number"] = number
        beat["label"] = beat["label"] or f"{number} {beat['shot']}"
        label = beat["label"]
        shot = beat["shot"]
        for name in REQUIRED_FIELDS[shot]:
            if not beat.get(name) and not (name == "url" and shot in INHERITS_URL):
                items.append(_item(number, label, "fix", "missing_field", f"a {shot} shot needs {name}"))
        if not beat["line"] and shot != "login":
            items.append(_item(number, label, "fix", "missing_line",
                               "every shot but login is narrated: give the exact line"))
        if beat["line"]:
            key = normalize_line(beat["line"])
            if key in seen_lines:
                items.append(_item(number, label, "fix", "repeated_line",
                                   f"the line repeats beat {seen_lines[key]}: every line is spoken once"))
            seen_lines.setdefault(key, number)
            if len(beat["line"].split()) > LONG_LINE_WORDS:
                items.append(_item(number, label, "warn", "long_line",
                                   f"{len(beat['line'].split())} words: split it, one thought per beat"))
        for name in ("url", "source"):
            if beat.get(name) and not _is_http(beat[name]):
                items.append(_item(number, label, "fix", "invalid_url", f"{name} must be an http(s) URL"))
        if shot == "image" and beat.get("url") and not beat["url"].startswith("https://"):
            items.append(_item(number, label, "fix", "invalid_url", "an image url must be https"))
        if shot == "card" and beat.get("background") and not _background_ok(beat["background"]):
            items.append(_item(number, label, "fix", "invalid_background",
                               "background is auto, brand, blur, a colour (#123456, navy) or an https image URL"))
        if beat.get("url") and shot not in ("card", "image"):
            beat["page_url"] = beat["url"]
        elif shot in INHERITS_URL:
            if page_url is None:
                items.append(_item(number, label, "fix", "missing_field",
                                   f"a {shot} shot without url stays on the page before, and there is none"))
            beat["page_url"] = page_url
            beat["inherits_url"] = True
        if shot in CLAIM_SHOTS and not beat.get("source"):
            # The page a quote is on is its source: the article a source shot
            # or the highlight itself opens. A highlight on a page some other
            # shot opened (a product tour) names its source itself.
            beat["source"] = beat.get("url") or (page_source if beat.get("inherits_url") else None)
        if shot == "highlight" and beat.get("text") and len(beat["text"]) > LONG_QUOTE_CHARS:
            items.append(_item(number, label, "warn", "long_quote",
                               f"{len(beat['text'])} characters: show a headline or a short quote, not a passage"))
        if (shot in CLAIM_SHOTS or (shot == "card" and has_statistic(beat.get("text")))) and not beat.get("source"):
            items.append(_item(number, label, "fix", "missing_source",
                               "a claim needs its source: add source=<the URL it comes from>"))
        elif "%" in beat["line"] or re.search(r"\bper ?cent\b", beat["line"], re.I):
            if not beat.get("source"):
                items.append(_item(number, label, "warn", "unsourced_number",
                                   "the line states a figure: put its source on this beat"))
        if shot == "live_answer" and beat.get("wait_for_text") and beat.get("input_text") and (
                beat["wait_for_text"].lower() in beat["input_text"].lower()):
            items.append(_item(number, label, "fix", "ambiguous_wait",
                               "wait_for_text is in input_text, so the question itself would match: "
                               "wait for words only the answer has"))
        if shot == "login" and not beat.get("wait_for_text"):
            items.append(_item(number, label, "warn", "unchecked_sign_in",
                               "give wait_for_text, something the page after sign-in shows, "
                               "or a failed sign-in goes unnoticed until the next beat"))
        if shot == "page_tour" and _is_http(beat.get("url")):
            # Every tour glides to the footer, so a second tour of the page
            # plays the same sections again under a different line: the first
            # Hermes storyboard toured agenthotel.org four times from four
            # anchors, and the footer was on screen under four lines (2026-10-28).
            page = tour_page(beat["url"])
            if page in seen_tours:
                items.append(_item(number, label, "fix", "repeated_tour",
                                   f"beat {seen_tours[page]} already tours this page, and a tour glides to the "
                                   "footer: the same sections would play under this line. To speak about one "
                                   "section, use a system shot (url, wait_for_text=<the section's heading>)"))
            seen_tours.setdefault(page, number)
        if shot == "login":
            # Where a sign-in lands is the app's to say: a beat after it that
            # stays on the page names its url, so a retake can go back to it.
            page_url = page_source = None
        elif beat.get("url") and shot not in ("card", "image"):
            page_url = beat["url"]
            page_source = beat.get("source") if shot == "source" else None
        beats.append(beat)
    return beats, items


_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")


def _background_ok(value: str) -> bool:
    return value in ("auto", "brand", "blur") or bool(_COLOR.match(value)) or value.startswith("https://")


PAYWALL_MARKERS = (
    re.compile(r'"isAccessibleForFree"\s*:\s*"?false', re.I),
    re.compile(r"subscribe to (?:continue|read|keep reading)", re.I),
    re.compile(r"(?:this (?:article|story) is|only) (?:for|available to) subscribers", re.I),
    re.compile(r"already a subscriber\?", re.I),
)
# An element the page hides is not a wall. Every TechTarget article carries
# its content gate's form, display: none and switched off — "Already a
# subscriber?" and all — and an article that reads in full was planned as a
# paywall (2026-10-28). A metered wall a script reveals later does not
# reach a recording either: each session starts with a fresh profile. Up to
# the first closing tag of the same name: a nested one leaves the rest in,
# which can only keep a marker, never invent one.
_HIDDEN = re.compile(
    r"<(\w+)\b[^>]*\bstyle\s*=\s*[\"'][^\"']*display\s*:\s*none[^>]*>.*?</\1\s*>", re.I | re.S
)


def looks_paywalled(html: str) -> bool:
    shown = _HIDDEN.sub(" ", html)
    return any(marker.search(shown) for marker in PAYWALL_MARKERS)


BROWSER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)


async def probe_url(url: str) -> dict:
    """How a URL answers: status, whether it looks paywalled, whether a bot
    check stood in the way. Never raises."""
    import httpx

    result: dict[str, Any] = {"url": url}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=12.0,
            headers={"User-Agent": BROWSER_AGENT, "Accept-Language": "en,sv;q=0.8"},
        ) as client:
            async with client.stream("GET", url) as response:
                result["status"] = response.status_code
                result["content_type"] = response.headers.get("content-type", "")
                result["challenge"] = (
                    response.headers.get("cf-mitigated") == "challenge"
                    or (response.status_code == 403 and "cloudflare" in response.headers.get("server", "").lower())
                )
                body = b""
                if "html" in result["content_type"]:
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > 400_000:
                            break
                text = body.decode("utf-8", "replace")
                result["paywall"] = looks_paywalled(text)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return result


ProbeFn = Callable[[str], Awaitable[dict]]


async def check_storyboard(
    raw: list | None,
    probe: ProbeFn | None = None,
    secrets: list[str] | None = None,
    target_seconds: int = 60,
) -> dict:
    """Everything plan_storyboard reports, without recording anything.

    ``fix`` items keep shoot_storyboard from starting; ``warn`` items are
    for the director to weigh.
    """
    beats, items = parse_storyboard(raw)
    if not raw:
        items.append(_item(None, "", "fix", "empty_storyboard", "beats is empty: write the film first"))
    estimated = round(sum(beat_seconds(beat) for beat in beats), 1)
    if estimated > target_seconds + LENGTH_TOLERANCE_SECONDS:
        items.append(_item(None, "", "warn", "over_length",
                           f"about {estimated:.0f} s of narration for a {target_seconds} s film: "
                           "cut lines or beats (150 words is a minute)"))
    known = {name.upper() for name in (secret_names() if secrets is None else secrets)}
    for beat in beats:
        wanted = [(beat.get("password_secret") or "").upper(), _secret_name(beat.get("username"))]
        for name in filter(None, wanted):
            name = name.removeprefix("REEL_SECRET_")
            if name not in known:
                items.append(_item(beat["number"], beat["label"], "fix", "unknown_secret",
                                   f"AgentTake has no secret {name}: its operator sets REEL_SECRET_{name}; "
                                   f"it has {', '.join(sorted(known)) or 'none'}"))
    urls: dict[str, list[tuple[dict, str]]] = {}
    for beat in beats:
        for name in ("url", "source"):
            if _is_http(beat.get(name)):
                urls.setdefault(beat[name], []).append((beat, name))
    probe = probe or probe_url
    answers = await asyncio.gather(*(probe(url) for url in urls))
    for answer, (url, uses) in zip(answers, urls.items()):
        for beat, name in uses:
            items.extend(_url_items(beat, name, url, answer))
    items.sort(key=lambda item: (item["severity"] != "fix", item.get("beat") or 0))
    return {
        "beats": beats,
        "checklist": items,
        "ready": not any(item["severity"] == "fix" for item in items),
        "estimated_seconds": estimated,
    }


def _secret_name(value: str | None) -> str | None:
    from .schema import SECRET_PLACEHOLDER

    match = SECRET_PLACEHOLDER.match(value or "")
    return (match.group(1) or match.group(2)).upper() if match else None


def _url_items(beat: dict, name: str, url: str, answer: dict) -> list[dict]:
    number, label, shot = beat["number"], beat["label"], beat["shot"]
    private = shot in PRIVATE_SHOTS and name == "url"
    status = answer.get("status")
    if answer.get("error"):
        return [_item(number, label, "fix", "unreachable_url", f"{name} {url} did not answer: {answer['error']}")]
    if status in (401, 403):
        if private:
            return [_item(number, label, "warn", "private_url",
                          f"{name} answers {status}: fine behind the sign-in a login beat does before it")]
        if answer.get("challenge"):
            return [_item(number, label, "warn", "bot_check",
                          f"{name} {url} answers {status} behind a bot check: it may still open in the "
                          "recording browser, but find another source if the shot fails")]
        return [_item(number, label, "fix", "forbidden_url",
                      f"{name} {url} answers {status}: a viewer cannot open it — find a public source")]
    if status is not None and status >= 400:
        return [_item(number, label, "fix", "broken_url", f"{name} {url} answers {status}")]
    found: list[dict] = []
    if answer.get("paywall"):
        found.append(_item(number, label, "warn", "paywall",
                           f"{name} {url} looks paywalled: show the headline only, or find an open source"))
    if shot == "image" and name == "url" and status and not str(answer.get("content_type", "")).startswith("image/"):
        found.append(_item(number, label, "warn", "not_an_image",
                           f"url answers {answer.get('content_type') or 'no content type'}, not an image"))
    return found


# ---------------------------------------------------------------------------
# The pages AgentTake draws itself: a card, a still image.

def _data_url(document: str) -> str:
    return "data:text/html;charset=utf-8," + urllib.parse.quote(document)


_PAGE_BASE = """<!doctype html><html><head><meta charset="utf-8"><style>
:root{color-scheme:light dark;--bg:#f4f1ea;--fg:#14161c;--muted:#5d6270}
@media (prefers-color-scheme: dark){:root{--bg:#0f1420;--fg:#f4f5f8;--muted:#a3a9b8}}
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--fg);
font-family:Inter,"Segoe UI",system-ui,sans-serif}
</style>"""


def card_page(text: str, background: str | None = None, source: str | None = None) -> str:
    """A full-screen card: the text on a colour or a dimmed image.

    auto (the default) follows the session's color_scheme. A data: URL, so
    the card is a page like any other: the recording, the cut on a new page
    and the camera all work on it unchanged.
    """
    background = (background or "auto").strip()
    if background in ("brand", "blur"):
        background = "auto"  # the render's own backgrounds; a page has neither
    style = ""
    if background.startswith("https://"):
        style = (f"background:linear-gradient(rgba(5,8,15,.62),rgba(5,8,15,.62)),"
                 f"url('{html.escape(background, quote=True)}') center/cover;color:#fff")
    elif background != "auto" and _COLOR.match(background):
        style = f"background:{background};color:#fff"
    paragraphs = "".join(f"<p>{html.escape(part)}</p>" for part in text.split("\n") if part.strip())
    credit = (f'<div class="credit">Source: {html.escape(domain(source))}</div>' if source else "")
    document = _PAGE_BASE + """<style>
body{display:flex;align-items:center;justify-content:center}
.card{max-width:72vw;text-align:center;font-weight:700;font-size:clamp(40px,4.6vw,92px);line-height:1.15;
letter-spacing:-.01em}
.card p{margin:.15em 0}
.credit{position:fixed;right:5vw;top:5vh;font-size:22px;opacity:.75;font-weight:500}
</style></head><body style="__STYLE__"><div class="card">__TEXT__</div>__CREDIT__</body></html>"""
    return _data_url(document.replace("__STYLE__", style).replace("__TEXT__", paragraphs).replace("__CREDIT__", credit))


def image_page(url: str, caption: str | None = None, source: str | None = None) -> str:
    """A still with a slow Ken Burns drift and an optional lower third.

    The lower third sits above the band burned-in subtitles take. Once the
    image has loaded the page adds "image ready" (transparent, but visible
    to wait_for_text), or "image failed" when it cannot load, so the step
    waits for the picture and a broken one fails the beat.
    """
    lower = ""
    if caption:
        lower = f'<div class="lower"><span>{html.escape(caption)}</span></div>'
    credit = f'<div class="credit">{html.escape(domain(source))}</div>' if source else ""
    document = _PAGE_BASE + """<style>
body{background:#000}
img{position:fixed;inset:0;width:100%;height:100%;object-fit:cover;transform-origin:60% 40%;
animation:kb 24s linear forwards}
@keyframes kb{from{transform:scale(1) translate(0,0)}to{transform:scale(1.14) translate(-1.5%,-1%)}}
.lower{position:fixed;left:6vw;bottom:19vh;max-width:60vw}
.lower span{display:inline-block;background:rgba(10,14,24,.82);color:#fff;border-left:6px solid #ffd166;
padding:14px 26px;font-size:clamp(24px,2.1vw,42px);font-weight:650;line-height:1.25}
.credit{position:fixed;right:3vw;top:3vh;color:#fff;opacity:.7;font-size:18px;text-shadow:0 1px 3px #000}
.status{position:fixed;left:0;top:0;opacity:0;font-size:10px}
</style></head><body>
<img src="__URL__" alt="" onload="mark('__READY__')" onerror="mark('__FAILED__')">__LOWER____CREDIT__
<script>function mark(t){var s=document.createElement('span');s.className='status';s.textContent=t;
document.body.appendChild(s)}</script></body></html>"""
    return _data_url(
        document.replace("__URL__", html.escape(url, quote=True)).replace("__LOWER__", lower)
        .replace("__CREDIT__", credit).replace("__READY__", IMAGE_READY).replace("__FAILED__", IMAGE_FAILED)
    )


# ---------------------------------------------------------------------------
# Recipes: a beat to the ops that shoot it.
#
# An op is one of
#   {"op": "act", "action": {...}, "narration": str}   — one engine action
#   {"op": "consent"}                                  — close a cookie banner
#   {"op": "shot", "zoom", "focus_text", "move_seconds", "lift", "fit"}  — the camera
#   {"op": "observe"}                                  — fresh refs and form fields
# An action's ref "@username", "@password" or "@message" is the field of that
# kind the last observe found.

def _act(action: dict, narration: str = "") -> dict:
    return {"op": "act", "action": action, "narration": narration}


def _shot(zoom: float, focus_text: str | None = None, move_seconds: float = 1.0, lift: bool = False,
          fit: bool = False) -> dict:
    return {"op": "shot", "zoom": zoom, "focus_text": focus_text, "move_seconds": move_seconds, "lift": lift,
            "fit": fit}


def _open(url: str, wait_for_text: str | None) -> list[dict]:
    goto: dict[str, Any] = {"type": "goto", "url": url, "offscreen": True}
    if wait_for_text:
        goto.update(wait_for_text=wait_for_text, wait_timeout_ms=OPEN_TIMEOUT_MS, settle_ms=800)
    else:
        goto["settle_ms"] = 2000
    return [_act(goto), {"op": "consent"}]


def _say(line: str, wait_for_text: str | None = None) -> dict:
    """The beat's narrated step: hold the frame for the line, after checking
    that what it talks about is on screen."""
    action: dict[str, Any] = {"type": "wait", "ms": 300}
    if wait_for_text:
        action["wait_for_text"] = wait_for_text
    return _act(action, line)


def glide_ms(line: str) -> int:
    return int(min(GLIDE_MAX_MS, max(GLIDE_MIN_MS, line_seconds(line) * 1000 + 1000)))


def recipe(beat: dict, retake: bool = False) -> list[dict]:
    """The ops that shoot one beat.

    ``retake``: the beat is shot again at the end of the session, so a beat
    that normally stays on the page before opens that page itself.
    """
    shot, line = beat["shot"], beat.get("line", "")
    wait_text = beat.get("wait_for_text")
    reopen = retake and beat.get("inherits_url") and beat.get("page_url")
    if shot == "source":
        return [
            *_open(beat["url"], wait_text),
            _act({"type": "scroll_to_text", "text": wait_text, "offscreen": True}),
            # Wide, then a slow push-in on the headline while the line plays.
            # Gentle: a full-width headline at 1.25x lost its last words off
            # both edges at 1280x720 (TNW, 2026-10-28).
            _shot(1.12, wait_text, move_seconds=3.0),
            _say(line, wait_text),
        ]
    if shot == "highlight":
        text = beat["text"]
        ops = _open(beat["url"], text) if beat.get("url") else (_open(beat["page_url"], text) if reopen else [])
        mark = beat.get("caption") or (f"Source: {domain(beat['source'])}" if beat.get("source") else "Quote")
        return [
            *ops,
            _act({"type": "scroll_to_text", "text": text, "offscreen": True}),
            # Close, but never closer than the whole quote: a standfirst that
            # wraps is as wide as its column, and at 1.8x it lost its last
            # words off the right edge (TechTarget, 2026-10-28).
            _shot(1.8, text, move_seconds=1.2, lift=True, fit=True),
            _act({"type": "annotate", "target_text": text, "text": mark, "style": "marker",
                  "ms": 4000, "wait_for_text": text}, line),
        ]
    if shot == "card":
        render_card = render_card_action(beat)
        if render_card is not None:
            return [_act(render_card, line)]
        first_words = " ".join(beat["text"].split("\n")[0].split()[:6])
        return [
            _act({"type": "goto", "url": card_page(beat["text"], beat.get("background"), beat.get("source")),
                  "offscreen": True, "wait_for_text": first_words, "settle_ms": 300}),
            _shot(1.06, None, move_seconds=3.0),
            _say(line, first_words),
        ]
    if shot == "image":
        return [
            _act({"type": "goto", "url": image_page(beat["url"], beat.get("caption"), beat.get("source")),
                  "offscreen": True, "wait_for_text": IMAGE_READY, "wait_timeout_ms": 20000, "settle_ms": 300}),
            _say(line, IMAGE_READY),
        ]
    if shot == "page_tour":
        return [
            *_open(beat["url"], wait_text),
            _act({"type": "scroll", "dy": 99999, "ms": glide_ms(line)}, line),
        ]
    if shot == "login":
        ops = [*_open(beat["url"], None), {"op": "observe"}]
        if beat.get("username"):
            ops.append(_act({"type": "type", "ref": "@username", "text": beat["username"], "offscreen": True}))
        submit: dict[str, Any] = {"type": "press_key", "ref": "@password", "text": "Enter", "offscreen": True,
                                  "settle_ms": 1500}
        if wait_text:
            submit.update(wait_for_text=wait_text, wait_timeout_ms=SIGN_IN_TIMEOUT_MS)
        ops += [
            _act({"type": "type", "ref": "@password", "text": "{{secret:%s}}" % beat["password_secret"],
                  "offscreen": True}),
            _act(submit),
        ]
        if line:
            ops += [{"op": "consent"}, _say(line, wait_text)]
        return ops
    if shot == "system":
        ops = _open(beat["url"], wait_text)
        if beat.get("zoom"):
            ops.append(_act({"type": "set_zoom", "text": f"{beat['zoom']:g}", "offscreen": True}))
        return [
            *ops,
            _act({"type": "scroll_to_text", "text": wait_text, "offscreen": True}),
            _shot(1.3, wait_text, move_seconds=2.5),
            _say(line, wait_text),
        ]
    if shot == "live_answer":
        # Sent offscreen, then the page is read until the answer stops
        # growing: waiting for words the director predicted gave no verdict
        # when the answer said something else (demo.sluss.eu, 2026-10-28).
        # "@answer" is the text the answer op framed: wait_for_text if given,
        # else the answer's first words.
        ops = _open(beat["url"], None) if beat.get("url") else (_open(beat["page_url"], None) if reopen else [])
        return [
            *ops,
            {"op": "observe"},
            _act({"type": "type", "ref": "@message", "text": beat["input_text"], "offscreen": True}),
            {"op": "answer_start"},
            _act({"type": "press_key", "ref": "@message", "text": "Enter", "offscreen": True}),
            {"op": "answer", "timeout": float(beat.get("timeout", 120)), "expect": wait_text,
             "question": beat["input_text"]},
            _act({"type": "scroll_to_text", "text": "@answer", "offscreen": True}),
            _shot(1.5, "@answer", move_seconds=1.2, lift=True),
            _say(line, "@answer"),
        ]
    if shot == "cta":
        return [
            *_open(beat["url"], wait_text),
            _shot(1.12, wait_text, move_seconds=3.0),
            _say(line, wait_text),
        ]
    raise ValueError(f"unknown shot {shot!r}")


# The render's own card (the card action, where this AgentTake has it) takes
# one or two short lines on the brand colour, a blur of the frame before, or a
# hex colour, and fades in and out. A card it cannot draw — an image behind
# the text, a named colour, a long text — is drawn as a page instead.
RENDER_CARD_TEXT_MAX = 140
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def render_card_action(beat: dict) -> dict | None:
    from .schema import ACTION_TYPES

    if "card" not in ACTION_TYPES:
        return None
    background = (beat.get("background") or "auto").strip()
    if background == "auto":
        background = "brand"
    if not (background in ("brand", "blur") or _HEX.match(background)):
        return None
    if len(beat["text"]) > RENDER_CARD_TEXT_MAX:
        return None
    return {"type": "card", "text": beat["text"], "background": background}


RunAction = Callable[[str, dict, str], Awaitable[tuple[dict, Any]]]


def new_text(before: str, now: str, question: str = "") -> str:
    """What the page says now that it did not before, the question left out:
    what lies between the longest common start and end of the two texts."""
    start = 0
    limit = min(len(before), len(now))
    while start < limit and before[start] == now[start]:
        start += 1
    end = 0
    while end < limit - start and before[len(before) - 1 - end] == now[len(now) - 1 - end]:
        end += 1
    added = now[start:len(now) - end]
    if question.strip():
        added = added.replace(question.strip(), " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", added)).strip()


def answer_focus(text: str) -> str:
    """Words to frame an answer by, when the director named none: the start
    of its first line with three words or more."""
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 3:
            return " ".join(words[:8])
    return " ".join(text.split()[:8])


async def watch_answer(
    read_text: Callable[[], Awaitable[str]], before: str, question: str, timeout: float,
    quiet: float | None = None, poll: float | None = None,
    clock: Callable[[], float] | None = None, sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> dict:
    """Read the page until its new text has stopped growing for ``quiet``
    seconds, or ``timeout`` has passed. Returns the text, whether it
    settled, and the seconds it took."""
    import time
    quiet = ANSWER_QUIET_SECONDS if quiet is None else quiet
    poll = ANSWER_POLL_SECONDS if poll is None else poll
    clock = clock or time.monotonic
    sleep = sleep or asyncio.sleep
    started = clock()
    text, changed = "", started
    while True:
        current = new_text(before, await read_text(), question)
        now = clock()
        if current != text:
            text, changed = current, now
        elif len(text) >= ANSWER_MIN_CHARS and now - changed >= quiet:
            return {"text": text, "settled": True, "seconds": round(now - started, 1)}
        if now - started >= timeout:
            return {"text": text, "settled": False, "seconds": round(now - started, 1)}
        await sleep(poll)


def answer_verdict(watched: dict, expect: str | None, timeout: float) -> tuple[str | None, str | None, str | None]:
    """(the text to frame, a failure reason, a note) for a watched answer."""
    text = watched["text"]
    said = text if len(text) <= 160 else text[:157] + "..."
    if not text:
        return None, f"no_answer: nothing new appeared on the page in {timeout:g} s", None
    still = "" if watched["settled"] else f" (still growing after {watched['seconds']:g} s)"
    if expect and expect.lower() not in text.lower():
        return None, (f"answer_without_text: the answer does not contain {expect!r}{still}; it said: {said!r}. "
                      "Wait for words it does say, or leave wait_for_text out"), None
    note = f"the answer was still growing after {watched['seconds']:g} s; it is shown as it was" if still else None
    return expect or answer_focus(text), None, note


def _with_answer(op: dict, answer: str) -> dict:
    """The op with "@answer" replaced by the text the answer op framed."""
    if op.get("focus_text") == "@answer":
        op = {**op, "focus_text": answer}
    action = op.get("action")
    if action and "@answer" in (action.get("text"), action.get("wait_for_text")):
        op = {**op, "action": {key: answer if value == "@answer" else value for key, value in action.items()}}
    return op


async def run_ops(session: Any, session_id: str, ops: list[dict], run_action: RunAction) -> dict:
    """Play a recipe's ops against a live session, in order, stopping at the
    first that fails. Returns ok, and on a failure the reason."""
    fields: dict[str, str] = {}
    notes: list[str] = []
    before = ""
    answer: str | None = None
    for number, op in enumerate(ops):
        kind = op["op"]
        if kind == "answer_start":
            before = await session.page_text()
            continue
        if kind == "answer":
            watched = await watch_answer(session.page_text, before, op.get("question", ""), op["timeout"])
            answer, reason, note = answer_verdict(watched, op.get("expect"), op["timeout"])
            if reason:
                return {"ok": False, "op": number, "notes": notes, "reason": reason}
            if note:
                notes.append(note)
            continue
        if answer is not None:
            op = _with_answer(op, answer)
        if kind == "consent":
            try:
                clicked = await session.dismiss_consent(CONSENT_REFUSE, CONSENT_MANAGERS, CONSENT_NOTICE)
            except Exception:
                clicked = None
            if clicked:
                notes.append(f"closed a consent banner ({clicked})")
            continue
        if kind == "observe":
            await session.observe("refs")
            fields = await session.form_fields()
            continue
        if kind == "shot":
            zoom = op["zoom"]
            result = await session.set_shot(
                "wide" if zoom <= 1.0 else "medium", zoom, None, op.get("focus_text"),
                op.get("move_seconds"), lift=op.get("lift", False), fit=op.get("fit", False),
            )
            # A shot with no focus pushes in on the centre on purpose.
            if result.get("camera_note") and op.get("focus_text"):
                notes.append(result["camera_note"])
            continue
        action = dict(op["action"])
        ref = action.get("ref") or ""
        if ref.startswith("@"):
            if not fields.get(ref[1:]):
                return {"ok": False, "op": number, "notes": notes,
                        "reason": f"no {ref[1:]} field found on {getattr(getattr(session, 'page', None), 'url', 'the page')}"}
            action["ref"] = fields[ref[1:]]
        payload, _ = await run_action(session_id, action, op.get("narration", ""))
        if not payload.get("ok"):
            error = payload.get("error") or {}
            return {"ok": False, "op": number, "notes": notes, "action": action["type"],
                    "reason": f"{action['type']}: {error.get('type', 'failed')}: {error.get('message', '')}".strip()}
        if op.get("narration") and focus_covered(payload.get("focus_cover")):
            # The step went through, but its line plays over a dialog, a
            # banner or an ad: a beat nobody can read is not shot.
            return {"ok": False, "op": number, "notes": notes, "action": action["type"],
                    "reason": f"focus_covered: {describe_cover(payload['focus_cover'])}. Close or avoid it, "
                              "or give the beat another url or wait_for_text"}
        if action["type"] == "scroll" and action.get("ms") and not (payload.get("scrolled") or {}).get("pixels"):
            notes.append("the glide moved nothing: the page is no taller than the screen")
    return {"ok": True, "notes": notes}

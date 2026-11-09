"""Personal data and secrets, in what a film types and what it shows.

The Sluss film typed a Swedish personal number that passed the check digit and
was not a test number. It went through seven takes and every review, and a
person found it by chance (2026-11-09). A film is a publication: a number that
may be somebody's is stopped before the shoot and again before approval.

Skatteverket's open list of test personal numbers is fetched when the image is
built (fetch_test_numbers), so a number is checked here and never sent anywhere
to be looked up. Without the list every personal number is stopped.
"""
from __future__ import annotations

from datetime import date
import gzip
import os
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Sequence

# REEL_TEST_NUMBERS names the file in the image (see Dockerfile).
TEST_NUMBERS_FILE = Path(os.environ.get("REEL_TEST_NUMBERS")
                         or Path(__file__).parent / "data" / "skatteverket-test-personnummer.txt.gz")

# 19811218-2392, 811218-2392, 811218+2392, 198112182392. Ten digits with no
# separator are left out: that is what a phone number or an order number looks
# like, and a false alarm stops the film.
_PERSONAL_NUMBER = re.compile(r"(?<![\d-])(?:(\d{8})-?(\d{4})|(\d{6})([-+])(\d{4}))(?![\d-])")
_CARD = re.compile(r"(?<![\d.-])(?:\d[ -]?){13,19}(?![\d-])")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")
_SECRETS = (
    (re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"), "a JWT token"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "a GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"), "an API key"),
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[a-zA-Z0-9]{16,}\b"), "an API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"), "a private key"),
)
# Addresses anyone may publish; they say nothing about a person.
_EXAMPLE_MAIL = re.compile(r"@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|test|invalid|localhost)$", re.I)
_ROLE_MAIL = re.compile(r"^(?:info|hello|hej|contact|kontakt|support|sales|press|privacy|security|abuse|legal|noreply|no-reply)@", re.I)


@lru_cache(maxsize=1)
def test_numbers() -> frozenset[str]:
    """Skatteverket's test personal numbers, as twelve digits; empty without the list."""
    if not TEST_NUMBERS_FILE.is_file():
        return frozenset()
    with gzip.open(TEST_NUMBERS_FILE, "rt", encoding="ascii") as listing:
        return frozenset(line.strip() for line in listing if line.strip())


def _luhn(digits: str) -> bool:
    total = 0
    for position, digit in enumerate(digits):
        value = int(digit) * (2 - position % 2)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _card_checks(digits: str) -> bool:
    """The Luhn check as cards use it: doubling starts at the second digit from the right."""
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit) * (1 + position % 2)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _is_date(century: int, short: str) -> bool:
    day = int(short[4:6])
    # A samordningsnummer is a personal number with 60 added to the day.
    if day > 60:
        day -= 60
    try:
        date(century * 100 + int(short[:2]), int(short[2:4]), day)
    except ValueError:
        return False
    return True


def _personal_numbers(text: str) -> list[tuple[str, bool]]:
    """Every valid-looking personal number in a text, and whether it is a test number."""
    found = []
    for match in _PERSONAL_NUMBER.finditer(text):
        long_date, long_end, short_date, separator, short_end = match.groups()
        if long_date:
            century, short = [int(long_date[:2])], long_date[2:] + long_end
            if century[0] not in (18, 19, 20):
                continue
        else:
            short = short_date + short_end
            # A "+" means a hundred years or older, so the century is either.
            century = [18, 19] if separator == "+" else [19, 20]
        if not _luhn(short):
            continue
        centuries = [value for value in century if _is_date(value, short)]
        if not centuries:
            continue
        found.append((match.group(0), any(f"{value}{short}" in test_numbers() for value in centuries)))
    return found


def _cards(text: str) -> list[str]:
    found = []
    for match in _CARD.finditer(text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and digits[0] in "3456" and len(set(digits)) > 2 and _card_checks(digits):
            found.append(match.group(0).strip())
    return found


def mask(value: str) -> str:
    """Enough of a value to find it on screen, without repeating it."""
    value = value.strip()
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:1]}***@{domain}"
    return f"{value[:4]}…" if len(value) > 4 else "…"


def scan(text: str | None) -> list[dict[str, Any]]:
    """What in a text must not reach a film, most serious first.

    Each hit is {kind, label, value, blocks}. ``value`` is masked: a report
    must not publish what it found. An e-mail address does not block: pages
    show them all the time, so it is listed for a person to look at.
    """
    if not text:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, label: str, value: str, blocks: bool) -> None:
        if value not in seen:
            seen.add(value)
            hits.append({"kind": kind, "label": label, "value": mask(value), "blocks": blocks})

    for value, is_test in _personal_numbers(text):
        if not is_test:
            add("personal_number", "a personal number outside Skatteverket's test list" if test_numbers() else
                "a personal number (this AgentTake has no copy of Skatteverket's test list to check it against)", value, True)
    for value in _cards(text):
        add("card_number", "a payment card number", value, True)
    for pattern, label in _SECRETS:
        for match in pattern.finditer(text):
            add("secret", label, match.group(0), True)
    for match in _EMAIL.finditer(text):
        value = match.group(0)
        if not _EXAMPLE_MAIL.search(value) and not _ROLE_MAIL.search(value):
            add("email", "an e-mail address", value, False)
    hits.sort(key=lambda hit: not hit["blocks"])
    return hits


# The fields of a beat that end up typed or spoken in the film.
BEAT_TEXT_FIELDS = ("input_text", "line", "text", "caption")
REPLACE_NUMBER = "Use a number from Skatteverket's test list, or leave the number out."


def storyboard_items(beats: Sequence[dict]) -> list[tuple[dict, str, dict]]:
    """(beat, field, hit) for everything a storyboard would put in the film."""
    return [(beat, name, hit) for beat in beats for name in BEAT_TEXT_FIELDS
            for hit in scan(str(beat.get(name) or ""))]


def findings(steps: Sequence[dict], texts: dict[int, str], frames: dict[int, str] | None = None) -> list[dict]:
    """Review findings for what the screen showed under the steps in the film.

    Steps left out of the film do not count: a replaced take stays in the
    recording but is not what a viewer sees. ``frames`` is what a framed shot
    had inside its frame, and is read instead of the whole page when known.
    """
    frames = frames or {}
    results: list[dict] = []
    for step in steps:
        idx = int(step.get("idx", 0))
        if step.get("offscreen") or not step.get("ok", True) or (idx not in texts and idx not in frames):
            continue
        for hit in scan(frames.get(idx, texts.get(idx))):
            results.append({
                "category": "sensitive_on_screen" if hit["blocks"] else "email_on_screen",
                "severity": "high" if hit["blocks"] else "low",
                "location": f"step[{idx}]",
                "measured": {"kind": hit["kind"], "starts": hit["value"]},
                "message": f"The page under step {idx} shows {hit['label']} ({hit['value']}).",
                "fix": ("Change what is typed or shown with edit_beat and reshoot_beat. "
                        f"{REPLACE_NUMBER if hit['kind'] == 'personal_number' else 'Show nothing that belongs to a real person or account.'} "
                        "A person decides if it may stay.")
                if hit["blocks"] else "Check that the address may be shown. Mask it with start_session(mask=...) if it may not.",
            })
    return results

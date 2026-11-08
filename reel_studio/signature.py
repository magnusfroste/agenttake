"""The signature: the house style a film carries.

A web agency is hired for a look nobody else has; a film an agent directs is
no different. Without a signature every film an instance makes is generic:
the director model picks a colour, the cards fall back to the system font,
the closing card is whoever's call to action. The signature (Swedish:
*signalement*, the description that lets you pick someone out of a crowd)
is one small document the instance owns: its colours, its type, the line it
signs with, the voice it speaks in, and the rules its director writes by.
The director never decides styling; it reads the signature and writes in
that voice. Every film records which signature version it was shot with, so
the style can evolve film by film without the old films changing.

The document lives at REEL_SIGNATURE, or signature.json under
REEL_OUTPUT_DIR. Missing, the instance has the default signature: the look
AgentTake had before signatures, unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import functools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT_NAME = re.compile(r"^[A-Za-z0-9 .+_-]{1,64}$")
MAX_LINE = 80
MAX_TONE_RULES = 12
MAX_RULE = 160


@dataclass(frozen=True)
class Signature:
    name: str = "default"
    version: int = 1
    # Colours. accent is the brand colour: rules, quote marks, the strip
    # under a hero card. ink is the dark ground solid cards are set on; empty
    # means "the accent", which is what AgentTake did before signatures.
    # paper is the text colour on ink.
    accent: str = "#1f2a44"
    ink: str = ""
    paper: str = "#ffffff"
    # What the title and closing cards are set on. "footage" is the film's
    # own first and last frame, blurred; "solid" is the house's ink, so the
    # sign-off looks the same on every film, whatever page it ended on.
    cards: str = "footage"
    # Type, by fontconfig family name. The display face sets cards, the text
    # face captions, the mono face addresses and small labels.
    display_font: str = "DejaVu Sans"
    text_font: str = "DejaVu Sans"
    mono_font: str = "DejaVu Sans Mono"
    # The closing card's small print: the line the house signs with, and the
    # AI label (EU AI Act, art. 50: a viewer is told when an AI made it).
    signature_line: str = ""
    ai_label: str = ""
    # The house voice: a voice id start_session uses unless told otherwise.
    voice: str = ""
    # Rules the director writes narration by. Prose, one rule per line; the
    # handbook quotes them, so an agent plans in the house voice.
    tone: tuple[str, ...] = ()
    changelog: tuple[dict[str, Any], ...] = ()

    @property
    def is_default(self) -> bool:
        return self.name == "default" and not self.ink and not self.signature_line

    @property
    def ground(self) -> str:
        """The colour a solid card is set on."""
        return self.ink or self.accent

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "version": self.version,
            "accent": self.accent, "ink": self.ink, "paper": self.paper, "cards": self.cards,
            "display_font": self.display_font, "text_font": self.text_font, "mono_font": self.mono_font,
            "signature_line": self.signature_line, "ai_label": self.ai_label,
            "voice": self.voice, "tone": list(self.tone), "changelog": [dict(entry) for entry in self.changelog],
        }


DEFAULT = Signature()


def signature_path() -> Path:
    custom = os.environ.get("REEL_SIGNATURE", "").strip()
    if custom:
        return Path(custom)
    return Path(os.environ.get("REEL_OUTPUT_DIR", "output")) / "signature.json"


def parse(document: Any) -> tuple[Signature, list[str]]:
    """A signature from its JSON document, and what was not taken: a bad
    colour or font keeps the default and is named, so a typo never leaves
    the instance without a look."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return DEFAULT, ["the signature must be a JSON object"]
    values: dict[str, Any] = {}

    def text(key: str, limit: int = MAX_LINE) -> None:
        raw = document.get(key)
        if raw is None:
            return
        if not isinstance(raw, str):
            problems.append(f"{key} must be text")
            return
        values[key] = " ".join(raw.split())[:limit]

    def colour(key: str, allow_empty: bool) -> None:
        raw = document.get(key)
        if raw is None:
            return
        if isinstance(raw, str) and (allow_empty and not raw.strip()):
            values[key] = ""
            return
        if isinstance(raw, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", raw.strip()):
            values[key] = raw.strip() if raw.strip().startswith("#") else f"#{raw.strip()}"
            return
        problems.append(f"{key} is not a hex colour: {raw!r}")

    def font(key: str) -> None:
        raw = document.get(key)
        if raw is None:
            return
        if isinstance(raw, str) and _FONT_NAME.match(raw.strip()):
            values[key] = raw.strip()
            return
        problems.append(f"{key} is not a font family name: {raw!r}")

    text("name", 40)
    if "name" in values:
        values["name"] = re.sub(r"[^a-z0-9-]+", "-", values["name"].lower()).strip("-") or "default"
    version = document.get("version", 1)
    values["version"] = version if isinstance(version, int) and version >= 1 else 1
    colour("accent", False)
    colour("ink", True)
    colour("paper", False)
    cards = document.get("cards")
    if cards in ("footage", "solid"):
        values["cards"] = cards
    elif cards is not None:
        problems.append(f"cards is footage or solid, not {cards!r}")
    for key in ("display_font", "text_font", "mono_font"):
        font(key)
    text("signature_line")
    text("ai_label")
    text("voice", 120)
    tone = document.get("tone")
    if tone is not None:
        if isinstance(tone, str):
            tone = [line for line in tone.splitlines()]
        if isinstance(tone, list) and all(isinstance(rule, str) for rule in tone):
            rules = [" ".join(rule.split())[:MAX_RULE] for rule in tone if rule.strip()]
            values["tone"] = tuple(rules[:MAX_TONE_RULES])
        else:
            problems.append("tone must be a list of rules")
    changelog = document.get("changelog")
    if isinstance(changelog, list):
        values["changelog"] = tuple(entry for entry in changelog if isinstance(entry, dict))
    return replace(DEFAULT, **values), problems


def load(path: Path | None = None) -> tuple[Signature, list[str]]:
    """The instance's signature. No file: the default. A file that will not
    parse: the default, and the reason."""
    path = path or signature_path()
    if not path.is_file():
        return DEFAULT, []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return DEFAULT, [f"{path.name} could not be read: {exc}"]
    return parse(document)


def current() -> Signature:
    """The signature now, read again when the file changes."""
    path = signature_path()
    try:
        stamp: Any = (str(path), path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(path), None)
    return _cached(stamp)


@functools.lru_cache(maxsize=4)
def _cached(stamp: tuple[str, int | None]) -> Signature:
    return load(Path(stamp[0]))[0]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def save(document: dict[str, Any], note: str = "", path: Path | None = None) -> tuple[Signature, list[str]]:
    """Make document the instance's signature: the version goes up by one
    from the signature on disk, and the note is kept in the changelog, so
    the style has a history a film can be read against."""
    path = path or signature_path()
    previous, _ = load(path)
    signature, problems = parse(document)
    entry = {"version": previous.version + 1 if path.is_file() else max(1, signature.version),
             "date": _now(), "note": " ".join(note.split())[:MAX_RULE]}
    history = tuple(previous.changelog) if path.is_file() else tuple(signature.changelog)
    signature = replace(signature, version=entry["version"], changelog=(*history, entry))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signature.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _cached.cache_clear()
    return signature, problems


@functools.lru_cache(maxsize=32)
def font_file(family: str, weight: str = "bold") -> str:
    """The font file fontconfig has for family, or the fallback face when
    the family is not installed, so a card always renders."""
    if shutil.which("fc-match") is None:
        return FALLBACK_FONT
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", f"{family}:{weight}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return FALLBACK_FONT
    path = result.stdout.strip()
    if result.returncode != 0 or not path or not Path(path).is_file():
        return FALLBACK_FONT
    # fontconfig answers with *some* face for any name; a wrong family means
    # the one asked for is missing, and the fallback is the honest answer.
    wanted = re.sub(r"[^a-z0-9]", "", family.lower())
    if wanted and wanted not in re.sub(r"[^a-z0-9]", "", Path(path).stem.lower()):
        return FALLBACK_FONT
    return path


def guide_section(signature: Signature) -> str:
    """The house style as the director handbook states it."""
    lines = [
        "## The house style (signature)",
        "",
        f"This instance signs as **{signature.name}** (version {signature.version}). "
        "The look is the instance's, not yours: the cards, captions and closing card are set "
        "by the signature, so you do not choose colours or fonts. You write in its voice.",
        "",
    ]
    if signature.tone:
        lines.append("Write every line by these rules:")
        lines.append("")
        lines.extend(f"- {rule}" for rule in signature.tone)
        lines.append("")
    if signature.voice:
        lines.append(f"The house voice is `{signature.voice}`; start_session uses it unless you ask for another.")
        lines.append("")
    if signature.signature_line or signature.ai_label:
        signed = " · ".join(part for part in (signature.signature_line, signature.ai_label) if part)
        lines.append(f"The closing card signs: *{signed}*. Do not write a sign-off into the narration; the card does it.")
        lines.append("")
    lines.append("Friction with the look or the voice goes to `submit_backlog(category=\"signature\")`; "
                 "the owner changes the signature with `set_signature`, and the next film shows it.")
    return "\n".join(lines) + "\n"

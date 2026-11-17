"""Graphics over a live page, as a news programme sets them: a callout (a
label that points at what the camera frames, in on the word that names it)
and a caption (a lower third: where and when).

Both are burned in at render, with the subtitles, not drawn in the page: the
camera is a crop made at render, so a label drawn in the page grew with every
push-in and slid away under every pan, and its timing hung on the recording's
clock instead of the film's.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Sequence
import unicodedata

GRAPHICS_FILE = "graphics.json"

# Read in a glance, under a line that is being spoken at the same time.
CALLOUT_MAX_WORDS = 5
CALLOUT_MAX_CHARS = 40
CAPTION_MAX_CHARS = 36

# A callout is on screen at least this long, comes in this much before the
# word it goes with, and never before the camera has stood still this long.
CALLOUT_MIN_SECONDS = 1.5
CALLOUT_LEAD = 0.15
CALLOUT_AFTER_MOVE = 0.2
# A word this long, or a number, ties a callout to the line that says it.
CUE_WORD_LETTERS = 4

# The share of the frame's height, from the bottom, that subtitles take (two
# lines of them): graphics stay above it.
SUBTITLE_BAND = 0.24
# Two parts with one caption are one caption when no more than this lies
# between them.
CAPTION_JOIN_SECONDS = 0.35

# The looks a house, or one film, can set its graphics in.
STYLES: dict[str, str] = {
    "label": "the callout filled in the accent, the lower third on the ink with an accent bar: a clear flag "
             "on any page (the default)",
    "ink": "both on the house's ink with the paper's text, the accent only as the lower third's bar: quiet, "
           "for busy or colourful pages",
    "paper": "both on the paper colour with the ink's text and an accent bar: for dark pages",
    "bare": "no plate: outlined text, the callout in the accent, the lower third in the paper colour: the "
            "lightest touch, for pages where a plate would hide too much",
}
DEFAULT_STYLE = "label"
# The ground a graphic is set on when the house names no ink.
DEFAULT_INK = "#101827"


_WORD = re.compile(r"[^\W_]+(?:[.,:][0-9]+)*%?")


def words(text: str | None) -> list[tuple[str, int]]:
    """The text's words, loosely (case, accents' form and punctuation aside),
    each with where it starts."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return [(match.group(), match.start()) for match in _WORD.finditer(text)]


def said_at(callout: str, line: str) -> float | None:
    """Where in the line the callout's words are said, as a share of it, when
    the line says them word for word; None when it does not."""
    wanted = [word for word, _ in words(callout)]
    spoken = words(line)
    if not wanted or not spoken:
        return None
    for start in range(len(spoken) - len(wanted) + 1):
        if [word for word, _ in spoken[start:start + len(wanted)]] == wanted:
            return spoken[start][1] / max(len(line), 1)
    return None


def cue_share(callout: str, line: str) -> float | None:
    """The share of the line after which the callout comes in: where the line
    says its words, or the first word they have in common that carries
    meaning (a number, or a word of CUE_WORD_LETTERS letters); None when the
    line names nothing of it."""
    exact = said_at(callout, line)
    if exact is not None:
        return exact
    keys = {word for word, _ in words(callout) if len(word) >= CUE_WORD_LETTERS or word[0].isdigit()}
    for word, at in words(line):
        if word in keys:
            return at / max(len(line), 1)
    return None


def callout_seconds(callout: str, line: str, line_seconds: float, move_seconds: float, hold_seconds: float) -> float:
    """When a callout comes in, in seconds after its line begins: on the word
    that names it, once the camera stands still, and early enough to be read."""
    share = cue_share(callout, line)
    wanted = share * line_seconds - CALLOUT_LEAD if share is not None else 0.0
    earliest = max(move_seconds, 0.0) + CALLOUT_AFTER_MOVE
    latest = max(earliest, hold_seconds - CALLOUT_MIN_SECONDS)
    return round(min(max(wanted, earliest), latest), 2)


def check_callout(text: str | None) -> str | None:
    """What is wrong with a callout's text, or None."""
    text = (text or "").strip()
    count = len(text.split())
    if count > CALLOUT_MAX_WORDS or len(text) > CALLOUT_MAX_CHARS:
        return (f"{count} words, {len(text)} characters: a callout is read in a glance while the line is "
                f"spoken, so {CALLOUT_MAX_WORDS} words and {CALLOUT_MAX_CHARS} characters at most")
    if "\n" in text:
        return "a callout is one line"
    return None


def check_caption(text: str | None) -> str | None:
    text = (text or "").strip()
    if len(text) > CAPTION_MAX_CHARS or "\n" in text:
        return (f"{len(text)} characters: a lower third says where and when in one short line, "
                f"{CAPTION_MAX_CHARS} characters at most (\"02:14 · Inbox\")")
    return None


# ---------------------------------------------------------------------------
# The record: what each step shows, keyed by the step's offset on the
# recording's clock, as cards and glides are.

def load_graphics(directory: Path) -> list[dict]:
    record = directory / GRAPHICS_FILE
    try:
        entries = json.loads(record.read_text()) if record.is_file() else []
    except (OSError, ValueError):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and "offset" in entry]


def record_graphic(directory: Path, offset: float, entry: dict) -> None:
    entries = [old for old in load_graphics(directory) if abs(float(old["offset"]) - offset) > 0.0005]
    entries.append({"offset": round(offset, 3), **entry})
    record = directory / GRAPHICS_FILE
    temporary = record.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries))
    temporary.replace(record)


def shift_graphics(entries: Sequence[dict], seconds: float) -> list[dict]:
    """The entries on a clock that starts this much earlier (a pickup joined
    onto a session's recording)."""
    return [{**entry, "offset": round(float(entry["offset"]) + seconds, 3)} for entry in entries]


def frame_box(box: dict | None, view: dict) -> list[float] | None:
    """A page box as shares of the frame the camera shows, or None when its
    middle is not in that frame."""
    if not box or not view.get("w") or not view.get("h"):
        return None
    x = (box["x"] - view["x"]) / view["w"]
    y = (box["y"] - view["y"]) / view["h"]
    w, h = box["width"] / view["w"], box["height"] / view["h"]
    if not (0.0 <= x + w / 2 <= 1.0 and 0.0 <= y + h / 2 <= 1.0):
        return None
    return [round(value, 4) for value in (x, y, w, h)]


# ---------------------------------------------------------------------------
# From the record to what the film shows, and when.

def graphic_cues(parts: Sequence[dict], offsets: dict[int, float], entries: Sequence[dict]) -> list[dict]:
    """The graphics as cues on the film's clock.

    ``parts`` is the film's storyboard, ``offsets`` each part's step offset
    (keyed as a part's "step" is). A caption runs for its part, and through
    the next when that has the same one; a callout from its moment to the end
    of its part.
    """
    by_offset = {round(float(entry["offset"]), 3): entry for entry in entries}
    starts = sorted(float(part.get("start") or 0.0) for part in parts)
    captions: list[dict] = []
    callouts: list[dict] = []
    for part in sorted(parts, key=lambda part: float(part.get("start") or 0.0)):
        if part.get("kind", "step") != "step" or part.get("step") not in offsets:
            continue
        entry = by_offset.get(round(float(offsets[part["step"]]), 3))
        if entry is None:
            continue
        start = float(part.get("start") or 0.0)
        duration = float(part.get("duration") or 0.0)
        following = [s for s in starts if s > start + 0.01]
        if following:
            duration = min(duration, following[0] - start)
        end = start + duration
        if duration <= 0.4:
            continue
        caption, callout = entry.get("caption"), entry.get("callout")
        if caption and caption.get("text"):
            last = captions[-1] if captions else None
            if last and last["text"] == caption["text"] and start - last["end"] <= CAPTION_JOIN_SECONDS:
                last["end"] = round(end, 2)
                last["boxes"].append(caption.get("box"))
            else:
                captions.append({"kind": "caption", "start": round(start, 2), "end": round(end, 2),
                                 "text": caption["text"], "boxes": [caption.get("box")]})
        if callout and callout.get("text") and callout.get("box"):
            at = min(float(callout.get("at") or 0.0), max(duration - 0.5, 0.0))
            callouts.append({"kind": "callout", "start": round(start + at, 2), "end": round(end, 2),
                             "text": callout["text"], "box": callout["box"],
                             "caption": bool(caption and caption.get("text"))})
    return [*captions, *callouts]


# ---------------------------------------------------------------------------
# Drawing: ASS events, in the frame's own pixels.

def _ass_colour(colour: str, alpha: int = 0) -> str:
    red, green, blue = (colour.lstrip("#")[i:i + 2] for i in (0, 2, 4))
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def _ass_fill(colour: str) -> str:
    """The colour as an override tag takes it (\\1c)."""
    red, green, blue = (colour.lstrip("#")[i:i + 2] for i in (0, 2, 4))
    return f"&H{blue}{green}{red}&".upper()


def _light(colour: str) -> bool:
    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue > 150


def _hex(colour: str | None, default: str) -> str:
    colour = (colour or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        return colour
    if re.fullmatch(r"#[0-9a-fA-F]{3}", colour):
        return "#" + "".join(c * 2 for c in colour[1:])
    return default


def _overlap(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


class Layout:
    """Where the graphics go in a frame of this size."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        # The subtitles' size (render.subtitles_ass), which the graphics follow.
        base = max(18, round(min(width, height) / 22))
        self.caption_size = round(base * 0.8)
        self.caption_pad = max(5, round(self.caption_size * 0.36))
        self.bar = max(4, round(self.caption_size * 0.2))
        self.callout_size = round(base * 0.88)
        self.callout_pad = max(6, round(self.callout_size * 0.4))
        self.tail = (round(self.callout_size * 0.8), round(self.callout_size * 0.55))
        self.margin = round(width * 0.04)
        self.floor = round(height * (1 - SUBTITLE_BAND))

    def text_width(self, text: str, size: int) -> float:
        """About how wide a bold line is: there is no font to measure with
        here, so wide enough for most faces."""
        return sum(0.34 if char in " .,:;·|!'il1" else 0.74 if char in "mwMW@%" else 0.6 for char in text) * size

    def caption_rect(self, text: str, top: bool) -> tuple[float, float, float, float]:
        height = self.caption_size * 1.16 + 2 * self.caption_pad
        width = self.bar + self.text_width(text, self.caption_size) + 2 * self.caption_pad
        y = round(self.height * 0.06) if top else self.floor - height
        return (self.margin, y, self.margin + width, y + height)

    def px(self, box: Sequence[float]) -> tuple[float, float, float, float]:
        x, y, w, h = box
        return (x * self.width, y * self.height, (x + w) * self.width, (y + h) * self.height)

    def caption_on_top(self, text: str, boxes: Sequence[Sequence[float] | None]) -> bool:
        """A lower third goes to the top when it would lie over what the
        camera frames."""
        rect = self.caption_rect(text, top=False)
        return any(box and _overlap(rect, self.px(box)) for box in boxes)

    def callout(self, text: str, box: Sequence[float], ceiling: float | None = None) -> dict:
        """A callout's place: over what it points at when there is room,
        under it otherwise, never in the subtitles' band."""
        left, top, right, bottom = self.px(box)
        size, pad = self.callout_size, self.callout_pad
        tail_width, tail_height = self.tail
        width = self.text_width(text, size) + 2 * pad
        height = size * 1.16 + 2 * pad
        gap = round(size * 0.25)
        floor = self.floor if ceiling is None else min(self.floor, ceiling)
        roof = round(self.height * 0.03)
        # Point at the words' start when they are a long row: its middle is
        # nowhere in particular.
        aim = min((left + right) / 2, left + max(size * 3, width / 2))
        aim = min(max(aim, self.margin + tail_width), self.width - self.margin - tail_width)
        centre = min(max(aim, self.margin + width / 2), self.width - self.margin - width / 2)
        above = top - gap - tail_height - height >= roof
        below = bottom + gap + tail_height + height <= floor
        if above or not below:
            tip = max(top - gap, roof + height + tail_height)
            return {"above": True, "aim": aim, "tip": tip, "centre": centre, "edge": tip - tail_height}
        tip = bottom + gap
        return {"above": False, "aim": aim, "tip": tip, "centre": centre, "edge": tip + tail_height}


def look_of(style: str | None, accent: str | None, ink: str | None = None, paper: str | None = None) -> dict:
    """The colours one of STYLES sets the graphics in, from the house's own:
    each plate and its text, the callout's pointer, the lower third's bar,
    and whether there is a plate at all."""
    style = style if style in STYLES else DEFAULT_STYLE
    accent = _hex(accent, "#ffd166")
    ink = _hex(ink, DEFAULT_INK)
    paper = _hex(paper, "#ffffff")
    on_accent = DEFAULT_INK if _light(accent) else "#ffffff"
    if style == "ink":
        return {"style": style, "plate": True, "callout": (ink, paper), "tail": ink, "caption": (ink, paper), "bar": accent}
    if style == "paper":
        return {"style": style, "plate": True, "callout": (paper, ink), "tail": paper, "caption": (paper, ink), "bar": accent}
    if style == "bare":
        return {"style": style, "plate": False, "callout": (ink, accent), "tail": accent, "caption": (ink, paper), "bar": accent}
    return {"style": style, "plate": True, "callout": (accent, on_accent), "tail": accent, "caption": (ink, "#ffffff"), "bar": accent}


def ass_styles(width: int, height: int, face: str, look: dict) -> list[str]:
    """The styles the graphics' events use (after the subtitles' Default).
    With a plate, the text's box is the plate; without, its outline is what
    keeps it readable on any page."""
    layout = Layout(width, height)
    fmt = "Style: {name},{face},{size},{text},{text},{box},{box},1,0,0,0,100,100,0,0,{border},{outline},0,{align},0,0,0,1"

    def style(name: str, size: int, pad: int, colours: tuple[str, str], align: int) -> str:
        ground, text = colours
        return fmt.format(name=name, face=face, size=size, text=_ass_colour(text), box=_ass_colour(ground),
                          border=3 if look["plate"] else 1,
                          # Thin: a heavy outline fills the letters' counters.
                          outline=pad if look["plate"] else round(max(1.5, size * 0.06), 1), align=align)

    return [
        style("Caption", layout.caption_size, layout.caption_pad, look["caption"], 7),
        style("Callout", layout.callout_size, layout.callout_pad, look["callout"], 2),
        fmt.format(name="Shape", face=face, size=20, text=_ass_colour(look["bar"]), box=_ass_colour(look["bar"]),
                   border=1, outline=0, align=7),
    ]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def _time(seconds: float) -> str:
    centis = int(round(max(seconds, 0.0) * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _shape(points: Sequence[tuple[float, float]]) -> str:
    first, *rest = [(round(x), round(y)) for x, y in points]
    return f"m {first[0]} {first[1]} l " + " ".join(f"{x} {y}" for x, y in rest)


def ass_events(cues: Sequence[dict], width: int, height: int, look: dict | None = None) -> list[str]:
    """The cues as ASS dialogue lines, over the subtitles' layer."""
    layout = Layout(width, height)
    look = look or look_of(None, None)
    bar_colour, tail_colour = _ass_fill(look["bar"]), _ass_fill(look["tail"])
    lines: list[str] = []
    placed: list[tuple[dict, bool]] = []
    for cue in cues:
        if cue["kind"] != "caption":
            continue
        top = layout.caption_on_top(cue["text"], cue.get("boxes") or [])
        placed.append((cue, top))
        left, y, _, bottom = layout.caption_rect(cue["text"], top)
        when = f"{_time(cue['start'])},{_time(cue['end'])}"
        pad, bar = layout.caption_pad, layout.bar
        lines.append(
            f"Dialogue: 2,{when},Shape,,0,0,0,,{{\\an7\\pos(0,0)\\fad(200,200)\\1c{bar_colour}\\p1}}"
            + _shape([(left, y), (left + bar, y), (left + bar, bottom), (left, bottom)]) + "{\\p0}")
        lines.append(
            f"Dialogue: 1,{when},Caption,,0,0,0,,{{\\an7\\pos({round(left + bar + pad)},{round(y + pad)})"
            f"\\fad(200,200)}}{_escape(cue['text'])}")
    for cue in cues:
        if cue["kind"] != "callout":
            continue
        # A lower third on screen with it keeps its place: the callout stays
        # clear of that, too.
        ceiling = None
        for caption, top in placed:
            if not top and caption["start"] < cue["end"] and cue["start"] < caption["end"]:
                rect = layout.caption_rect(caption["text"], top=False)
                box = layout.px(cue["box"])
                if box[0] < rect[2]:
                    ceiling = rect[1] - round(layout.callout_size * 0.3)
        place = layout.callout(cue["text"], cue["box"], ceiling)
        when = f"{_time(cue['start'])},{_time(cue['end'])}"
        tail_width, _ = layout.tail
        pad = layout.callout_pad
        # One pixel into the label, so no seam shows between the two.
        edge = place["edge"] + (1 if place["above"] else -1)
        lines.append(
            f"Dialogue: 3,{when},Shape,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,160)\\1c{tail_colour}\\p1}}"
            + _shape([(place["aim"] - tail_width / 2, edge), (place["aim"] + tail_width / 2, edge),
                      (place["aim"], place["tip"])]) + "{\\p0}")
        anchor = 2 if place["above"] else 8
        text_y = place["edge"] - pad if place["above"] else place["edge"] + pad
        lines.append(
            f"Dialogue: 3,{when},Callout,,0,0,0,,{{\\an{anchor}\\pos({round(place['centre'])},{round(text_y)})"
            f"\\fad(160,160)}}{_escape(cue['text'])}")
    return lines

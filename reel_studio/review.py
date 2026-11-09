"""What review_session checks beyond the step rows: the planned beats, the
picture under each line, menus left open, and a contact sheet of the film.

"Hermes runs Nordbrygg" (16 takes, 2026-10-24/25) scored 95-100 on takes
that skipped beats, narrated over a page that had not loaded and left a menu
open on screen. A human caught each one from a contact sheet of the video;
these checks catch them from the same evidence.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .render import FONT_PATH, STORYBOARD_DIR, storyboard_still_at


# Beats -------------------------------------------------------------------

def normalize_line(text: str | None) -> str:
    """A line as spoken: case, punctuation and spacing do not change it."""
    lowered = (text or "").lower().replace("’", "'")
    return " ".join(re.sub(r"[^\w\s]", " ", lowered).split())


def recorded_lines(steps: Sequence[dict]) -> list[tuple[int, str]]:
    """The narrated steps that reach the video, in the order it plays them
    (a beat recorded again at the end and moved into place counts where it
    plays): (step idx, line)."""
    from .store import play_order  # store reaches this module through engine

    lines = []
    for step in play_order(list(steps)):
        line = normalize_line(step.get("narration_text"))
        if line and step.get("ok", True) and not step.get("offscreen"):
            lines.append((int(step.get("idx", 0)), line))
    return lines


def compare_beats(beats: Sequence[dict], steps: Sequence[dict]) -> dict[str, Any]:
    """Match the planned beats against the recorded lines.

    The longest run of beats recorded in the planned order counts as in
    order; a planned line recorded elsewhere is out of order, one never
    recorded is missing, and one recorded twice is repeated.
    """
    planned = [normalize_line(beat.get("line")) for beat in beats]
    recorded = recorded_lines(steps)
    spoken = [line for _, line in recorded]
    # Longest common subsequence: which beats sit in the planned order.
    rows, cols = len(planned), len(spoken)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            table[i][j] = (table[i + 1][j + 1] + 1 if planned[i] == spoken[j]
                           else max(table[i + 1][j], table[i][j + 1]))
    in_order: dict[int, int] = {}
    i = j = 0
    while i < rows and j < cols:
        if planned[i] == spoken[j]:
            in_order[i] = recorded[j][0]
            i, j = i + 1, j + 1
        elif table[i][j + 1] >= table[i + 1][j]:
            # On a tie keep the earlier beat: of a hook recorded after the
            # closing line, the closing line is the one out of place.
            j += 1
        else:
            i += 1

    def describe(index: int) -> dict[str, Any]:
        beat = beats[index]
        return {"beat": index, "label": beat.get("label") or f"beat {index + 1}", "line": beat.get("line", "")}

    missing, out_of_order, repeated = [], [], []
    for index, line in enumerate(planned):
        at = [idx for idx, spoken_line in recorded if spoken_line == line]
        if index not in in_order:
            if at:
                out_of_order.append({**describe(index), "steps": at})
            else:
                missing.append(describe(index))
        if len(at) > 1:
            repeated.append({**describe(index), "steps": at})
    planned_set = set(planned)
    return {
        "planned": len(planned),
        "in_order": len(in_order),
        "missing": missing,
        "out_of_order": out_of_order,
        "repeated": repeated,
        "unplanned_steps": [idx for idx, line in recorded if line not in planned_set],
    }


def beat_findings(comparison: dict[str, Any]) -> list[dict]:
    findings = []
    for beat in comparison["missing"]:
        findings.append({
            "category": "beat_missing",
            "severity": "high",
            "location": f"beat[{beat['beat']}]",
            "message": f"Planned beat {beat['label']!r} was never recorded: \"{beat['line']}\"",
        })
    for beat in comparison["out_of_order"]:
        steps = ", ".join(str(idx) for idx in beat["steps"])
        findings.append({
            "category": "beat_out_of_order",
            "severity": "high",
            "location": f"beat[{beat['beat']}]",
            "message": f"Planned beat {beat['label']!r} was recorded out of the planned order (step {steps})",
        })
    for beat in comparison["repeated"]:
        steps = ", ".join(str(idx) for idx in beat["steps"])
        findings.append({
            "category": "beat_repeated",
            "severity": "medium",
            "location": f"beat[{beat['beat']}]",
            "message": f"Planned beat {beat['label']!r} is spoken {len(beat['steps'])} times (steps {steps}); "
                       "hide the extra with edit_step(hidden=true) and rerender",
        })
    return findings


# Near-empty frames -------------------------------------------------------

# The picture is measured at this size, in cells of DETAIL_CELL pixels. The
# bottom fifth is left out: AgentTake draws its captions there, 34 px from the
# bottom, and burns subtitles in there, and either over a blank page made it
# look busy.
DETAIL_WIDTH, DETAIL_HEIGHT, DETAIL_CELL = 320, 140, 20
DETAIL_CROP = 0.8
# A cell has detail when 1% of its pixels are edges each way.
EDGE_STEP = 40
# Measured on the Nordbrygg takes and AgentHotel's own: a page still showing
# its loading spinner 0.02, the faintest real page (a dim sign-in card on
# black) 0.06, other sign-in pages 0.1-0.13, a dashboard 0.3-0.8.
NEAR_EMPTY_DETAIL = 0.04


def frame_pixels(source: Path, at: float = 0.0) -> bytes | None:
    """A frame of a video (at `at` seconds) or a still, as DETAIL_WIDTH x
    DETAIL_HEIGHT grey bytes."""
    # No -ss at 0: on a still image it yields no frame at all.
    seek = ["-ss", f"{at:.3f}"] if at > 0 else []
    command = [
        "ffmpeg", "-loglevel", "error", *seek, "-i", str(source),
        "-frames:v", "1",
        "-vf", f"crop=iw:ih*{DETAIL_CROP}:0:0,scale={DETAIL_WIDTH}:{DETAIL_HEIGHT},format=gray",
        "-f", "rawvideo", "-",
    ]
    try:
        pixels = subprocess.run(command, capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return pixels if len(pixels) == DETAIL_WIDTH * DETAIL_HEIGHT else None


def frame_detail(pixels: bytes, width: int = DETAIL_WIDTH, height: int = DETAIL_HEIGHT,
                 cell: int = DETAIL_CELL) -> float:
    """The share of the picture that has any detail, 0-1.

    Counted cell by cell rather than over the whole frame, so a blank page
    with a caption still reads as nearly empty and a sparse but real page (a
    heading, a form) does not. A cell has detail when it has edges running
    both ways: text, icons, pictures. A straight line — a top bar's edge, a
    border, a divider — runs one way, and a blank page under a nav bar
    scored as high as a real sign-in page while lines counted.
    """
    columns, rows = width // cell, height // cell
    across = [0] * (columns * rows)
    down = [0] * (columns * rows)
    for y in range(rows * cell - 1):
        line = y * width
        cell_row = (y // cell) * columns
        for x in range(columns * cell - 1):
            here = pixels[line + x]
            if abs(pixels[line + x + 1] - here) > EDGE_STEP:
                across[cell_row + x // cell] += 1
            if abs(pixels[line + width + x] - here) > EDGE_STEP:
                down[cell_row + x // cell] += 1
    enough = cell * cell // 100
    busy = sum(1 for a, d in zip(across, down) if a >= enough and d >= enough)
    return busy / len(across) if across else 0.0


def narrated_step_parts(parts: Sequence[dict], steps: Sequence[dict]) -> list[tuple[dict, dict]]:
    """Each narrated step with its storyboard part: where its line plays in the video."""
    visible = [s for s in steps if s.get("offset_seconds") is not None and not s.get("offscreen")]
    pairs = []
    for part in parts:
        if part.get("kind") != "step" or part.get("step") is None or part["step"] >= len(visible):
            continue
        step = visible[part["step"]]
        if (step.get("narration_text") or "").strip():
            pairs.append((step, part))
    return pairs


def step_frames(video: Path, parts: Sequence[dict], steps: Sequence[dict], out_dir: Path) -> list[dict]:
    """A frame under each narrated line, with how much of it has detail.

    The storyboard's still of the step's part when there is one: the render
    took it from the film before subtitles were burned in, and seeking the
    video costs most of a second a frame (a keyframe every 10 s). Otherwise
    a frame cut from the video 40% into the line.
    """
    stills = video.parent / STORYBOARD_DIR
    frames = []
    for step, part in narrated_step_parts(parts, steps):
        if step.get("action_type") == "card":
            # A text card is meant to be plain: not a page that had not loaded.
            continue
        still = stills / part["frame"] if part.get("frame") else None
        if still is not None and still.is_file():
            path, at = still, storyboard_still_at(part)
        else:
            at = float(part["start"]) + float(part["duration"]) * 0.4
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"step-{int(step['idx']):03d}.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{at:.3f}", "-i", str(video),
                     "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(path)],
                    check=True, capture_output=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                continue
        pixels = frame_pixels(path) if path.is_file() else None
        if pixels is None:
            continue
        frames.append({
            "label": f"step {step['idx']}",
            "step": step["idx"],
            "timestamp_seconds": round(at, 3),
            "detail": round(frame_detail(pixels), 3),
            "path": str(path),
        })
    return frames


def blank_frame_findings(frames: Sequence[dict]) -> list[dict]:
    return [
        {
            "category": "blank_frame",
            "severity": "high",
            "location": f"step[{frame['step']}]",
            "message": (
                f"The picture under step {frame['step']}'s line at {frame['timestamp_seconds']:.1f}s is "
                f"nearly empty ({frame['detail']:.0%} of it has any detail): a page that had not loaded? "
                "Wait for its content (wait_for_text) before the line, or hide the step"
            ),
            **({"frame": frame["path"]} if frame.get("path") else {}),
        }
        for frame in frames
        if frame.get("step") is not None and frame["detail"] < NEAR_EMPTY_DETAIL
    ]


# Menus left open ---------------------------------------------------------

# Counted after every step. A count above what the page had when it loaded is
# something a step opened; kept open past the step after it, it is on screen
# under a line that is not about it.
OPEN_OVERLAYS_JS = """() => {
  const kinds = [["[role=menu]", "menu"], ["[role=listbox]", "listbox"],
                 ["[data-radix-popper-content-wrapper]", "popover"]];
  const counts = {};
  for (const [selector, kind] of kinds) {
    for (const element of document.querySelectorAll(selector)) {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (box.width < 2 || box.height < 2 || style.visibility === "hidden"
          || style.display === "none" || Number(style.opacity) < 0.05) continue;
      if (box.bottom <= 0 || box.right <= 0 || box.top >= innerHeight || box.left >= innerWidth) continue;
      counts[kind] = (counts[kind] || 0) + 1;
    }
  }
  return counts;
}"""


def overlay_findings(steps: Sequence[dict]) -> list[dict]:
    findings = []
    baseline: dict[str, int] = {}
    previous_extra: dict[str, int] = {}
    page = None
    for step in steps:
        counts = step.get("open_overlays")
        if counts is None or not step.get("ok", True):
            continue
        if step.get("url") != page:
            # What a page has when it arrives is part of the page: a nav
            # built as role=menu, a listbox that is always shown.
            page, baseline, previous_extra = step.get("url"), dict(counts), {}
            continue
        baseline = {kind: min(number, counts.get(kind, 0)) for kind, number in baseline.items()}
        extra = {kind: number - baseline.get(kind, 0)
                 for kind, number in counts.items() if number > baseline.get(kind, 0)}
        left_open = sorted(kind for kind in extra if previous_extra.get(kind))
        if (left_open and (step.get("narration_text") or "").strip()
                and not step.get("offscreen")):
            findings.append({
                "category": "overlay_left_open",
                "severity": "medium",
                "location": f"step[{step.get('idx', 0)}]",
                "message": (
                    f"A {' and a '.join(left_open)} opened by an earlier step is still open under step "
                    f"{step.get('idx', 0)}'s line. Close it (press_key Escape) before the line, unless "
                    "the line is about it"
                ),
            })
        previous_extra = extra
    return findings


# The line's text under something else -------------------------------------

# wait_for_text passes on text a dialog covers: Playwright's is_visible does
# not look at what is on top. The Sluss film's source beats waited on The
# Register's headline and played their lines under Google's consent dialog for
# 22 s, and the director's own contact-sheet review called it "a cookie bar at
# the bottom" (2026-10-28). So the page is asked: points across the element's
# on-screen box, and at each the element the browser would hit there. One that
# is neither the element, inside it, nor around it is covering it; the layer
# reported is its outermost ancestor that does not hold the element. Take's own
# annotations take no pointer events, so they are never in the way.
FOCUS_COVER_JS = """(el) => {
  const box = el.getBoundingClientRect();
  const left = Math.max(box.left, 0), right = Math.min(box.right, innerWidth);
  const top = Math.max(box.top, 0), bottom = Math.min(box.bottom, innerHeight);
  if (right - left < 2 || bottom - top < 2) return {points: 0, covered: 0, by: null};
  const describe = (node) => {
    let layer = node;
    while (layer.parentElement && layer.parentElement !== document.body
           && !layer.parentElement.contains(el)) layer = layer.parentElement;
    const name = layer.tagName.toLowerCase() + (layer.id ? "#" + layer.id : "")
      + (typeof layer.className === "string" && layer.className.trim()
         ? "." + layer.className.trim().split(/\\s+/)[0] : "");
    let text = (layer.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 60);
    if (layer.tagName === "IFRAME") {
      try { text = new URL(layer.src).host; } catch (e) { text = "a frame"; }
    }
    return {element: name, position: getComputedStyle(layer).position, text};
  };
  let points = 0, covered = 0;
  const by = new Map();
  for (const fy of [0.2, 0.5, 0.8]) {
    for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      const hit = document.elementFromPoint(left + (right - left) * fx, top + (bottom - top) * fy);
      if (!hit) continue;
      points += 1;
      if (hit === el || el.contains(hit) || hit.contains(el)) continue;
      covered += 1;
      const layer = describe(hit);
      const seen = by.get(layer.element) || {...layer, count: 0};
      seen.count += 1;
      by.set(layer.element, seen);
    }
  }
  const worst = [...by.values()].sort((a, b) => b.count - a.count)[0] || null;
  if (worst) delete worst.count;
  return {points, covered, by: worst};
}"""

# A line whose text is at least half covered plays over the thing covering it.
FOCUS_COVERED_SHARE = 0.5


def focus_covered(cover: dict | None) -> bool:
    if not cover or not cover.get("points"):
        return False
    return cover.get("covered", 0) / cover["points"] >= FOCUS_COVERED_SHARE


def describe_cover(cover: dict) -> str:
    """'"Employees regularly paste…" is 12 of 15 points under div.fc-consent-root
    (fixed): "The Register asks for your consent…"'"""
    layer = cover.get("by") or {}
    what = layer.get("element") or "something"
    if layer.get("position") in ("fixed", "sticky"):
        what += f" ({layer['position']})"
    if layer.get("text"):
        what += f': "{layer["text"]}"'
    return f'"{cover.get("text", "")}" is {cover.get("covered", 0)} of {cover.get("points", 0)} points under {what}'


def focus_findings(steps: Sequence[dict]) -> list[dict]:
    findings = []
    for step in steps:
        cover = step.get("focus_cover")
        if (not focus_covered(cover) or not step.get("ok", True) or step.get("offscreen")
                or not (step.get("narration_text") or "").strip()):
            continue
        findings.append({
            "category": "focus_covered",
            "severity": "high",
            "location": f"step[{step.get('idx', 0)}]",
            "message": (
                f"Step {step.get('idx', 0)}'s line plays over a covered page: {describe_cover(cover)}. "
                "A dialog, banner or ad is in front of what the line is about. Close or avoid it and retake "
                "the step, or choose another page or text"
            ),
        })
    return findings


# The film, measured ------------------------------------------------------

# Sluss take 5 went out of Hermes's review as "all 16 ok" with a white page for
# a second and a half and a line running over the next page (2026-11-03). A
# director reading stills every two seconds cannot see either; the film can be
# read frame by frame instead. The numbers below come from Sluss takes 4 and 5:
# they find each flaw a person found in those films, and nothing else.
SCAN_FPS = 4
# Bright or dark enough to be an empty page, and with this little detail:
# heise.de before its image loaded measured 0.062, the faintest real page in
# these films well above it.
FLASH_BRIGHT, FLASH_DARK, FLASH_DETAIL = 235, 16, 0.08
FLASH_MIN_SECONDS = 0.5
# Between two samples, the share of pixels that moved by more than PIXEL_STEP.
PIXEL_STEP = 30
STILL_CHANGE = 0.01
# A cut inside a shot: this much at once, once the shot has opened...
HARD_CHANGE, HARD_FROM = 0.5, 1.0
# ...or this much once the camera's move into the shot is over (it eases for
# up to three seconds, and moves the picture by up to 0.16 meanwhile).
SOFT_CHANGE, SOFT_FROM = 0.1, 3.5
# The dissolve into the next shot starts this far before a part's end.
TAIL_SECONDS = 0.75
LONG_STILL, VERY_LONG_STILL = 7.0, 12.0


def scan_frames(video: Path, fps: int = SCAN_FPS) -> list[bytes]:
    """The film as grey frames of DETAIL_WIDTH x DETAIL_HEIGHT, without the
    band subtitles are burned into."""
    command = [
        "ffmpeg", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps={fps},crop=iw:ih*{DETAIL_CROP}:0:0,scale={DETAIL_WIDTH}:{DETAIL_HEIGHT},format=gray",
        "-f", "rawvideo", "-",
    ]
    try:
        raw = subprocess.run(command, capture_output=True, check=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    size = DETAIL_WIDTH * DETAIL_HEIGHT
    return [raw[start:start + size] for start in range(0, len(raw) - size + 1, size)]


def _moved(first: bytes, second: bytes) -> float:
    """The share of the picture that changed; every third pixel is enough."""
    a, b = first[::3], second[::3]
    return sum(1 for x, y in zip(a, b) if abs(x - y) > PIXEL_STEP) / len(a) if a else 0.0


def _is_empty(frame: bytes) -> bool:
    sample = frame[::7]
    level = sum(sample) / len(sample)
    return (level > FLASH_BRIGHT or level < FLASH_DARK) and frame_detail(frame) < FLASH_DETAIL


def film_findings(frames: Sequence[bytes], parts: Sequence[dict], steps: Sequence[dict],
                  fps: int = SCAN_FPS) -> list[dict]:
    """What shows only in the moving picture: a page that had not loaded, a
    line that runs over the next page, a picture that stands still too long."""
    if len(frames) < 2:
        return []
    visible = [s for s in steps if s.get("offset_seconds") is not None and not s.get("offscreen")]
    shots = [(part, visible[part["step"]]) for part in parts
             if part.get("kind") == "step" and part.get("step") is not None and part["step"] < len(visible)]

    def shot_at(seconds: float) -> tuple[dict, dict] | None:
        # Parts overlap by their dissolve; the later one has the screen.
        found = None
        for part, step in shots:
            if float(part["start"]) <= seconds < float(part["start"]) + float(part["duration"]):
                found = (part, step)
        return found

    changes = [0.0] + [_moved(frames[i - 1], frames[i]) for i in range(1, len(frames))]
    findings: list[dict] = []
    flashed: set[int] = set()
    run: list[int] = []
    for index in [*range(len(frames)), -1]:
        current = shot_at(index / fps) if index >= 0 else None
        if current is not None and current[1].get("action_type") != "card" and _is_empty(frames[index]):
            run.append(index)
            continue
        if len(run) / fps >= FLASH_MIN_SECONDS:
            at, seconds = run[0] / fps, len(run) / fps
            step = shot_at(at)[1]  # type: ignore[index]
            flashed.add(int(step.get("idx", 0)))
            findings.append({
                "category": "blank_flash", "severity": "high", "location": f"step[{step.get('idx', 0)}]",
                "at_seconds": at, "measured": {"seconds": seconds},
                "message": f"The screen is empty for {seconds:.2f} s at {at:.1f}s: a page that had not loaded.",
                "fix": "fresh_take with a wait_for_text on the page's main content, so the shot starts once it is there.",
            })
        run = []

    # One finding per shot: the first change is where the shot stops being right.
    reported: set[int] = set()
    for index, change in enumerate(changes):
        current = shot_at(index / fps)
        if current is None:
            continue
        part, step = current
        idx = int(step.get("idx", 0))
        into, left = index / fps - float(part["start"]), float(part["start"]) + float(part["duration"]) - index / fps
        if (idx in reported or idx in flashed or left <= TAIL_SECONDS
                or not (step.get("narration_text") or "").strip()
                or step.get("action_type") in ("scroll", "card") or float(step.get("speed") or 1.0) > 1.0):
            continue
        if (change >= HARD_CHANGE and into >= HARD_FROM) or (change >= SOFT_CHANGE and into >= SOFT_FROM):
            reported.add(idx)
            findings.append({
                "category": "picture_changes_under_line", "severity": "medium", "location": f"step[{idx}]",
                "at_seconds": round(index / fps, 2), "measured": {"changed": round(change, 3), "seconds_into_line": round(into, 2)},
                "message": (f"The picture changes {into:.1f} s into step {idx}'s line, {left:.1f} s before it ends: "
                            "the line is running over something else."),
                "fix": "Shorten the line with update_step_narration so it ends before the change, or fresh_take if the "
                       "wrong page is in the shot.",
            })

    start = 0
    for index in range(1, len(frames) + 1):
        if index < len(frames) and changes[index] <= STILL_CHANGE:
            continue
        seconds = (index - start) / fps
        if seconds >= LONG_STILL:
            current = shot_at((start + index) / 2 / fps)
            step = current[1] if current else None
            where = f"step[{step.get('idx', 0)}]" if step else "session"
            findings.append({
                "category": "long_still", "severity": "medium" if seconds >= VERY_LONG_STILL else "low",
                "location": where, "at_seconds": start / fps, "measured": {"seconds": seconds},
                "message": f"Nothing moves for {seconds:.1f} s from {start / fps:.1f}s.",
                "fix": "Shorten the line with update_step_narration, push in on what the line names (the beat's camera), "
                       "or split it into two beats.",
            })
        start = index
    return findings


# What a shot framed. Recorded when the shot is set up, because only the page
# knows where its words are: Sluss's audit beat cut "verify offline" at the
# frame's edge, and a README beat cut its own headline (take 3).
SHOT_FACTS_JS = """(view) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  const right = view.x + view.w, bottom = view.y + view.h;
  let lines = 0, count = 0;
  const cut = [];
  for (let node, seen = 0; (node = walker.nextNode()) && seen < 6000; seen++) {
    const words = node.textContent.replace(/\\s+/g, " ").trim();
    const parent = node.parentElement;
    if (words.length < 2 || !parent || ["SCRIPT", "STYLE", "NOSCRIPT"].includes(parent.tagName)) continue;
    const style = getComputedStyle(parent);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) < 0.05) continue;
    range.selectNodeContents(node);
    for (const rect of range.getClientRects()) {
      if (rect.width < 3 || rect.height < 3) continue;
      if (rect.bottom <= view.y || rect.top >= bottom || rect.right <= view.x || rect.left >= right) continue;
      lines += 1;
      const side = rect.left < view.x - 2 ? "left" : rect.right > right + 2 ? "right" : null;
      if (!side) continue;
      count += 1;
      if (cut.length < 60) cut.push({text: words.slice(0, 48), side, top: rect.top, bottom: rect.bottom});
    }
  }
  return {lines, cut_count: count, cut};
}"""

# Most of the focus box has to be in the frame.
FOCUS_IN_FRAME = 0.95
# A line of text at the focus's height, cut by the frame's edge...
NEAR_FOCUS_LINES = 1.5
# ...or this much of everything in the frame.
CUT_SHARE = 0.4
# The focus text as a share of the frame's height: 24 px in a 1080p film.
SMALL_TEXT = 0.022


def shot_findings(camera: Any, steps: Sequence[dict]) -> list[dict]:
    """A shot's frame against what the page had in it when it was set up."""
    if camera is None:
        return []
    by_offset = [s for s in steps if s.get("offset_seconds") is not None and not s.get("offscreen")
                 and s.get("ok", True) and (s.get("narration_text") or "").strip()]
    findings: list[dict] = []
    for key in camera.keys:
        facts = key.get("facts")
        step = next((s for s in by_offset if abs(float(s["offset_seconds"]) - float(key["t"])) < 0.06), None)
        if not facts or step is None or step.get("action_type") == "scroll":
            continue
        idx = int(step.get("idx", 0))
        zoom = float(key["zoom"])
        view = camera.view((zoom, float(key["cx"]), float(key["cy"])))
        focus = facts.get("focus") or {}
        common = {"location": f"step[{idx}]", "at_seconds": None}
        if focus.get("width") and focus.get("height"):
            across = min(focus["x"] + focus["width"], view["x"] + view["w"]) - max(focus["x"], view["x"])
            down = min(focus["y"] + focus["height"], view["y"] + view["h"]) - max(focus["y"], view["y"])
            share = max(0.0, across) * max(0.0, down) / (focus["width"] * focus["height"])
            if share < FOCUS_IN_FRAME:
                findings.append({
                    **common, "category": "focus_cropped", "severity": "high", "measured": {"in_frame": round(share, 2), "zoom": zoom},
                    "message": f"Only {share:.0%} of what step {idx}'s line is about is in the frame at zoom {zoom:g}.",
                    "fix": "Lower the beat's camera, or its page zoom, and fresh_take.",
                })
        cut = facts.get("cut") or []
        if focus.get("height"):
            reach = max(float(focus["height"]) * NEAR_FOCUS_LINES, 24.0)
            near = [line for line in cut if line["top"] < focus["y"] + focus["height"] + reach
                    and line["bottom"] > focus["y"] - reach]
        else:
            near = []
        lines, count = int(facts.get("lines") or 0), int(facts.get("cut_count") or 0)
        share = count / lines if lines else 0.0
        if near or share >= CUT_SHARE:
            sample = near or cut
            findings.append({
                **common, "category": "text_cut_at_edge", "severity": "medium" if near else "low",
                "measured": {"lines_cut": count, "lines_in_frame": lines, "next_to_focus": len(near), "zoom": zoom},
                "message": (f"The frame cuts {count} of {lines} lines of text at zoom {zoom:g}"
                            + (f", {len(near)} of them next to the focus" if near else "")
                            + f': "{sample[0]["text"]}" runs off the {sample[0]["side"]} edge.'),
                "fix": f"Set the beat's camera below {zoom:g}, or lower its page zoom, and fresh_take. rehearse_beats shows the frame first.",
            })
        font = float(facts.get("font_px") or 0.0)
        if font and font * zoom / camera.height < SMALL_TEXT:
            findings.append({
                **common, "category": "focus_text_small", "severity": "low",
                "measured": {"text_share_of_height": round(font * zoom / camera.height, 4), "zoom": zoom},
                "message": f"What step {idx}'s line is about is {font * zoom / camera.height:.1%} of the frame's height: "
                           "hard to read in a feed on a phone.",
                "fix": "Raise the beat's camera, and fresh_take. rehearse_beats shows the frame first.",
            })
    return findings


# What a line claims -------------------------------------------------------

# Sluss take 2 said "Most stayed in the house" over a chart reading 48%, and
# Hermes's review passed it. A number has to be on the screen it is said over;
# a word for a share is listed with the screen's own percentages to check.
PAGE_TEXT_FILE = "page-text.jsonl"
PAGE_TEXT_MAX = 20000
_NUMBER = re.compile(r"(?<![\w.,])\d[\d  ,.]*\d%?|(?<![\w.,])\d%?(?!\w)")
_YEAR = re.compile(r"^(19|20)\d\d$")
_MAJORITY = re.compile(r"(?:^|[.!?:]\s+)most\b|\bmost of\b|\bmajority\b|\bde flesta\b|\bmajoriteten\b|\bmerparten\b", re.I)
_SHARE = re.compile(r"\b(?:nearly|almost|about|around|over|under|roughly) (?:half|all)\b|\bhalf of\b|\bfew\b"
                    r"|\bnästan (?:hälften|alla)\b|\bhälften\b|\bminority\b", re.I)


def _digits(token: str) -> str:
    return re.sub(r"[^\d]", "", token)


def page_numbers(text: str) -> set[str]:
    return {_digits(token) for token in _NUMBER.findall(text or "")}


def page_percentages(text: str) -> list[float]:
    return [float(value.replace(",", ".")) for value in re.findall(r"(\d+(?:[.,]\d+)?)\s?%", text or "")]


def read_page_texts(directory: Path) -> dict[int, str]:
    """What was on the page under each narrated step, as recorded."""
    texts: dict[int, str] = {}
    try:
        for line in (directory / PAGE_TEXT_FILE).read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            texts[int(entry["step"])] = str(entry["text"])
    except (OSError, ValueError, KeyError, TypeError):
        return texts
    return texts


def claim_findings(steps: Sequence[dict], texts: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Findings for numbers a screen does not show and shares it contradicts,
    and the shares a director has to compare with the screen itself."""
    findings: list[dict] = []
    to_check: list[dict] = []
    for step in steps:
        idx = int(step.get("idx", 0))
        line = (step.get("narration_text") or "").strip()
        text = texts.get(idx)
        if not line or text is None or step.get("offscreen") or not step.get("ok", True):
            continue
        shown = page_numbers(text)
        missing = [token.strip() for token in _NUMBER.findall(line)
                   if not _YEAR.match(_digits(token)) and _digits(token) not in shown]
        if missing:
            findings.append({
                "category": "claim_not_on_screen", "severity": "medium", "location": f"step[{idx}]",
                "measured": {"numbers": missing},
                "message": f"Step {idx}'s line says {', '.join(missing)}, and the screen under it does not.",
                "fix": "Show the source of the number in this beat, or change the line with update_step_narration.",
            })
        percentages = page_percentages(text)
        if _MAJORITY.search(line) and percentages and max(percentages) <= 50:
            findings.append({
                "category": "claim_contradicted", "severity": "high", "location": f"step[{idx}]",
                "measured": {"on_screen": percentages[:8]},
                "message": f'Step {idx}\'s line says most ("{line[:70]}"), and the largest share on the screen is {max(percentages):g}%.',
                "fix": "Say what the screen says, for example \"nearly half\", with update_step_narration.",
            })
        elif _MAJORITY.search(line) or _SHARE.search(line):
            to_check.append({"step": idx, "line": line, "on_screen_percentages": percentages[:8]})
    return findings, to_check


# Contact sheet -----------------------------------------------------------

CONTACT_SHEET_COLUMNS = 6
CONTACT_SHEET_MAX_TILES = 96


def contact_sheet_interval(duration: float, every_seconds: float) -> float:
    """The interval actually used: at least 0.5 s, and no more than
    CONTACT_SHEET_MAX_TILES tiles for a long film."""
    interval = max(0.5, float(every_seconds))
    if duration / interval > CONTACT_SHEET_MAX_TILES:
        interval = math.ceil(duration / CONTACT_SHEET_MAX_TILES)
    return interval


# What an image reply is sent at: a vision model scales anything larger down.
SHEET_REPLY_EDGE = 1568


def write_small_sheet(sheet: Path, out: Path) -> Path | None:
    """The contact sheet at the size an MCP image reply can carry."""
    edge = SHEET_REPLY_EDGE
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(sheet),
             "-vf", f"scale='min({edge},iw*{edge}/max(iw,ih))':-2", "-q:v", "5", str(out)],
            check=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out.is_file() else None


def write_contact_sheet(video: Path, out: Path, duration: float, every_seconds: float) -> dict | None:
    """The whole video as one picture: a frame every `every_seconds`, 400 px
    wide, six to a row, each with its time."""
    interval = contact_sheet_interval(duration, every_seconds)
    tiles = max(1, math.floor(duration / interval) + 1)
    rows = math.ceil(tiles / CONTACT_SHEET_COLUMNS)
    stamp = (f"drawtext=fontfile={FONT_PATH}:text='%{{eif\\:t\\:d}}s':fontcolor=white:fontsize=20:"
             "box=1:boxcolor=black@0.65:boxborderw=6:x=8:y=8")
    command = [
        "ffmpeg", "-loglevel", "error", "-y", "-i", str(video),
        "-vf", f"fps=1/{interval:g},scale=400:-2,{stamp},"
               f"tile={CONTACT_SHEET_COLUMNS}x{rows}:padding=4:color=0x0d111a",
        "-frames:v", "1", "-q:v", "4", str(out),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if not out.is_file():
        return None
    return {"every_seconds": interval, "tiles": tiles, "columns": CONTACT_SHEET_COLUMNS, "rows": rows}

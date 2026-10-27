"""What review_session checks beyond the step rows: the planned beats, the
picture under each line, menus left open, and a contact sheet of the film.

"Hermes runs Nordbrygg" (16 takes, 2026-10-24/25) scored 95-100 on takes
that skipped beats, narrated over a page that had not loaded and left a menu
open on screen. A human caught each one from a contact sheet of the video;
these checks catch them from the same evidence.
"""
from __future__ import annotations

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

"""Best-effort retention of finished session media and metadata."""

import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Sequence

from . import store
from .camera import Camera
from .engine import output_root
from .render import GLIDES_FILE, probe_duration, run_media
from .review import PAGE_TEXT_FILE


SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]+$")
DEFAULT_MAX_CLIPS = 50
DEFAULT_KEEP_RAW_CLIPS = 0
# How far past its line a shot may still be on screen.
LINE_TAIL_SECONDS = 2.0


def _env_int(name: str, default: int) -> int:
    return normalize_limit(os.environ.get(name), default)


def normalize_limit(value: object, default: int) -> int:
    """Normalize a configured or explicitly supplied retention limit."""
    try:
        return max(0, int(str(value).strip())) if value is not None else default
    except (AttributeError, TypeError, ValueError):
        return default


def _session_directory(session: dict[str, Any], root: Path) -> Path:
    configured = session.get("output_dir")
    return Path(configured).expanduser() if configured else root / session["id"]


def _safe_session_directory(
    session_id: str,
    configured: Path,
    root: Path,
) -> Path:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"unsafe session id: {session_id}")
    target = configured.resolve()
    if target == root or target.parent != root or target.name != session_id:
        raise ValueError(f"unsafe session directory: {target}")
    return target


def delete_session_storage(session_id: str, force: bool = False) -> dict[str, Any]:
    """Delete one session's media and metadata safely.

    Finished sessions are always deletable. With ``force=True`` a stale
    active session (for example one orphaned by a restart) can be removed
    too; the caller is responsible for tearing down any live runtime first.
    """
    session = store.get_session(session_id)
    if session is None:
        return {"deleted": False, "session_id": session_id, "reason": "not_found"}
    if session.get("status") != "finished" and not force:
        raise ValueError("only finished sessions can be deleted")
    root = output_root().resolve()
    target = _safe_session_directory(
        session_id, _session_directory(session, root), root
    )
    if target.exists():
        shutil.rmtree(target)
    store.delete_session(session_id)
    store.delete_orphan_storyboards()
    return {"deleted": True, "session_id": session_id}


def replaced_takes(steps: Sequence[dict], plan: dict | None) -> tuple[list[int], list[tuple[float, float | None]]]:
    """The steps of takes a later take has replaced, and where they are in
    the recording: (start, end), end None for the recording's end. A beat
    whose last take is not yet in place keeps all of its takes."""
    indexes: set[int] = set()
    for beat in (plan or {}).get("beats", []):
        attempts = beat.get("state", {}).get("attempts") or []
        if len(attempts) < 2 or not attempts[-1].get("ok") or not beat["state"].get("placed"):
            continue
        for attempt in attempts[:-1]:
            if not attempt.get("purged"):
                indexes.update(int(idx) for idx in attempt.get("steps") or [])
    ordered = sorted((step for step in steps if step.get("offset_seconds") is not None),
                     key=lambda step: float(step["offset_seconds"]))
    ranges: list[tuple[float, float | None]] = []
    for position, step in enumerate(ordered):
        if int(step["idx"]) not in indexes:
            continue
        start = float(step.get("started_seconds") or step["offset_seconds"])
        # The shot before keeps playing under the end of its line, past where
        # the next take began: Sluss's flows beat ran 0.5 s into it.
        before = next((earlier for earlier in reversed(ordered[:position])
                       if int(earlier["idx"]) not in indexes and not earlier.get("offscreen")), None)
        if before is not None:
            start = max(start, float(before["offset_seconds"]) + float(before.get("narration_duration") or 0.0) + LINE_TAIL_SECONDS)
        following = ordered[position + 1] if position + 1 < len(ordered) else None
        end = None if following is None else float(following.get("started_seconds") or following["offset_seconds"])
        if end is not None and start >= end:
            continue
        if ranges and ranges[-1][1] is not None and start <= ranges[-1][1] + 0.05:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return sorted(indexes), ranges


def blank_recording(recording: Path, ranges: Sequence[tuple[float, float | None]]) -> None:
    """Paint these stretches of a recording black. Its length does not change,
    so every kept step's place in it stays right."""
    duration = probe_duration(recording)
    spans = [(start, duration + 1.0 if end is None else end) for start, end in ranges]
    enable = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in spans)
    output = recording.with_name("screen.blanked.mp4")
    try:
        run_media(["ffmpeg", "-loglevel", "error", "-y", "-i", str(recording), "-map", "0", "-c:a", "copy",
                   "-vf", f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{enable}'",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", str(output)])
        if abs(probe_duration(output) - duration) > 0.5:
            raise RuntimeError("the blanked recording came out a different length")
        os.replace(output, recording)
    finally:
        output.unlink(missing_ok=True)


def _forget_files(directory: Path, removed: Sequence[dict], kept: Sequence[dict]) -> int:
    """Delete the files only removed steps used; returns how many."""
    used = {str(step.get(field)) for step in kept for field in ("narration_clip", "screenshot_path") if step.get(field)}
    count = 0
    for step in removed:
        for field in ("narration_clip", "screenshot_path"):
            value = step.get(field)
            path = Path(str(value)) if value else None
            if path is None or str(value) in used or path.parent != directory:
                continue
            if path.is_file():
                path.unlink()
                count += 1
    return count


def purge_replaced_takes(session_id: str, steps: Sequence[dict], plan: dict, directory: Path) -> dict[str, Any]:
    """Remove what replaced takes left behind: their picture in the recording,
    their page text, voice clips, screenshots and step rows. The film itself is
    not touched: it no longer shows these takes."""
    indexes, ranges = replaced_takes(steps, plan)
    if not indexes:
        return {"ok": True, "status": "done", "steps_removed": 0, "message": "No replaced take to remove."}
    recording = directory / "screen.mp4"
    if ranges and recording.is_file():
        blank_recording(recording, ranges)
    gone = set(indexes)
    removed = [step for step in steps if int(step["idx"]) in gone]
    kept = [step for step in steps if int(step["idx"]) not in gone]
    files = _forget_files(directory, removed, kept)
    texts = directory / PAGE_TEXT_FILE
    if texts.is_file():
        lines = [line for line in texts.read_text(encoding="utf-8").splitlines() if line.strip()]
        texts.write_text("".join(line + "\n" for line in lines if int(json.loads(line)["step"]) not in gone), encoding="utf-8")

    def inside(moment: float) -> bool:
        return any(start <= moment and (end is None or moment < end) for start, end in ranges)

    camera = Camera.load(directory)
    if camera is not None:
        for key in camera.keys:
            if inside(float(key["t"])):
                key.pop("facts", None)
        camera.save(directory)
    glides_file = directory / GLIDES_FILE
    if glides_file.is_file():
        glides = json.loads(glides_file.read_text())
        for glide in glides:
            if inside(float(glide["offset"])):
                (directory / f"glide-{int(float(glide['offset']) * 1000)}.jpg").unlink(missing_ok=True)
        glides_file.write_text(json.dumps([glide for glide in glides if not inside(float(glide["offset"]))]))
    shutil.rmtree(directory / "review_frames", ignore_errors=True)
    store.scrub_steps(session_id, indexes)
    for beat in plan["beats"]:
        attempts = beat.get("state", {}).get("attempts") or []
        if len(attempts) > 1 and beat["state"].get("placed") and attempts[-1].get("ok"):
            for attempt in attempts[:-1]:
                attempt["purged"] = True
    store.update_storyboard_beats(plan["plan_id"], plan["beats"])
    return {"ok": True, "status": "done", "steps_removed": len(indexes), "seconds_blanked": round(
        sum((probe_duration(recording) if end is None else end) - start for start, end in ranges), 1)
        if recording.is_file() else 0.0, "files_removed": files}


def prune_storage(max_clips: int, keep_raw_clips: int) -> dict[str, Any]:
    """Prune old finished sessions without touching shared storage."""
    max_clips = normalize_limit(max_clips, DEFAULT_MAX_CLIPS)
    keep_raw_clips = normalize_limit(keep_raw_clips, DEFAULT_KEEP_RAW_CLIPS)
    if max_clips > 0:
        keep_raw_clips = min(keep_raw_clips, max_clips)
    else:
        keep_raw_clips = 0
    root = output_root().resolve()
    sessions = store.list_finished_sessions()
    summary: dict[str, Any] = {
        "removed_sessions": [],
        "removed_raw": [],
        "kept": len(sessions) if max_clips == 0 else min(max_clips, len(sessions)),
        "errors": [],
    }
    summary["removed_plans"] = store.delete_orphan_storyboards()
    for rank, session in enumerate(sessions):
        session_id = session["id"]
        directory = _session_directory(session, root)
        try:
            target = _safe_session_directory(session_id, directory, root)
            if max_clips > 0 and rank >= max_clips:
                shutil.rmtree(target)
                store.delete_session(session_id)
                summary["removed_sessions"].append(session_id)
            elif keep_raw_clips > 0 and rank >= keep_raw_clips:
                raw_path = target / "screen.mp4"
                if raw_path.is_file():
                    raw_path.unlink()
                    summary["removed_raw"].append(session_id)
        except Exception as exc:
            summary["errors"].append({
                "id": session_id,
                "message": str(exc),
            })
    return summary


def prune_from_env() -> dict[str, Any]:
    """Prune using REEL_MAX_CLIPS and REEL_KEEP_RAW_CLIPS."""
    max_clips, keep_raw_clips = retention_settings()
    return prune_storage(max_clips, keep_raw_clips)


def retention_settings() -> tuple[int, int]:
    """Return normalized retention limits from the environment."""
    return (
        _env_int("REEL_MAX_CLIPS", DEFAULT_MAX_CLIPS),
        _env_int("REEL_KEEP_RAW_CLIPS", DEFAULT_KEEP_RAW_CLIPS),
    )

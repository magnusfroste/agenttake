"""A pickup: one beat shot again after finish, in a recording of its own, and
joined onto the session's recording so it is an ordinary retake from there on.

Sluss take 6's only remedy for one badly framed beat was a fresh take of all
16, and that take cut another beat mid-word.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .camera import Camera
from .render import GLIDES_FILE, probe_duration, run_media
from .review import PAGE_TEXT_FILE


def join_recordings(first: Path, second: Path) -> float:
    """Put second after first, in first's place. Copies the streams when they
    join cleanly, else encodes. Returns where second starts."""
    start = probe_duration(first)
    expected = start + probe_duration(second)
    joined = first.with_name("screen.joined.mp4")
    listing = first.with_name("join.txt")
    listing.write_text(f"file '{first.resolve()}'\nfile '{second.resolve()}'\n")
    base = ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    try:
        run_media([*base, "-c", "copy", str(joined)])
        clean = abs(probe_duration(joined) - expected) < 1.0
    except (OSError, subprocess.SubprocessError, ValueError):
        clean = False
    if not clean:
        run_media([*base, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                   "-pix_fmt", "yuv420p", str(joined)])
    os.replace(joined, first)
    listing.unlink(missing_ok=True)
    return start


def take_in(directory: Path, pickup: Path, start: float, camera: Any) -> None:
    """Bring a pickup's camera moves, glides, voice clips and page texts into
    the session, on the session's clock."""
    if camera is not None:
        own = Camera.load(directory) or Camera(camera.width, camera.height, camera.scale)
        # The pickup opens wide, whatever the film's last shot was.
        own.keys.append({"t": round(start, 3), "zoom": 1.0, "cx": own.width / 2, "cy": own.height / 2, "ease": 0.0})
        own.keys.extend({**key, "t": round(float(key["t"]) + start, 3)} for key in camera.keys)
        own.save(directory)
    glides = pickup / GLIDES_FILE
    if glides.is_file():
        record = directory / GLIDES_FILE
        kept = json.loads(record.read_text()) if record.is_file() else []
        kept += [{**glide, "offset": round(float(glide["offset"]) + start, 3)} for glide in json.loads(glides.read_text())]
        record.write_text(json.dumps(kept))
    for path in [*pickup.glob("narration-*"), *pickup.glob("glide-*")]:
        shutil.move(str(path), directory / path.name)
    texts = pickup / PAGE_TEXT_FILE
    if texts.is_file():
        with (directory / PAGE_TEXT_FILE).open("a", encoding="utf-8") as out:
            out.write(texts.read_text(encoding="utf-8"))

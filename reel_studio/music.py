"""Music under a film: tracks from a library of free ones, chosen by mood.

A film had no music, or ``music: "subtle"``, which was filtered pink noise: a
hiss, not music (Magnus, 2026-11-25: "det vore ju fantastisk om agenten även
kunde lägga på lite pausmusik"). Generating music costs a provider and a
quota; cutting finished tracks costs nothing and sounds the same every time.

The library is reel_studio/data/music/library.json and the files beside it,
each one CC0 (no credit needed, no licence to track): a person listened to
every one before it went in. A director chooses a mood ("calm"), or a
track's id for one track. The track is laid under the whole film: it fades
in, sits a little under the voice where nobody speaks (pausmusik), goes well
down under every line and under the page's own sound, as a presenter takes
music down for anyone who speaks, and fades out with the film. A track
shorter than the film plays again: a loop is joined where it was made to
join, any other track with a crossfade.

REEL_MUSIC_LIBRARY names another library.json (a test's, an instance's own).
"""
from __future__ import annotations

from dataclasses import dataclass
import functools
import json
import math
import os
from pathlib import Path
from typing import Sequence

from . import sound as page_sound

LIBRARY_ENV = "REEL_MUSIC_LIBRARY"
DEFAULT_LIBRARY = Path(__file__).parent / "data" / "music" / "library.json"
# What a film without music says, and what the old "subtle" (a noise bed)
# now means: the library's first calm track.
NONE = "none"
LEGACY_SUBTLE = "subtle"
LEGACY_MOOD = "calm"
# Where nobody speaks, the music sits this far under the voice (LU): heard,
# a bed and not a song. Under a line it goes DUCK_DB further down, about 22
# under the voice: the research report "Ljud upp och under rösten" puts
# music under a voice at 18-24 dB down.
BELOW_VOICE_LU = 8.0
DUCK_DB = 14.0
# Music moves slower than the page's sound: down over LEAD before a line,
# up over RELEASE from AFTER past it, and kept down over a gap under HOLD.
LEAD = 0.5
AFTER = 0.2
RELEASE = 1.0
HOLD = 2.0
FADE_IN = 1.0
FADE_OUT = 3.0
# A track that is not a loop joins itself with this long a crossfade.
CROSSFADE = 3.0
# The film's sound with music: music has more above 12 kHz than a voice.
SAMPLE_RATE = 48000


@dataclass(frozen=True)
class Track:
    id: str
    mood: str
    path: Path
    title: str
    artist: str
    source: str
    licence: str
    loop: bool = False
    about: str = ""

    def public(self) -> dict:
        return {"id": self.id, "mood": self.mood, "title": self.title, "artist": self.artist,
                "about": self.about, "source": self.source, "licence": self.licence, "loop": self.loop,
                "seconds": round(duration(self.path), 1)}


def library_path() -> Path:
    return Path(os.environ.get(LIBRARY_ENV) or DEFAULT_LIBRARY)


def tracks() -> list[Track]:
    """The library's tracks, in its order; [] without a library."""
    return list(_load(str(library_path())))


@functools.lru_cache(maxsize=4)
def _load(path: str) -> tuple[Track, ...]:
    file = Path(path)
    try:
        entries = json.loads(file.read_text()).get("tracks", [])
    except (OSError, ValueError):
        return ()
    found = []
    for entry in entries:
        track_path = file.parent / str(entry.get("file", ""))
        if not entry.get("id") or not track_path.is_file():
            continue
        found.append(Track(
            id=str(entry["id"]).lower(), mood=str(entry.get("mood", "")).lower(), path=track_path,
            title=str(entry.get("title", "")), artist=str(entry.get("artist", "")),
            source=str(entry.get("source", "")), licence=str(entry.get("licence", "")),
            loop=bool(entry.get("loop", False)), about=str(entry.get("about", "")),
        ))
    return tuple(found)


def moods() -> list[str]:
    seen: list[str] = []
    for track in tracks():
        if track.mood and track.mood not in seen:
            seen.append(track.mood)
    return seen


def choose(choice: str | None) -> Track | None:
    """The track ``choice`` names: a track's id, a mood (its first track),
    "subtle" (the first calm one, or the first of all); None for "none",
    an empty choice or one the library does not have."""
    wanted = (choice or "").strip().lower()
    if not wanted or wanted == NONE:
        return None
    library = tracks()
    for track in library:
        if track.id == wanted:
            return track
    if wanted == LEGACY_SUBTLE:
        wanted = LEGACY_MOOD
        fallback = library[0] if library else None
    else:
        fallback = None
    for track in library:
        if track.mood == wanted:
            return track
    return fallback


def normalize(choice: str | None) -> str:
    """What a session keeps: the choice as given when the library has it,
    else "none"."""
    wanted = (choice or "").strip().lower()
    return wanted if choose(wanted) is not None else NONE


@functools.lru_cache(maxsize=64)
def duration(path: Path) -> float:
    from .render import probe_duration
    try:
        return probe_duration(path)
    except Exception:
        return 0.0


@functools.lru_cache(maxsize=64)
def track_loudness(path: Path) -> float | None:
    return page_sound.loudness(path, mono=True)


def gain(track: Track, voice: float) -> float:
    """The gain, in dB, that sets the track BELOW_VOICE_LU under ``voice``."""
    heard = track_loudness(track.path)
    if heard is None:
        return 0.0
    return voice - BELOW_VOICE_LU - heard


def bed(track: Track, film_seconds: float, voice: float, spans: Sequence[tuple[float, float]],
        first_input: int) -> tuple[list[str], list[str]]:
    """The music as one track [music]: the inputs to add (ffmpeg arguments)
    and the filters. ``spans`` are where someone speaks, the narrator or the
    page, on the film's clock: the music goes down under them."""
    length = duration(track.path)
    if length <= 0 or film_seconds <= 0:
        return [], []
    arguments: list[str] = []
    filters: list[str] = []
    if track.loop or length >= film_seconds:
        arguments += (["-stream_loop", "-1"] if length < film_seconds else []) + ["-i", str(track.path)]
        source = f"[{first_input}:a]"
    else:
        # Each copy overlaps the last by CROSSFADE.
        crossfade = min(CROSSFADE, length / 3)
        copies = 1 + math.ceil((film_seconds - length) / (length - crossfade))
        for _ in range(copies):
            arguments += ["-i", str(track.path)]
        source = f"[{first_input}:a]"
        for number in range(1, copies):
            joined = f"[mj{number}]"
            filters.append(f"{source}[{first_input + number}:a]acrossfade=d={crossfade:.3f}{joined}")
            source = joined
    ducked = page_sound.duck(spans, depth=DUCK_DB, lead=LEAD, after=AFTER, release=RELEASE, hold=HOLD)
    fade_out = min(FADE_OUT, film_seconds / 3)
    filters.append(
        f"{source}aformat=channel_layouts=stereo,{page_sound.MONO},aresample={SAMPLE_RATE},"
        f"atrim=0:{film_seconds:.3f},asetpts=N/SR/TB,volume={gain(track, voice):.2f}dB,"
        f"{ducked + ',' if ducked else ''}"
        f"afade=t=in:d={min(FADE_IN, film_seconds / 3):.3f},"
        f"afade=t=out:st={film_seconds - fade_out:.3f}:d={fade_out:.3f}[music]"
    )
    return filters, arguments

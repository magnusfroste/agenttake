"""ffmpeg and X11 recording helpers."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from typing import TYPE_CHECKING, Callable, Sequence, cast
import textwrap

if TYPE_CHECKING:
    from .camera import Camera


FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
CARD_DURATION = 3.0


@dataclass(frozen=True)
class RenderConfig:
    title: str = ""
    subtitle: str = ""
    accent: str = "#1f2a44"
    cta_url: str = ""
    cta_text: str = "Learn more"
    music: str = "none"
    # "smooth": cut on page changes, dissolve where a cut would jump, fade the
    # cards. "cuts": every join a hard cut. See choose_transitions.
    transitions: str = "smooth"
    # The title and closing cards' background: "auto" — a still from the
    # video itself, blurred and dimmed under the text, like a hero section —
    # "solid" (the accent colour), or an https image URL.
    title_background: str = "auto"
    # A QR code of cta_url on the closing card: on LinkedIn the link in a
    # video cannot be clicked, but a phone can scan it off a laptop screen.
    closing_qr: bool = True


def start_recording(display: str, width: int, height: int, output: Path) -> subprocess.Popen[bytes]:
    output.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error", "-nostats", "-y", "-f", "x11grab",
            "-video_size", f"{width}x{height}",
            "-framerate", "25", "-i", f"{display}.0", "-draw_mouse", "1",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-threads", "0", "-pix_fmt", "yuv420p", str(output),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_recording(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=4)
    finally:
        if process.stdin:
            process.stdin.close()


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def mux_narration(
    video_path: Path,
    clips: Sequence[tuple[float, Path]],
    output_path: Path,
    output_size: tuple[int, int] | None = None,
    config: RenderConfig | None = None,
    camera: "Camera | None" = None,
) -> Path:
    """Create a delayed mixed narration track and mux it into the video."""
    config = config or RenderConfig()
    if camera is not None:
        output_size = camera.output_size(output_size)
    if not clips and not _has_branding(config) and config.music == "none":
        if output_size is None:
            video_path.replace(output_path)
        else:
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y", "-i", str(video_path),
                    "-vf", f"scale={output_size[0]}:{output_size[1]}",
                    "-an", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", str(output_path),
                ],
                check=True,
            )
        return output_path

    video_duration = probe_duration(video_path)
    with tempfile.TemporaryDirectory(
        prefix=".continuous-", dir=output_path.parent
    ) as temporary:
        body_path = Path(temporary) / "body.mp4"
        _render_video_only(video_path, body_path, output_size)
        _compose_video(
            body_path,
            clips,
            output_path,
            video_duration,
            output_size,
            config,
        )
    return output_path


def _render_video_only(
    video_path: Path,
    output_path: Path,
    output_size: tuple[int, int] | None,
) -> None:
    command = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video_path)]
    if output_size is not None:
        command.extend([
            "-vf", f"scale={output_size[0]}:{output_size[1]}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        ])
    else:
        command.extend(["-c:v", "copy"])
    command.extend(["-an", str(output_path)])
    subprocess.run(command, check=True)


def _has_branding(config: RenderConfig) -> bool:
    return bool(config.title.strip() or config.cta_url.strip())


def _escape_drawtext(text: str) -> str:
    """Text for drawtext's text='...' inside a -vf chain, which unquotes and
    unescapes it twice (the filtergraph, then the filter's options) before
    drawtext expands %. A quote closes the quoting and is escaped for both
    levels: as \\' an apostrophe ("you're not alone") broke the whole chain.
    """
    return (
        text.replace("\\", "\\\\\\\\")
        .replace("'", "'\\\\\\''")
        .replace("%", "\\\\%")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )


def wrap_card_text(text: str, max_chars: int = 28) -> list[str]:
    """Wrap title-card text at word boundaries for safe video margins."""
    text = text.strip()
    if not text:
        return []
    return textwrap.wrap(
        text, width=max_chars, break_long_words=False, break_on_hyphens=False,
    ) or [text]


CARD_DIM = 0.4
CARD_URL_MAX_BYTES = 10 * 1024 * 1024


def _still(video: Path, at_end: bool, output: Path) -> Path | None:
    """One full-size frame from the start or the end of a video."""
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y",
             *(["-sseof", "-0.12"] if at_end else ["-ss", "0.3"]),
             "-i", str(video), "-frames:v", "1", str(output)],
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    return output if output.is_file() and output.stat().st_size else None


def _public_https(url: str) -> bool:
    """An https URL whose host resolves only to public addresses.

    reel-studio runs beside other services on its host's network; fetching
    whatever URL an agent names would let it reach those.
    """
    import ipaddress
    import socket
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443)
    except OSError:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            return False
    return True


def _download_image(url: str, output: Path) -> Path | None:
    import urllib.request

    if not _public_https(url):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 agenttake"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if not (response.headers.get("Content-Type") or "").startswith("image/"):
                return None
            data = response.read(CARD_URL_MAX_BYTES + 1)
    except Exception:
        return None
    if not data or len(data) > CARD_URL_MAX_BYTES:
        return None
    output.write_bytes(data)
    return output


def card_backgrounds(
    config: RenderConfig, first_video: Path, last_video: Path, temporary: Path,
) -> tuple[Path | None, Path | None]:
    """The images under the title card and the closing card, if any.

    "auto" takes the first frame of the video for the title and its last
    frame for the close — the product itself, as a hero image. An image URL
    is used for both; one that cannot be fetched falls back to "auto".
    """
    choice = (config.title_background or "auto").strip()
    if choice == "solid":
        return None, None
    if choice.startswith("https://"):
        image = _download_image(choice, temporary / "card-background")
        if image is not None:
            return image, image
    return (
        _still(first_video, False, temporary / "card-first.png"),
        _still(last_video, True, temporary / "card-last.png"),
    )


def _card(
    output_path: Path,
    width: int,
    height: int,
    accent: str,
    lines: Sequence[tuple[str, int]],
    background: Path | None = None,
    duration: float = CARD_DURATION,
    text_colour: str = "white",
) -> None:
    if not Path(FONT_PATH).is_file():
        raise RuntimeError(f"Title-card font is missing: {FONT_PATH}")
    drawtext = []
    expanded_lines: list[tuple[str, int]] = []
    for text, size in lines:
        wrapped = wrap_card_text(text, max_chars=28 if size >= 36 else 54)
        expanded_lines.extend((line, size) for line in wrapped)
    center_offset = (len(expanded_lines) - 1) * 0.7
    for index, (text, size) in enumerate(expanded_lines):
        drawtext.append(
            "drawtext="
            f"fontfile={FONT_PATH}:text='{_escape_drawtext(text)}':"
            f"fontcolor={text_colour if background is None else 'white'}:fontsize={size}:"
            + ("shadowcolor=black@0.55:shadowx=2:shadowy=2:" if background else "")
            + f"x=(w-text_w)/2:y=(h-text_h)/2+{index * 1.4 - center_offset}*{size}"
        )
    if background is not None:
        # A hero section: the image fills the frame, drifts in a little over
        # the card, and is blurred and dimmed just enough for the text to read
        # on any image, with a soft shadow under the letters.
        # A strip of the accent colour at the bottom keeps the brand.
        frames = int(round(duration * 25))
        hero = [
            # The bottom band is where captions sit in a still from the video;
            # it is cut off so a caption does not ghost through the title.
            "crop=iw:ih*0.84:0:0",
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
            f"zoompan=z='1+0.05*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
            f":d={frames}:s={width}x{height}:fps=25",
            f"boxblur={max(2, width // 320)}:2",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{CARD_DIM}:t=fill",
            f"drawbox=x=0:y=ih-{max(4, height // 135)}:w=iw:h={max(4, height // 135)}:color={accent}@0.9:t=fill",
        ]
        result = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-i", str(background),
                "-vf", ",".join(hero + drawtext + ["format=yuv420p"]),
                "-frames:v", str(frames), "-r", "25",
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", str(output_path),
            ],
        )
        if result.returncode == 0 and output_path.is_file():
            return
        # An image ffmpeg cannot read: the solid card below.
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c={accent}:s={width}x{height}:r=25:d={duration:.3f}",
            "-vf", ",".join(drawtext),
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", str(output_path),
        ],
        check=True,
    )


def make_qr(url: str, output: Path) -> Path | None:
    """A QR code of url as a PNG, dark on white with a quiet zone; None if
    the library is missing or the URL will not encode."""
    try:
        import segno
    except ImportError:
        return None
    try:
        segno.make(url, error="m").save(str(output), scale=12, border=2, dark="#0d111a", light="#ffffff")
    except Exception:
        return None
    return output if output.is_file() else None


def _overlay_qr(card: Path, qr: Path, width: int, height: int) -> None:
    """Put the QR code in the closing card's bottom-right corner."""
    side = max(96, int(height * 0.22)) // 2 * 2
    margin = max(24, int(height * 0.06))
    out = card.with_name(card.stem + "-qr.mp4")
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(card), "-i", str(qr),
         "-filter_complex", f"[1:v]scale={side}:{side}:flags=neighbor[q];[0:v][q]overlay=W-w-{margin}:H-h-{margin}",
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "25", str(out)],
    )
    if result.returncode == 0 and out.is_file():
        out.replace(card)


def _closing_card(outro: Path, width: int, height: int, config: RenderConfig, background: Path | None, temporary: Path) -> None:
    _card(
        outro, width, height, config.accent,
        [
            (config.cta_text.strip() or "Learn more", max(28, width // 32)),
            (config.cta_url.strip(), max(22, width // 44)),
        ],
        background,
    )
    if config.closing_qr:
        qr = make_qr(config.cta_url.strip(), temporary / "closing-qr.png")
        if qr is not None:
            _overlay_qr(outro, qr, width, height)


def _compose_video(
    body_path: Path,
    clips: Sequence[tuple[float, Path]],
    output_path: Path,
    body_duration: float,
    output_size: tuple[int, int] | None,
    config: RenderConfig,
) -> None:
    width, height = output_size or probe_video_size(body_path)
    temporary = body_path.parent
    parts: list[Path] = []
    intro_duration = 0.0
    intro_background, outro_background = (
        card_backgrounds(config, body_path, body_path, temporary)
        if (config.title.strip() or config.cta_url.strip()) else (None, None)
    )
    if config.title.strip():
        intro = temporary / "intro.mp4"
        _card(
            intro, width, height, config.accent,
            [(config.title.strip(), max(36, width // 22)),
             (config.subtitle.strip(), max(20, width // 48))]
            if config.subtitle.strip()
            else [(config.title.strip(), max(36, width // 22))],
            intro_background,
        )
        parts.append(intro)
        intro_duration = CARD_DURATION
    parts.append(body_path)
    if config.cta_url.strip():
        outro = temporary / "outro.mp4"
        _closing_card(outro, width, height, config, outro_background, temporary)
        parts.append(outro)
    base = temporary / "branded-base.mp4"
    concat = temporary / "branded.txt"
    concat.write_text("".join(f"file '{path}'\n" for path in parts))
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y", "-f", "concat",
            "-safe", "0", "-i", str(concat), "-c", "copy", str(base),
        ],
        check=True,
    )
    shifted = [
        (offset + intro_duration, clip)
        for offset, clip in clips
    ]
    total_duration = intro_duration + body_duration
    if config.cta_url.strip():
        total_duration += CARD_DURATION
    _mux_segment_audio(
        base, shifted, output_path, total_duration, config.music
    )


def probe_video_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)


def _mux_segment_audio(
    video_path: Path,
    clips: Sequence[tuple[float, Path]],
    output_path: Path,
    duration: float,
    music: str = "none",
) -> None:
    command = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video_path)]
    filters: list[str] = []
    for index, (offset, clip) in enumerate(clips, start=1):
        command.extend(["-i", str(clip)])
        delay = max(0, round(offset * 1000))
        filters.append(f"[{index}:a]adelay={delay}|{delay}[a{index}]")
    input_count = len(clips)
    if music == "subtle":
        command.extend([
            "-f", "lavfi", "-i",
            (
                "anoisesrc=color=pink:amplitude=0.015:"
                f"sample_rate=48000:d={duration:.3f}"
            ),
        ])
        input_count += 1
        music_index = input_count
        filters.append(
            f"[{music_index}:a]highpass=f=120,lowpass=f=1800,"
            "volume=0.4[bed]"
        )
    labels = "".join(f"[a{i}]" for i in range(1, len(clips) + 1))
    if labels:
        if music == "subtle":
            labels += "[bed]"
        filters.append(
            f"{labels}amix=inputs={input_count}:duration=longest:"
            "dropout_transition=0:normalize=0[a]"
        )
    elif music == "subtle":
        filters.append("[bed]anull[a]")
    if filters:
        command.extend(["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[a]"])
    else:
        command.extend([
            "-f", "lavfi", "-i",
            "anullsrc=channel_layout=mono:sample_rate=24000",
            "-map", "0:v", "-map", "1:a",
        ])
    command.extend([
        "-t", f"{duration:.3f}", "-c:v", "copy", "-c:a", "aac",
        str(output_path),
    ])
    subprocess.run(command, check=True)


SEGMENT_FLOOR = 1.0
# A step that is silent on purpose (quiet) is part of the beat before it: a
# click, a page turning. A second and a half of it per step made sign-in and
# navigation the slowest part of a video.
QUIET_FLOOR = 0.6
SEGMENT_TAIL_PAD = 0.4
LEAD_IN_CAP = 1.0


@dataclass(frozen=True)
class SegmentedRenderResult:
    path: Path
    duration: float
    warnings: list[dict]
    # Where each part sits in the finished video: {"kind": "intro" | "lead" |
    # "step" | "outro", "step": index into the steps rendered, "start",
    # "duration"} — the storyboard's timings and frames come from it.
    timeline: list[dict] = field(default_factory=list)


def segmented_render_enabled() -> bool:
    return os.environ.get("REEL_SEGMENTED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


@dataclass(frozen=True)
class Segment:
    name: str
    offset: float
    source_duration: float
    output_duration: float
    clip: Path | None = None
    # Position of the step in the steps given to plan_segments; None for the
    # lead-in.
    step_index: int | None = None
    # A time-lapse: the source plays this many times faster.
    speed: float = 1.0
    # Stretches of the source, in seconds from its start, left out: where a
    # glide's screen stood still because the browser stalled.
    drop: tuple[tuple[float, float], ...] = ()


# Footage a time-lapse keeps after the screen stops changing, so the result
# it waited for is seen before the cut.
LAPSE_TAIL = 2.0
# What counts as a still screen: freezedetect's noise floor and the shortest
# stillness that ends the action. A blinking caret or a small spinner stays
# below the floor.
FREEZE_NOISE = 0.003
FREEZE_MIN_SECONDS = 1.5


def active_seconds(video_path: Path, start: float, end: float) -> float:
    """Seconds from start until the screen stops changing for good.

    The last still stretch that runs to the end of the window ends the
    action; stillness in the middle (a pause before the next answer streams
    in) does not. The whole window when nothing settles, or ffmpeg fails.
    """
    length = max(0.0, end - start)
    if length <= FREEZE_MIN_SECONDS:
        return length
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", str(video_path), "-an",
         "-vf", f"fps=5,scale=480:-2,freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN_SECONDS}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return length
    starts = [float(m) for m in re.findall(r"freeze_start: ([0-9.]+)", result.stderr)]
    ends = [float(m) for m in re.findall(r"freeze_end: ([0-9.]+)", result.stderr)]
    # A freeze still running when the window ends has a start and no end.
    if starts and len(ends) < len(starts):
        return min(length, starts[-1])
    return length


# Where a session keeps its document glides: for each, the step's offset, a
# full-page image and how far down it the glide went (BrowserSession.
# _capture_glide). The render pans over the image instead of the footage.
GLIDES_FILE = "glides.json"
# The pan holds on its last frame a moment, like a glide's tail.
PAN_TAIL = 0.4


def load_glides(directory: Path) -> dict[float, dict]:
    """The document glides recorded in directory, keyed by step offset."""
    record = directory / GLIDES_FILE
    try:
        glides = json.loads(record.read_text()) if record.is_file() else []
    except (OSError, ValueError):
        return {}
    return {
        round(float(glide["offset"]), 3): glide for glide in glides
        if (directory / str(glide.get("image", ""))).is_file()
    }


def _render_glide_pan(
    image: Path,
    glide: dict,
    output_duration: float,
    output_path: Path,
    output_size: tuple[int, int] | None,
) -> Path:
    """The glide as a pan down the full-page image: the viewport's window
    moves from where the glide started by its distance, eased in and out
    over its ms, then holds."""
    # The image is at the capture density; the glide was measured in CSS px.
    scale = float(glide.get("scale") or 1.0)
    view = round(float(glide["view_height"]) * scale / 2) * 2
    top = float(glide["start"]) * scale
    distance = float(glide["distance"]) * scale
    seconds = max(0.1, float(glide["ms"]) / 1000)
    x = f"min(t/{seconds:.3f},1)"
    ease = f"if(lt({x},0.5),2*{x}*{x},1-pow(-2*{x}+2,2)/2)"
    filters = [f"crop=iw:{view}:0:'{top:.1f}+{distance:.1f}*{ease}'"]
    if output_size is not None:
        filters.append(f"scale={output_size[0]}:{output_size[1]}")
    filters.append("format=yuv420p")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "25",
         "-t", f"{output_duration:.3f}", "-i", str(image), "-vf", ",".join(filters),
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-r", "25", str(output_path)],
        check=True,
    )
    return output_path


# Where a session keeps its mid-film cards (the card action): for each, the
# step's offset, its text and its background. The render cuts a generated
# card in where the step was recorded, instead of the footage, so a rerender
# keeps it.
CARDS_FILE = "cards.json"
# A card holds its line plus this, and never less than CARD_MIN_SECONDS: with
# a dissolve out of it the line has ended before the next part's begins.
CARD_TAIL = 0.8
CARD_MIN_SECONDS = 2.5
CARD_TEXT_MAX = 140
HEX_COLOUR = re.compile(r"#?[0-9a-fA-F]{6}")


def card_background(value: str | None) -> str | None:
    """A card's background as stored: "brand", "blur" or "#rrggbb"; None when
    it is none of those."""
    value = (value or "brand").strip()
    if value.lower() in {"brand", "blur"}:
        return value.lower()
    if HEX_COLOUR.fullmatch(value):
        return "#" + value.lstrip("#").lower()
    return None


def card_seconds(hold: float) -> float:
    """How long a card plays: its line (or its hold) plus a tail, at least
    CARD_MIN_SECONDS."""
    return max(CARD_MIN_SECONDS, hold + CARD_TAIL if hold else 0.0)


def load_cards(directory: Path) -> dict[float, dict]:
    """The mid-film cards recorded in directory, keyed by step offset."""
    record = directory / CARDS_FILE
    try:
        cards = json.loads(record.read_text()) if record.is_file() else []
    except (OSError, ValueError):
        return {}
    return {round(float(card["offset"]), 3): card for card in cards if str(card.get("text") or "").strip()}


def record_card(directory: Path, offset: float, text: str, background: str) -> None:
    record = directory / CARDS_FILE
    try:
        cards = json.loads(record.read_text()) if record.is_file() else []
    except (OSError, ValueError):
        cards = []
    cards.append({"offset": round(offset, 3), "text": text, "background": background})
    record.write_text(json.dumps(cards))


def _light(colour: str) -> bool:
    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue > 150


def render_card_segment(
    card: dict,
    duration: float,
    output_path: Path,
    size: tuple[int, int],
    accent: str,
    still: Path | None = None,
) -> Path:
    """A mid-film card, as a segment of the film: its text over the accent
    colour ("brand"), over a blurred still of what came before ("blur"), or
    over a flat colour. Built like the title card; dark text on a light
    colour, so it reads."""
    width, height = size
    background = card_background(card.get("background")) or "brand"
    colour = accent if background in {"brand", "blur"} else background
    lines = [(line, max(36, width // 26)) for line in str(card["text"]).split("\n") if line.strip()]
    _card(
        output_path, width, height, colour, lines,
        still if background == "blur" else None, duration,
        "#111111" if _light(colour) else "white",
    )
    return output_path


# A glide's screen never stands still on purpose: a still stretch this long
# inside one is the browser stalling (a busy two-core host, 2026-10-24: 4.4 s
# with no frame painted in the middle of a page glide), and it is cut.
GLIDE_STALL_SECONDS = 0.3


def frozen_spans(video_path: Path, start: float, end: float) -> list[tuple[float, float]]:
    """Still stretches of at least GLIDE_STALL_SECONDS inside [start, end],
    in seconds from start. One still at the end of the window is not a stall
    — the glide is done — and is not returned. Empty when ffmpeg fails."""
    length = max(0.0, end - start)
    if length <= GLIDE_STALL_SECONDS:
        return []
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", str(video_path), "-an",
         "-vf", f"scale=480:-2,freezedetect=n={FREEZE_NOISE}:d={GLIDE_STALL_SECONDS}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    starts = [float(m) for m in re.findall(r"freeze_start: ([0-9.]+)", result.stderr)]
    ends = [float(m) for m in re.findall(r"freeze_end: ([0-9.]+)", result.stderr)]
    return [(a, b) for a, b in zip(starts, ends) if b < length - 0.05]


def plan_segments(
    steps: Sequence[tuple[float, Path | None, float]],
    video_duration: float,
    floors: Sequence[float] | None = None,
    lead_in: bool = True,
    speeds: Sequence[float] | None = None,
    activity: Callable[[float, float], float] | None = None,
    glides: Sequence[float | None] | None = None,
    stalls: Callable[[float, float], list[tuple[float, float]]] | None = None,
    order: Sequence[float] | None = None,
    cards: Sequence[bool] | None = None,
) -> tuple[list[Segment], list[dict]]:
    """The windows of the recording a segmented render keeps, in order.

    Each step keeps its narration (at least SEGMENT_FLOOR) plus a short tail,
    and never more footage than there is before the next step; a step whose
    narration outlasts its footage holds its last frame. The time between
    steps, when an agent was thinking, is cut. Shared by the renderer and by
    the length estimate a director sees while recording.

    ``floors`` gives each step its own minimum (QUIET_FLOOR for a quiet
    step), in the order of ``steps``. ``lead_in`` False drops the second of
    footage before the first step — the video then opens on the first step,
    not on whatever was on screen while it was set up offscreen.

    ``activity(start, end)`` says how many seconds of a window the screen
    keeps changing; a time-lapse keeps only that much (plus LAPSE_TAIL).
    Without it a time-lapse ran to the next step — including every minute the
    director spent thinking — and a 12-second wait for two chat answers came
    out as 95 s of a still screen at 4× (2026-10-10).

    ``glides`` gives each step its narration's length when it is a glide (a
    scroll with ms), else None. A glide keeps its whole move, and with
    ``stalls(start, end)`` the stretches where the screen stood still are
    left out, so a browser that stalled mid-glide does not show as a frozen
    page; its line still plays in full, over a held last frame if need be.

    ``order`` gives each step its place in the video, in the order of
    ``steps``: a beat recorded at the end of a session plays where it
    belongs (edit_step position). Each step's footage is still the window
    after it in the recording, so its camera keys and its glide go with it;
    only the order the windows are played in changes. The lead-in stays
    first.

    ``cards`` marks the steps that are mid-film cards: generated, not cut
    from the recording, so a card lasts card_seconds of its line whatever
    footage follows it.
    """
    ordered = sorted(
        (offset, index, clip, max(0.0, duration))
        for index, (offset, clip, duration) in enumerate(steps)
        if 0 <= offset < video_duration
    )
    segments: list[Segment] = []
    warnings: list[dict] = []
    if lead_in and ordered and ordered[0][0] > 0:
        lead = min(LEAD_IN_CAP, ordered[0][0])
        segments.append(Segment("lead", 0.0, lead, lead))
    for index, (offset, step_index, clip, narration_duration) in enumerate(ordered):
        next_offset = (
            ordered[index + 1][0] if index + 1 < len(ordered) else video_duration
        )
        available = max(0.0, next_offset - offset)
        if available <= 0:
            continue
        floor = (
            floors[step_index]
            if floors is not None and step_index < len(floors) else SEGMENT_FLOOR
        )
        speed = speeds[step_index] if speeds is not None and step_index < len(speeds) else 1.0
        glide_line = glides[step_index] if glides is not None and step_index < len(glides) else None
        if cards is not None and step_index < len(cards) and cards[step_index]:
            keep = card_seconds(narration_duration)
            segments.append(Segment(f"{index:04d}", offset, min(available, keep), keep, clip, step_index))
            continue
        if glide_line is not None:
            # narration_duration is the step's hold: the glide's whole length.
            window = min(available, narration_duration + SEGMENT_TAIL_PAD)
            drop = tuple(stalls(offset, offset + window)) if stalls is not None else ()
            played = window - sum(end - start for start, end in drop)
            keep = max(played, glide_line + SEGMENT_TAIL_PAD if glide_line else 0.0, QUIET_FLOOR)
            segments.append(Segment(f"{index:04d}", offset, window, keep, clip, step_index, drop=drop))
            continue
        if speed and speed > 1.0:
            # A time-lapse keeps all of the step's footage, sped up; it lasts
            # at least as long as its line.
            if activity is not None:
                active = activity(offset, offset + available)
                available = min(available, max(active + LAPSE_TAIL, 1.0))
            played = available / speed
            keep = max(played, narration_duration + SEGMENT_TAIL_PAD if narration_duration else 0.0, QUIET_FLOOR)
            segments.append(Segment(f"{index:04d}", offset, available, keep, clip, step_index, speed))
            continue
        target = (max(narration_duration, floor) + SEGMENT_TAIL_PAD) if narration_duration else (
            floor + (SEGMENT_TAIL_PAD if floor >= SEGMENT_FLOOR else 0.0)
        )
        keep_duration = min(available, target)
        if narration_duration > available:
            warnings.append({
                "index": index,
                "needed_seconds": round(narration_duration, 3),
                "available_seconds": round(available, 3),
            })
            keep_duration = narration_duration + SEGMENT_TAIL_PAD
        segments.append(Segment(
            f"{index:04d}", offset, min(available, keep_duration), keep_duration, clip,
            step_index,
        ))
    if order is not None:
        def place(segment: Segment) -> float:
            if segment.step_index is None:
                return float("-inf")
            return order[segment.step_index] if segment.step_index < len(order) else float("inf")

        segments.sort(key=place)
    return segments, warnings


def segmented_render(
    video_path: Path,
    steps: Sequence[tuple[float, Path | None, float]],
    output_path: Path,
    output_size: tuple[int, int] | None = None,
    config: RenderConfig | None = None,
    camera: "Camera | None" = None,
    pages: Sequence[str] | None = None,
    floors: Sequence[float] | None = None,
    lead_in: bool = True,
    speeds: Sequence[float] | None = None,
    glides: Sequence[float | None] | None = None,
    order: Sequence[float] | None = None,
) -> SegmentedRenderResult:
    """Render kept step windows from the original continuous recording.

    ``pages`` is the URL each step ended on, in the order of ``steps``; with
    transitions "smooth" it tells a page change (a hard cut) from a jump on
    the same page (a dissolve). ``order`` plays the steps in another order
    than they were recorded in (see plan_segments). A step with a card in
    cards.json beside the recording plays as that card (render_card_segment).
    """
    config = config or RenderConfig()
    if camera is not None:
        output_size = camera.output_size(output_size)
    video_duration = probe_duration(video_path)
    lapsing = speeds is not None and any(speed and speed > 1.0 for speed in speeds)
    cards = load_cards(video_path.parent)
    segments, warnings = plan_segments(
        steps, video_duration, floors, lead_in, speeds,
        (lambda start, end: active_seconds(video_path, start, end)) if lapsing else None,
        glides, lambda start, end: frozen_spans(video_path, start, end), order,
        [round(offset, 3) in cards for offset, _, _ in steps] if cards else None,
    )
    pans = load_glides(video_path.parent) if glides is not None else {}
    if pans:
        # A panned glide plays its whole move at the pace it was asked for,
        # and at least as long as its line.
        for position, segment in enumerate(segments):
            pan = pans.get(round(segment.offset, 3))
            if pan is None or segment.step_index is None:
                continue
            line = glides[segment.step_index] or 0.0
            segments[position] = Segment(
                segment.name, segment.offset, segment.source_duration,
                max(float(pan["ms"]) / 1000 + PAN_TAIL, line + SEGMENT_TAIL_PAD if line else 0.0),
                segment.clip, segment.step_index,
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".segments-", dir=output_path.parent
    ) as temporary:
        temporary_path = Path(temporary)
        segment_paths: list[Path] = []
        segment_pages: list[str | None] = []
        audio_clips: list[tuple[float, Path]] = []
        cumulative = 0.0
        # Cards are made once the footage around them is: "blur" is built on
        # the last frame before the card.
        card_parts: list[tuple[int, dict, Segment]] = []

        for segment in segments:
            segment_pages.append(
                pages[segment.step_index]
                if pages is not None and segment.step_index is not None
                and segment.step_index < len(pages) else None
            )
            card = cards.get(round(segment.offset, 3)) if segment.step_index is not None else None
            if card is not None:
                card_parts.append((len(segment_paths), card, segment))
                segment_paths.append(temporary_path / f"segment-{segment.name}.mp4")
                if segment.clip is not None:
                    audio_clips.append((cumulative, segment.clip))
                cumulative += segment.output_duration
                continue
            pan = pans.get(round(segment.offset, 3)) if segment.step_index is not None else None
            if pan is not None:
                segment_paths.append(_render_glide_pan(
                    video_path.parent / pan["image"], pan, segment.output_duration,
                    temporary_path / f"segment-{segment.name}.mp4", output_size,
                ))
                if segment.clip is not None:
                    audio_clips.append((cumulative, segment.clip))
                cumulative += segment.output_duration
                continue
            segment_paths.append(
                _render_video_segment(
                    video_path,
                    segment.offset,
                    segment.source_duration,
                    segment.output_duration,
                    temporary_path / f"segment-{segment.name}.mp4",
                    output_size,
                    camera,
                    segment.speed,
                    segment.drop,
                )
            )
            if segment.clip is not None:
                audio_clips.append((cumulative, segment.clip))
            cumulative += segment.output_duration

        if not segment_paths:
            segment_paths.append(
                _render_video_segment(
                    video_path, 0.0, video_duration, video_duration,
                    temporary_path / "segment-full.mp4",
                    output_size, camera,
                )
            )
            cumulative = video_duration

        is_card = [False] * len(segment_paths)
        for position, _, _ in card_parts:
            is_card[position] = True
        if card_parts:
            footage = [position for position, card in enumerate(is_card) if not card]
            size = output_size or probe_video_size(segment_paths[footage[0]] if footage else video_path)
            for position, card, segment in card_parts:
                still = None
                if card_background(card.get("background")) == "blur":
                    # The last frame before the card; for a card that opens
                    # the film, the first frame after it.
                    before = [other for other in footage if other < position]
                    after = [other for other in footage if other > position]
                    if before or after:
                        still = _still(
                            segment_paths[before[-1] if before else after[0]], bool(before),
                            temporary_path / f"card-{segment.name}.png",
                        )
                render_card_segment(
                    card, segment.output_duration, segment_paths[position], size, config.accent, still,
                )

        if config.transitions == "smooth" and segment_paths and len(segments) > 0:
            timeline = _assemble_smooth(
                segment_paths, segment_pages,
                [segment.clip for segment in segments],
                output_path, output_size, config, temporary_path,
                [segment.step_index for segment in segments], is_card,
            )
            return SegmentedRenderResult(output_path, probe_duration(output_path), warnings, timeline)

        joined = temporary_path / "joined.mp4"
        concat_list = temporary_path / "segments.txt"
        concat_list.write_text(
            "".join(f"file '{path}'\n" for path in segment_paths)
        )
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-f", "concat",
                "-safe", "0", "-i", str(concat_list), "-c", "copy",
                str(joined),
            ],
            check=True,
        )
        if _has_branding(config) or config.music == "subtle":
            _compose_video(
                joined, audio_clips, output_path, cumulative,
                output_size, config,
            )
        else:
            _mux_segment_audio(joined, audio_clips, output_path, cumulative)
    # Hard cuts: parts follow each other, after the title card if there is one.
    timeline: list[dict] = []
    at = 0.0
    if config.title.strip():
        timeline.append({"kind": "intro", "step": None, "start": 0.0, "duration": CARD_DURATION})
        at = CARD_DURATION
    for segment in segments:
        timeline.append({
            "kind": "lead" if segment.step_index is None else "step", "step": segment.step_index,
            "start": round(at, 3), "duration": round(segment.output_duration, 3),
        })
        at += segment.output_duration
    if config.cta_url.strip():
        timeline.append({"kind": "outro", "step": None, "start": round(at, 3), "duration": CARD_DURATION})
    return SegmentedRenderResult(output_path, probe_duration(output_path), warnings, timeline)


SOFT_DISSOLVE = 0.25
TIME_DISSOLVE = 0.5
CARD_FADE = 0.5
# A one-frame xfade: a cut, inside the same filter chain as the dissolves.
CUT_FRAME = 0.04
# PSNR of the frames either side of a join: above this nothing visible
# changed; below the lower bound the picture changed a lot (a result arrived,
# a panel opened) and the join reads as time passing.
SAME_PICTURE_DB = 40.0
BIG_CHANGE_DB = 24.0


def _edge_frame(video: Path, at_end: bool, directory: Path) -> Path:
    frame = directory / f"{video.stem}.{'end' if at_end else 'start'}.png"
    if not frame.exists():
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y",
             *(["-sseof", "-0.08"] if at_end else []), "-i", str(video),
             "-frames:v", "1", "-vf", "scale=480:270,format=gray", str(frame)],
            check=True,
        )
    return frame


def _psnr(first: Path, second: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-i", str(first), "-i", str(second), "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.search(r"average:(inf|[\d.]+)", result.stderr)
    if not found or found.group(1) == "inf":
        return 99.0
    return float(found.group(1))


def choose_transitions(
    segment_paths: Sequence[Path],
    pages: Sequence[str | None],
    directory: Path,
) -> list[float]:
    """The dissolve before each segment after the first (0 means a cut).

    The rules an editor would use, applied the same way every time, so a
    video never fills up with effects:

    - a new page is a hard cut — the cut lands on the action that opened it;
    - on the same page, a join where nothing visible changed stays a cut;
    - a small jump on the same page (a counter ticked, a panel shifted while
      the director was thinking) gets a 0.25 s dissolve that reads as no
      effect at all, just no jolt;
    - a big change on the same page (a result arrived) gets 0.5 s, which
      reads as time passing.

    Chosen from A/B renders of the same take, 2026-10-09.
    """
    choices: list[float] = []
    for index in range(1, len(segment_paths)):
        before, after = pages[index - 1], pages[index]
        if before and after and before != after:
            choices.append(0.0)
            continue
        similarity = _psnr(
            _edge_frame(segment_paths[index - 1], True, directory),
            _edge_frame(segment_paths[index], False, directory),
        )
        if similarity >= SAME_PICTURE_DB:
            choices.append(0.0)
        elif similarity >= BIG_CHANGE_DB:
            choices.append(SOFT_DISSOLVE)
        else:
            choices.append(TIME_DISSOLVE)
    return choices


def _assemble_smooth(
    segment_paths: Sequence[Path],
    pages: Sequence[str | None],
    clips: Sequence[Path | None],
    output_path: Path,
    output_size: tuple[int, int] | None,
    config: RenderConfig,
    temporary: Path,
    step_indices: Sequence[int | None] | None = None,
    cards: Sequence[bool] | None = None,
) -> list[dict]:
    """Join segments and cards in one xfade chain, then lay the narration on.

    Returns where each part landed in the finished video (see
    SegmentedRenderResult.timeline). A mid-film card (``cards``) fades in
    and out like the title and closing cards.

    Each dissolve overlaps two parts, so every later part — and its
    narration — starts that much earlier; offsets are tracked as the chain is
    built.
    """
    width, height = output_size or probe_video_size(segment_paths[0])
    parts: list[tuple[Path, Path | None]] = []
    joins: list[float] = []
    intro_background, outro_background = (
        card_backgrounds(config, segment_paths[0], segment_paths[-1], temporary)
        if (config.title.strip() or config.cta_url.strip()) else (None, None)
    )
    if config.title.strip():
        intro = temporary / "intro.mp4"
        _card(
            intro, width, height, config.accent,
            [(config.title.strip(), max(36, width // 22)),
             (config.subtitle.strip(), max(20, width // 48))]
            if config.subtitle.strip()
            else [(config.title.strip(), max(36, width // 22))],
            intro_background,
        )
        parts.append((intro, None))
        joins.append(CARD_FADE)
    parts.extend(zip(segment_paths, clips))
    segment_joins = choose_transitions(segment_paths, pages, temporary)
    if cards:
        for index in range(1, len(segment_paths)):
            if cards[index] or cards[index - 1]:
                segment_joins[index - 1] = CARD_FADE
    joins.extend(segment_joins)
    if config.cta_url.strip():
        outro = temporary / "outro.mp4"
        _closing_card(outro, width, height, config, outro_background, temporary)
        parts.append((outro, None))
        joins.append(CARD_FADE)

    kinds: list[tuple[str, int | None]] = []
    if config.title.strip():
        kinds.append(("intro", None))
    for position in range(len(segment_paths)):
        step = step_indices[position] if step_indices and position < len(step_indices) else None
        kinds.append(("lead" if step is None else "step", step))
    if config.cta_url.strip():
        kinds.append(("outro", None))

    command = ["ffmpeg", "-loglevel", "error", "-y"]
    for path, _ in parts:
        command += ["-i", str(path)]
    chain: list[str] = []
    label = "[0:v]"
    end = probe_duration(parts[0][0])
    placed = [{"kind": kinds[0][0], "step": kinds[0][1], "start": 0.0, "duration": round(end, 3)}]
    audio: list[tuple[float, Path]] = []
    if parts[0][1] is not None:
        audio.append((0.0, parts[0][1]))
    for index in range(1, len(parts)):
        overlap = joins[index - 1] or CUT_FRAME
        start = max(0.0, end - overlap)
        chain.append(
            f"{label}[{index}:v]xfade=transition=fade:duration={overlap:.3f}"
            f":offset={start:.3f}[v{index}]"
        )
        label = f"[v{index}]"
        if parts[index][1] is not None:
            audio.append((start, cast(Path, parts[index][1])))
        length = probe_duration(parts[index][0])
        end = start + length
        placed.append({"kind": kinds[index][0], "step": kinds[index][1],
                       "start": round(start, 3), "duration": round(length, 3)})
    body = temporary / "smooth.mp4"
    command += (
        ["-filter_complex", ";".join(chain), "-map", label] if chain else ["-map", "0:v"]
    )
    command += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-r", "25", str(body)]
    subprocess.run(command, check=True)
    _mux_segment_audio(body, audio, output_path, end, config.music)
    return placed


STORYBOARD_FILE = "storyboard.json"
STORYBOARD_DIR = "storyboard"


def storyboard_still_at(part: dict) -> float:
    """Where a part's still is taken: a little past its middle, after a
    camera move has landed."""
    return part["start"] + min(part["duration"] * 0.6, max(part["duration"] - 0.15, 0.0))


def write_storyboard(directory: Path, video: Path, timeline: Sequence[dict]) -> list[dict]:
    """Save the finished video's timeline and one still per part.

    The still is taken a little past the middle of each part — after a
    camera move has landed — at 480x270: the watch page's storyboard shows
    them, and the storyboard tool tiles them into a contact sheet.
    """
    frames_dir = directory / STORYBOARD_DIR
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink(missing_ok=True)
    entries = []
    for number, part in enumerate(timeline):
        at = storyboard_still_at(part)
        name: str | None = f"part-{number:03d}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{at:.3f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=480:270", "-q:v", "4", str(frames_dir / name)],
                check=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            name = None
        entries.append({**part, "frame": name})
    (directory / STORYBOARD_FILE).write_text(json.dumps({"parts": entries}))
    return entries


def read_storyboard(directory: Path) -> list[dict]:
    try:
        return json.loads((directory / STORYBOARD_FILE).read_text()).get("parts", [])
    except (OSError, ValueError):
        return []


def _render_video_segment(
    video_path: Path,
    offset: float,
    source_duration: float,
    output_duration: float,
    output_path: Path,
    output_size: tuple[int, int] | None = None,
    camera: "Camera | None" = None,
    speed: float = 1.0,
    drop: Sequence[tuple[float, float]] = (),
) -> Path:
    # -t is an input option here. After -i it capped the output instead, and
    # cut off the frozen frames tpad adds when narration outlasts the footage
    # — the segment came out short and every later line drifted late.
    command = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-ss", f"{offset:.3f}", "-t", f"{source_duration:.3f}",
        "-i", str(video_path),
    ]
    played = source_duration / speed if speed > 1.0 else source_duration
    # fps after setpts drops the frames a time-lapse skips. setpts alone only
    # moves timestamps; zoompan then emits every frame at 25 fps again, and a
    # 4× segment with a camera move played at 1× under its "4×" badge
    # (take 11, 2026-10-10).
    filters = [f"setpts=PTS/{speed:.4f}", "fps=25"] if speed > 1.0 else []
    if drop:
        # A glide's stalls leave the footage; the frames after one follow on.
        skip = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in drop)
        filters = [f"select='not({skip})'", "setpts=N/(25*TB)"]
        played = source_duration - sum(end - start for start, end in drop)
    extension = output_duration - played
    if extension > 0.01:
        filters.append(f"tpad=stop_mode=clone:stop_duration={extension:.3f}")
    camera_filter = None
    if camera is not None:
        size = output_size or camera.physical_size
        camera_filter = camera.zoompan(offset, offset + source_duration, size, speed=speed)
    if camera_filter:
        # After tpad, so a move keeps easing over a held frame; zoompan
        # crops and scales to the output size in one pass.
        filters.append(camera_filter)
    elif output_size is not None:
        filters.append(f"scale={output_size[0]}:{output_size[1]}")
    if speed > 1.0 and Path(FONT_PATH).is_file():
        # Say it is sped up, so a time-lapse never reads as the real pace.
        # Only while the footage plays fast: a hold after it (the line, or
        # an edit_step hold) is a still frame, and "4×" over it read as a
        # frozen time-lapse for eight seconds in take 15 (2026-10-12).
        filters.append(
            f"drawtext=fontfile={FONT_PATH}:text='{speed:g}×':fontcolor=white:fontsize=h/22:"
            f"box=1:boxcolor=black@0.55:boxborderw=14:x=w-tw-48:y=40:enable='lt(t,{played:.3f})'"
        )
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend([
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(output_path),
    ])
    subprocess.run(command, check=True)
    return output_path


def rerender_narration(
    video_path: Path,
    clips: Sequence[tuple[float, Path]],
    output_path: Path,
    output_size: tuple[int, int] | None = None,
    config: RenderConfig | None = None,
    camera: "Camera | None" = None,
) -> Path:
    """Replace a video's audio with delayed narration, extending its last frame if needed.

    Camera moves are rendered only by segmented_render; here a hi-res
    recording is just scaled down to its CSS size.
    """
    config = config or RenderConfig()
    if camera is not None:
        output_size = camera.output_size(output_size)
    video_duration = probe_duration(video_path)
    temp_path = output_path.with_name(f".{output_path.stem}.rerender.mp4")
    if not clips:
        video_filter = (
            f"scale={output_size[0]}:{output_size[1]}"
            if output_size is not None else None
        )
        command = [
            "ffmpeg", "-y", "-i", str(video_path),
            *(["-vf", video_filter] if video_filter else []),
            "-map", "0:v:0", "-an",
            *(["-c:v", "libx264", "-preset", "veryfast"]
              if output_size is not None else ["-c:v", "copy"]),
            str(temp_path),
        ]
        subprocess.run(command, check=True)
        if _has_branding(config) or config.music == "subtle":
            _compose_video(
                temp_path, clips, output_path, video_duration,
                output_size, config,
            )
            temp_path.unlink(missing_ok=True)
        else:
            temp_path.replace(output_path)
        return output_path

    clip_durations = [(offset, clip, probe_duration(clip)) for offset, clip in clips]
    audio_end = max(offset + duration for offset, _, duration in clip_durations)
    extend_by = max(0.0, audio_end - video_duration)
    command = ["ffmpeg", "-y", "-i", str(video_path)]
    filters: list[str] = []
    for index, (offset, clip, _) in enumerate(clip_durations, start=1):
        command.extend(["-i", str(clip)])
        delay = max(0, round(offset * 1000))
        filters.append(f"[{index}:a]adelay={delay}|{delay}[a{index}]")
    labels = "".join(f"[a{i}]" for i in range(1, len(clip_durations) + 1))
    filters.append(
        f"{labels}amix=inputs={len(clip_durations)}:duration=longest:"
        "dropout_transition=0[a]"
    )
    if extend_by > 0.05:
        filters.insert(
            0,
            f"[0:v]tpad=stop_mode=clone:stop_duration={extend_by:.3f}[v]",
        )
        video_map = "[v]"
        video_codec = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    else:
        video_map = "0:v:0"
        video_codec = ["-c:v", "copy"]
    if output_size is not None:
        if extend_by > 0.05:
            filters[0] = (
                f"[0:v]tpad=stop_mode=clone:stop_duration={extend_by:.3f}[padded];"
                f"[padded]scale={output_size[0]}:{output_size[1]}[scaled]"
            )
        else:
            filters.insert(
                0,
                f"[0:v]scale={output_size[0]}:{output_size[1]}[scaled]",
            )
        video_map = "[scaled]"
        video_codec = ["-c:v", "libx264", "-preset", "veryfast"]
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", video_map, "-map", "[a]",
        *video_codec, "-c:a", "aac", str(temp_path),
    ])
    subprocess.run(command, check=True)
    if _has_branding(config) or config.music == "subtle":
        _compose_video(
            temp_path, clips, output_path,
            max(video_duration, audio_end), output_size, config,
        )
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(output_path)
    return output_path


# Subtitles: the narration written into the picture, for the feeds that play
# video muted (LinkedIn does by default). Each line shows while its step is on
# screen; a long line is split into short chunks, timed by their length.
SUBTITLE_MAX_WORDS = 8
# The box behind the text, as ASS colours (alpha first: 00 opaque, FF clear).
# "soft" is about 40% black; on light, busy pages white text over it was hard
# to read (FlowWink film, 2026-10-25), so "box" is about 75% and "solid" opaque.
SUBTITLE_STYLES = {"soft": "&H96000000", "box": "&H40000000", "solid": "&H00000000"}


def subtitle_cues(parts: Sequence[dict], narrations: dict, max_words: int = SUBTITLE_MAX_WORDS) -> list[tuple[float, float, str]]:
    """(start, end, text) per chunk, from the storyboard and each step's line."""
    cues: list[tuple[float, float, str]] = []
    # Parts overlap where a transition blends one into the next, so a part's
    # line ends where the next part begins — two subtitles on screen at once
    # read as one garbled sentence (FlowWink take 2, 2026-10-24).
    starts = sorted(float(p.get("start") or 0.0) for p in parts)
    for part in parts:
        if part.get("kind", "step") != "step":
            continue
        text = " ".join(str(narrations.get(part.get("step")) or "").split())
        if not text:
            continue
        start = float(part.get("start") or 0.0)
        duration = float(part.get("duration") or 0.0)
        following = [s for s in starts if s > start + 0.01]
        if following:
            duration = min(duration, following[0] - start)
        if duration <= 0.2:
            continue
        words = text.split(" ")
        chunks = [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
        # Even out a short last chunk: "…the hotel" + "too." reads badly.
        if len(chunks) > 1 and len(chunks[-1].split(" ")) <= 2:
            merged = (chunks[-2] + " " + chunks[-1]).split(" ")
            half = (len(merged) + 1) // 2
            chunks[-2:] = [" ".join(merged[:half]), " ".join(merged[half:])]
        total = sum(len(c) for c in chunks)
        at = start
        for chunk in chunks:
            length = duration * len(chunk) / total
            cues.append((round(at, 2), round(at + length, 2), chunk))
            at += length
    return cues


def _ass_time(seconds: float) -> str:
    centis = int(round(max(seconds, 0.0) * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def subtitles_ass(cues: Sequence[tuple[float, float, str]], width: int, height: int, style: str = "soft") -> str:
    """An ASS script: white text on a dark box near the bottom, sized to the
    frame; style (SUBTITLE_STYLES) sets how dark the box is."""
    box = SUBTITLE_STYLES.get(style, SUBTITLE_STYLES["soft"])
    size = max(18, round(min(width, height) / 22))
    margin = max(24, round(height * 0.07))
    escape = lambda t: t.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,{box},{box},1,0,0,0,100,100,0,0,3,{max(6, size // 3)},0,2,{round(width * 0.08)},{round(width * 0.08)},{margin},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in cues:
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{escape(text)}")
    return "\n".join(lines) + "\n"


def burn_subtitles(video: Path, cues: Sequence[tuple[float, float, str]], style: str = "soft") -> bool:
    """Write the cues into the video in place. False when there was nothing to write."""
    if not cues:
        return False
    width, height = probe_video_size(video)
    script = video.with_name("subtitles.ass")
    script.write_text(subtitles_ass(cues, width, height, style), encoding="utf-8")
    temporary = video.with_name("video.subtitled.mp4")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vf", f"ass={script.name}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", str(temporary)],
        check=True, cwd=str(video.parent), timeout=900,
    )
    temporary.replace(video)
    return True

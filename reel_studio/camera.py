"""A virtual camera: push in on a detail, pull back out, in post.

The browser is recorded at a higher pixel density than its CSS layout (see
``capture_scale``), so a 2x push-in on a 1920x1080 page crops a 1280x720 CSS
region that still has 1707x960 real pixels behind it. Text stays sharp at the
closest framing instead of being upscaled from 960x540.

The camera is a list of keyframes on the recording's own clock. Each key says
"from t, ease to this zoom and centre over EASE_SECONDS". Rendering turns the
keys into an ffmpeg zoompan expression per kept segment, so the moves survive
the segmented cut and a rerender.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path

FRAMING_ZOOM = {"wide": 1.0, "medium": 1.5, "close": 2.0}
# Deeper than 2.5x reads as a cut-in, not a camera move, and even at 4/3
# capture the text starts to soften.
MAX_ZOOM = 2.5
# Product-video tools settle around a second per move; shorter feels like a
# jolt, longer drags under narration.
EASE_SECONDS = 1.0
# A target whose centre leaves the middle of the view gets the camera panned
# to it before it is clicked or typed into, so no action happens off-frame.
FOLLOW_MARGIN = 0.05
# Space kept between a wide target's first edge and the frame's edge.
AIM_PAD = 0.08
CAMERA_FILE = "camera.json"
# Space kept beside a row of panels, as a share of the page's width.
PANEL_GUTTER = 0.012
# A panel showing less than this, or more than PARTLY_MAX, is out of frame or
# in it; between the two the frame's edge runs through it.
PARTLY_MIN, PARTLY_MAX = 0.08, 0.92
# A line held this long gets a second, gentle camera move. Sluss's flows beat
# stood still for 11 s under its line, and agents left the finding open.
HOLD_SECONDS = 7.0
HOLD_PUSH = 1.07
HOLD_EASE = 1.6
# Keep this far from what the shot's own move and the next cut are doing.
HOLD_CLEAR = 1.5

SCALE_CANDIDATES = (4 / 3, 1.5, 2.0)
# 2560x1440 is what a 2-core host records at 25 fps without dropping frames.
MAX_CAPTURE_PIXELS = 2560 * 1440
# The largest scale an output_size may ask the capture for.
MAX_OUTPUT_SCALE = 2.0


def capture_scale(width: int, height: int, output: tuple[int, int] | None = None) -> float:
    """Pick the device scale factor the browser is recorded at.

    The physical frame must be whole, even pixels (libx264 with yuv420p) and
    no larger than MAX_CAPTURE_PIXELS. ``REEL_CAPTURE_SCALE`` overrides; 1
    turns hi-res capture off.

    An ``output`` larger than the page asks for that scale: a 720x900 page
    (the panel's phone layout) recorded for a 1080x1350 video is captured at
    1.5, so the 4:5 video is sharp at full size rather than upscaled.
    """
    configured = os.environ.get("REEL_CAPTURE_SCALE", "").strip()
    candidates: tuple[float, ...] = SCALE_CANDIDATES
    if output is not None and output[0] > width:
        wanted = output[0] / width
        if abs(output[1] / height - wanted) < 1e-6 and wanted <= MAX_OUTPUT_SCALE:
            candidates = (wanted,)
    if configured:
        try:
            value = float(configured)
        except ValueError:
            value = 0.0
        if value <= 1.0:
            return 1.0
        candidates = (value,)
    for scale in candidates:
        physical_w, physical_h = width * scale, height * scale
        whole = (
            abs(physical_w - round(physical_w)) < 1e-6
            and abs(physical_h - round(physical_h)) < 1e-6
        )
        if not whole or round(physical_w) % 2 or round(physical_h) % 2:
            continue
        if round(physical_w) * round(physical_h) > MAX_CAPTURE_PIXELS:
            continue
        return scale
    return 1.0


def _ease_term(t: str, start: float, seconds: float = EASE_SECONDS) -> str:
    u = f"clip(({t}-{start:.3f})/{max(seconds, 0.001):.3f},0,1)"
    return f"({u}*{u}*(3-2*{u}))"


def panel_is_cut(panel: dict, left: float, right: float) -> bool:
    """Whether a frame from left to right runs through this panel."""
    shown = (min(panel["x"] + panel["width"], right) - max(panel["x"], left)) / panel["width"]
    return PARTLY_MIN < shown < PARTLY_MAX


@dataclass
class Camera:
    width: int
    height: int
    scale: float = 1.0
    # Each key: {"t": seconds on the recording clock, "zoom", "cx", "cy"} with
    # the centre in CSS pixels.
    keys: list[dict] = field(default_factory=list)

    @property
    def physical_size(self) -> tuple[int, int]:
        return round(self.width * self.scale), round(self.height * self.scale)

    def output_size(self, requested: tuple[int, int] | None) -> tuple[int, int] | None:
        """The final video size: the CSS size unless asked otherwise.

        At scale 1 the recording already is the CSS size, so None (no scale
        pass) keeps the old behaviour.
        """
        if requested is not None:
            return requested
        if self.scale != 1.0:
            return (self.width, self.height)
        return None

    def state_at(self, t: float) -> tuple[float, float, float]:
        """Zoom and CSS centre after every key up to t has fully eased."""
        zoom, cx, cy = 1.0, self.width / 2, self.height / 2
        for key in self.keys:
            if key["t"] > t:
                break
            zoom, cx, cy = key["zoom"], key["cx"], key["cy"]
        return zoom, cx, cy

    def final_state(self) -> tuple[float, float, float]:
        return self.state_at(float("inf"))

    def view(self, state: tuple[float, float, float] | None = None) -> dict:
        """The CSS rectangle a state shows, clamped to the page like zoompan."""
        zoom, cx, cy = state or self.final_state()
        w, h = self.width / zoom, self.height / zoom
        x = min(max(cx - w / 2, 0.0), self.width - w)
        y = min(max(cy - h / 2, 0.0), self.height - h)
        return {"x": x, "y": y, "w": w, "h": h, "zoom": zoom}

    def move(
        self, t: float, zoom: float, cx: float | None = None, cy: float | None = None,
        ease: float = EASE_SECONDS,
    ) -> bool:
        """Add a key at t. Returns False when it would not change the shot.

        ``ease`` 0 is a cut to the new framing: a new page starts wide on its
        first frame instead of zooming out over it.
        """
        zoom = min(max(zoom, 1.0), MAX_ZOOM)
        current = self.final_state()
        if zoom == 1.0:
            cx, cy = self.width / 2, self.height / 2
        cx = current[1] if cx is None else min(max(cx, 0.0), float(self.width))
        cy = current[2] if cy is None else min(max(cy, 0.0), float(self.height))
        new = (zoom, cx, cy)
        if all(abs(a - b) < 0.5 for a, b in zip(self.view(new).values(), self.view(current).values())):
            return False
        # Keys are appended in recording order; a key at the same instant as
        # the previous one replaces it.
        if self.keys and abs(self.keys[-1]["t"] - t) < 1e-3:
            self.keys.pop()
        key = {"t": round(t, 3), "zoom": round(zoom, 4),
               "cx": round(cx, 1), "cy": round(cy, 1)}
        if ease != EASE_SECONDS:
            key["ease"] = round(ease, 3)
        self.keys.append(key)
        return True

    def _anchor(self, box: dict, zoom: float) -> tuple[float, float, float, float]:
        """The part of a box a frame at this zoom should show.

        A box that fits is shown whole. One wider or taller than the frame is
        shown from its start: centring a full-width result panel put its
        first words — "✓ The model answered" — off the left edge, and the
        close-up showed an empty green box ending in "work." (2026-10-09).
        """
        view_w, view_h = self.width / zoom, self.height / zoom
        usable_w, usable_h = view_w * (1 - 2 * AIM_PAD), view_h * (1 - 2 * AIM_PAD)
        x0, y0 = box["x"], box["y"]
        return x0, y0, x0 + min(box["width"], usable_w), y0 + min(box["height"], usable_h)

    def aim(self, box: dict, zoom: float, lead: bool = False) -> tuple[float, float]:
        """The centre that frames a box at this zoom.

        With ``lead`` the box starts AIM_PAD from the frame's left edge
        instead of sitting in the middle: how a line of text is framed. A
        short line near a page's left column, centred, pulled the frame
        against the page edge and put the whole sidebar in a quarter of
        every close-up (2026-10-09); led, the line opens the frame and the
        page continues to its right.
        """
        zoom = max(zoom, 1.0)
        x0, y0, x1, y1 = self._anchor(box, zoom)
        cy = (y0 + y1) / 2
        if lead:
            view_w = self.width / zoom
            return x0 - view_w * AIM_PAD + view_w / 2, cy
        return (x0 + x1) / 2, cy

    def fit_zoom(self, box: dict, zoom: float, lift: float = 0.0) -> float:
        """The deepest zoom, up to ``zoom``, at which a box is seen whole.

        A highlighted standfirst at 1.8x lost its last words off the right
        edge at 1280x720: a quote that wraps is as wide as its column, and
        _anchor shows a box too wide for the frame from its start
        (TechTarget, 2026-10-28). ``lift`` is the share of the frame the
        focus is raised by (set_shot's lift): the box's middle then sits
        that much above the frame's, and has that much less room above it.
        """
        zoom = max(zoom, 1.0)
        usable = 1 - 2 * AIM_PAD
        if box.get("width", 0) > 0:
            zoom = min(zoom, self.width * usable / box["width"])
        if box.get("height", 0) > 0:
            zoom = min(zoom, self.height * (usable - 2 * lift) / box["height"])
        return max(zoom, 1.0)

    def frame_panels(self, panels: list[dict], box: dict, zoom: float, keep_zoom: bool = False) -> dict:
        """Frame a page of panels so the frame's edge runs between them.

        Sluss's audit beat framed "Audit chain" and left an empty third of the
        frame beside a chart cut in two (take 7). The focus's panel is shown
        whole, and the left edge is put where it cuts the fewest panels in that
        row. Without a camera the beat asked for, the zoom comes down until
        the row fits. Returns {} when the focus is in no panel.
        """
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        holding = [p for p in panels if p["x"] <= cx <= p["x"] + p["width"] and p["y"] <= cy <= p["y"] + p["height"]]
        if not holding:
            return {}
        focus = min(holding, key=lambda p: p["width"] * p["height"])

        def beside(panel: dict) -> bool:
            overlap = min(panel["y"] + panel["height"], focus["y"] + focus["height"]) - max(panel["y"], focus["y"])
            return overlap >= 0.3 * min(panel["height"], focus["height"])

        row = [panel for panel in panels if beside(panel)]
        left, right = min(p["x"] for p in row), max(p["x"] + p["width"] for p in row)
        gutter = self.width * PANEL_GUTTER
        usable = self.width - 2 * gutter
        row_zoom, panel_zoom = max(1.0, usable / (right - left)), max(1.0, usable / focus["width"])
        level = max(zoom, 1.0) if keep_zoom else max(1.0, min(zoom, row_zoom))
        view = self.width / level
        limit = self.width - view
        centre = focus["x"] + focus["width"] / 2
        edges = {min(max(edge, 0.0), limit) for edge in (
            focus["x"] - gutter, focus["x"] + focus["width"] + gutter - view,
            left - gutter, right + gutter - view, centre - view / 2)}

        def cost(edge: float) -> tuple[bool, int, float]:
            whole = edge <= focus["x"] and focus["x"] + focus["width"] <= edge + view
            return (not whole, sum(panel_is_cut(p, edge, edge + view) for p in row), abs(edge + view / 2 - centre))

        best = min(sorted(edges), key=cost)
        return {"zoom": level, "cx": best + view / 2, "panels": row,
                "row_camera": round(row_zoom, 2), "panel_camera": round(panel_zoom, 2)}

    def hold_moves(self, holds: list[tuple[float, float]]) -> list[dict]:
        """The moves that keep a long line from standing still.

        ``holds`` is (where the line starts, how long it lasts) on the
        recording's clock. Only a shot the camera is already framing gets one:
        such a shot has its own move out, so the frame does not stay pushed
        in. Each move is {t, seconds_in, zoom, cx, cy}.
        """
        moves: list[dict] = []
        for start, seconds in holds:
            if seconds < HOLD_SECONDS:
                continue
            opening = next((key for key in self.keys if abs(float(key["t"]) - start) < 0.06), None)
            if opening is None or float(opening["zoom"]) <= 1.0:
                continue
            if any(start + HOLD_CLEAR < float(key["t"]) < start + seconds - HOLD_CLEAR for key in self.keys):
                continue
            zoom, cx, cy = float(opening["zoom"]), float(opening["cx"]), float(opening["cy"])
            focus = (opening.get("facts") or {}).get("focus") or {}
            ceiling = float((opening.get("facts") or {}).get("panel_camera") or MAX_ZOOM)
            count = 2 if seconds >= 2 * HOLD_SECONDS else 1
            for number in range(1, count + 1):
                level = min(zoom * HOLD_PUSH, MAX_ZOOM, max(ceiling, zoom))
                if level <= zoom + 1e-3:
                    break
                if focus.get("width"):
                    cx += (focus["x"] + focus["width"] / 2 - cx) * 0.25
                    cy += (focus["y"] + focus["height"] / 2 - cy) * 0.25
                seconds_in = seconds * number / (count + 1)
                moves.append({"t": round(start + seconds_in, 3), "seconds_in": round(seconds_in, 3),
                              "zoom": round(level, 4), "cx": round(cx, 1), "cy": round(cy, 1)})
                zoom = level
        return moves

    def with_hold_moves(self, holds: list[tuple[float, float]]) -> "Camera":
        """This camera with the hold moves added: for the render only, so a
        rerender starts from the shots as they were set up."""
        moves = self.hold_moves(holds)
        keys = [{key: move[key] for key in ("t", "zoom", "cx", "cy")} | {"ease": HOLD_EASE} for move in moves]
        return Camera(self.width, self.height, self.scale, sorted([*self.keys, *keys], key=lambda key: float(key["t"])))

    def needs_follow(self, box: dict | None) -> tuple[float, float] | None:
        """The centre to pan to so a target is inside the view, if needed."""
        if not box:
            return None
        zoom, _, _ = self.final_state()
        if zoom <= 1.0:
            return None
        view = self.view()
        x0, y0, x1, y1 = self._anchor(box, zoom)
        mx, my = view["w"] * FOLLOW_MARGIN, view["h"] * FOLLOW_MARGIN
        inside = (view["x"] + mx <= x0 and x1 <= view["x"] + view["w"] - mx
                  and view["y"] + my <= y0 and y1 <= view["y"] + view["h"] - my)
        return None if inside else self.aim(box, zoom)

    def moves(self) -> bool:
        return any(key["zoom"] != 1.0 for key in self.keys)

    def zoompan(
        self, offset: float, source_end: float, size: tuple[int, int], fps: int = 25, speed: float = 1.0,
    ) -> str | None:
        """An ffmpeg zoompan filter for one segment of the recording.

        The segment starts at ``offset`` on the recording clock; zoompan's
        ``it`` counts from 0 there and keeps counting through tpad's frozen
        frames, so a move that starts near the end of the footage still
        finishes on the held frame. Keys at or after ``source_end`` belong to
        the next segment and are left out, or they would fire early inside
        this one's padding. Returns None when this segment needs no camera.
        """
        if not self.moves():
            return None
        # A sped-up segment plays its source `speed` times faster, so one
        # second of output covers `speed` seconds of the recording's clock.
        t = f"({offset:.3f}+it*{speed:.4f})" if speed != 1.0 else f"({offset:.3f}+it)"
        base = self.state_at(offset - EASE_SECONDS)
        active = [key for key in self.keys
                  if offset - EASE_SECONDS < key["t"] < source_end]
        previous = base
        values = [[f"{base[0]:.4f}"], [f"{base[1]:.1f}"], [f"{base[2]:.1f}"]]
        for key in active:
            current = (key["zoom"], key["cx"], key["cy"])
            ease = _ease_term(t, key["t"], key.get("ease", EASE_SECONDS))
            for axis in range(3):
                delta = current[axis] - previous[axis]
                if abs(delta) > 1e-6:
                    values[axis].append(f"{delta:+.4f}*{ease}")
            previous = current
        if not active and base[0] == 1.0:
            return None
        zoom, cx, cy = ("".join(parts) for parts in values)
        s = self.scale
        exprs = [
            f"z='max(1,{zoom})'",
            f"x='clip(({cx})*{s:.6f}-iw/zoom/2,0,iw-iw/zoom)'",
            f"y='clip(({cy})*{s:.6f}-ih/zoom/2,0,ih-ih/zoom)'",
            "d=1",
            f"s={size[0]}x{size[1]}",
            f"fps={fps}",
        ]
        return "zoompan=" + ":".join(exprs)

    def save(self, directory: Path) -> None:
        (directory / CAMERA_FILE).write_text(json.dumps(asdict(self)))

    @classmethod
    def load(cls, directory: Path) -> "Camera | None":
        path = directory / CAMERA_FILE
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
            return cls(int(data["width"]), int(data["height"]),
                       float(data.get("scale", 1.0)), list(data.get("keys", [])))
        except (ValueError, KeyError, TypeError):
            return None

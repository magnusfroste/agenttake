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
import math
import os
from pathlib import Path

FRAMING_ZOOM = {"wide": 1.0, "medium": 1.5, "close": 2.0}
# Deeper than 2.5x reads as a cut-in, not a camera move, and even at 4/3
# capture the text starts to soften.
MAX_ZOOM = 2.5
# How long a move takes to settle. Moves follow a critically damped spring:
# no jolt at the start, most of the way in the first half, a soft landing.
# Screen-recording tools settle a push-in in about half a second (Cap's
# default spring: 90 % at 0.32 s, still at 0.5 s); Take's old 1.0 s ease-in-
# out under every move made films read slow (research 2026-11-18).
EASE_SECONDS = 0.6
# A move this long or longer is a drift, not a push: an even ease-in-out, so
# a slow move over a whole line does not arrive early and then creep.
DRIFT_SECONDS = 1.2
# The spring's rate over a move of length 1: at the move's end it has covered
# 99 % of the way (1 - (1 + a) e^-a), and 90 % at 59 % of it.
SPRING_RATE = 6.6
# The second kind of move: an emphasis. Where the line names the thing, the
# frame pushes in a little (pros use 5-15 %; past that it reads as a new
# shot) and fast, then lets go when the line is over.
EMPHASIS_PUSH = 1.08
EMPHASIS_SECONDS = 0.25
# A move under a line has landed by this share of the line, so what the line
# names stands still while it is said. FlowWink take 2 pushed in for 2.5 s
# under lines of 1.4-2.2 s, ten shots in a row: the camera was moving for 87%
# of that stretch and arrived after each line was over (2026-11-17).
LAND_SHARE = 0.4
# Shorter than this a move is a jolt: the shot cuts to its framing instead.
MIN_MOVE_SECONDS = 0.4
# Closer than this is a push-in on a detail; up to it, a slow drift over a
# frame that already shows what the line is about.
PUSH_ZOOM = 1.25
# A target whose centre leaves the middle of the view gets the camera panned
# to it before it is clicked or typed into, so no action happens off-frame.
FOLLOW_MARGIN = 0.05
# Space kept between a wide target's first edge and the frame's edge.
AIM_PAD = 0.08
# A line of text is led from the start of the row, card or line it sits in
# (its own padding is the margin: a pad on top showed a strip of the sidebar
# beside the page's column), as long as the words themselves stay
# inside the frame with their middle no further in than LEAD_MAX_SHARE of it.
# Led from the words, FlowWink take 3 cut "Welcome, admin" to "ome, admin" and
# "Prospects" to "ospects" at the frame's left edge, and the thing the line
# named sat against that edge in every close-up (2026-11-18).
LEAD_MAX_SHARE = 0.6
# A start closer to the words than this share of the frame is the words' own
# box: the frame would open on their first letter.
LEAD_ROOM = 0.02
# A block this share of the page's width or wider is the page's layout, not a
# row: leading from it would bring the sidebar back in.
LEAD_LAYOUT_SHARE = 0.9
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


def landing_ease(ease: float, line_seconds: float, short: str = "cut") -> float:
    """How long a move may take under a line of ``line_seconds``: at most
    LAND_SHARE of it. When that is too short to be a move, ``short`` says what
    it becomes: "cut" a cut to the framing (0), "move" the shortest move."""
    if line_seconds <= 0 or ease <= 0:
        return max(ease, 0.0)
    fits = min(ease, LAND_SHARE * line_seconds)
    if fits >= MIN_MOVE_SECONDS:
        return fits
    return 0.0 if short == "cut" else min(ease, MIN_MOVE_SECONDS)


# Where a key is still easing into place, as far back as any move reaches.
LOOKBACK_SECONDS = 3.0


def spring_share(u: float) -> float:
    """How far along a move of length 1 is at u (0-1): a critically damped
    spring, scaled so it ends exactly there."""
    u = min(max(u, 0.0), 1.0)
    a = SPRING_RATE
    end = 1 - (1 + a) * math.exp(-a)
    return (1 - (1 + a * u) * math.exp(-a * u)) / end


def drift_share(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def ease_share(u: float, seconds: float) -> float:
    """The share of a move done at u, by the curve a move this long takes."""
    return drift_share(u) if seconds >= DRIFT_SECONDS else spring_share(u)


def _ease_term(t: str, start: float, seconds: float = EASE_SECONDS) -> str:
    u = f"clip(({t}-{start:.3f})/{max(seconds, 0.001):.3f},0,1)"
    if seconds >= DRIFT_SECONDS:
        return f"({u}*{u}*(3-2*{u}))"
    a = SPRING_RATE
    end = 1 - (1 + a) * math.exp(-a)
    return f"((1-(1+{a:g}*{u})*exp(-{a:g}*{u}))/{end:.6f})"


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
    # Emphasis pushes, added at render (with_hold_moves): {"t", "hold": how
    # long it stays in, "cx", "cy": the centre it leans to, or none}.
    emphasis: list[dict] = field(default_factory=list)

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
            return self.lead_edge(box, zoom) + view_w / 2, cy
        return (x0 + x1) / 2, cy

    def lead_edge(self, box: dict, zoom: float) -> float:
        """The frame's left edge for a line of text led from its start.

        ``box["starts"]`` are the boxes the words sit in, from the page's
        measure (the span, the cell, the row, the card). The leftmost one the
        words still fit beside wins: the frame then opens where the row
        opens, not on a word in its middle, and not AIM_PAD before the words
        when that is inside the sidebar beside the page's column. Without
        them, or when none fits, the frame opens AIM_PAD before the words.
        """
        view_w = self.width / max(zoom, 1.0)
        x0, _, x1, _ = self._anchor(box, zoom)
        edge = x0 - view_w * AIM_PAD
        middle = (x0 + x1) / 2
        for start in sorted(box.get("starts") or (), key=lambda s: s["x"]):
            left = start["x"]
            # The words' own span opens on the words: no room before them.
            if start.get("width", 0) >= self.width * LEAD_LAYOUT_SHARE or x0 - left < view_w * LEAD_ROOM:
                continue
            if x1 + view_w * AIM_PAD - left <= view_w and middle - left <= view_w * LEAD_MAX_SHARE:
                return left
        return edge

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

    def emphasize(self, t: float, hold: float, focus: dict | None = None) -> None:
        """Push in a little at t, on the words the line names, until ``hold``
        seconds later. Kept apart from the shot's keys and added at render,
        like the hold moves: the shot's own framing stays what it was set to."""
        entry = {"t": round(t, 3), "hold": round(max(hold, EMPHASIS_SECONDS), 3)}
        if focus and focus.get("width"):
            entry["cx"] = round(focus["x"] + focus["width"] / 2, 1)
            entry["cy"] = round(focus["y"] + focus["height"] / 2, 1)
        self.emphasis.append(entry)

    def emphasis_moves(self) -> list[dict]:
        """The keys the emphasis pushes add: in at t, out again when the hold
        is over unless the shot has moved on by then.

        The push is anchored on the words: they stay where they are in the
        frame and grow there. Leaning the frame a quarter of the way toward
        them instead pulled a strip of the page's edge into a close-up that
        had been framed from the row's start (proof take, 2026-11-19)."""
        added: list[dict] = []
        for entry in self.emphasis:
            t, until = float(entry["t"]), float(entry["t"]) + float(entry["hold"])
            zoom, cx, cy = self.state_at(t)
            level = min(zoom * EMPHASIS_PUSH, MAX_ZOOM)
            if level <= zoom + 1e-3:
                continue
            if entry.get("cx") is not None:
                # The point under the words keeps its place on screen.
                fx, fy = float(entry["cx"]), float(entry["cy"])
                cx = fx + (cx - fx) * zoom / level
                cy = fy + (cy - fy) * zoom / level
            added.append({"t": round(t, 3), "zoom": round(level, 4), "cx": round(cx, 1), "cy": round(cy, 1),
                          "ease": EMPHASIS_SECONDS})
            if not any(t < float(key["t"]) <= until + EASE_SECONDS for key in self.keys):
                back = self.state_at(until)
                added.append({"t": round(until, 3), "zoom": back[0], "cx": back[1], "cy": back[2],
                              "ease": EASE_SECONDS})
        return added

    def with_hold_moves(self, holds: list[tuple[float, float]]) -> "Camera":
        """This camera with the hold moves and emphasis pushes added: for the
        render only, so a rerender starts from the shots as they were set up."""
        moves = self.hold_moves(holds)
        keys = [{key: move[key] for key in ("t", "zoom", "cx", "cy")} | {"ease": HOLD_EASE} for move in moves]
        keys += self.emphasis_moves()
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
        base = self.state_at(offset - LOOKBACK_SECONDS)
        active = [key for key in self.keys
                  if offset - LOOKBACK_SECONDS < key["t"] < source_end]
        previous = base
        values = [[f"{base[0]:.4f}"], [f"{base[1]:.1f}"], [f"{base[2]:.1f}"]]
        for key in active:
            current = (key["zoom"], key["cx"], key["cy"])
            seconds = key.get("ease", EASE_SECONDS)
            # A cut on the segment's first frame is that frame's framing: as
            # a step in time it was still the old framing at it=0, one frame
            # of the wide page before a shot that opens close.
            ease = "1" if seconds < 0.002 and key["t"] <= offset + 0.0005 else _ease_term(t, key["t"], seconds)
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
                       float(data.get("scale", 1.0)), list(data.get("keys", [])),
                       list(data.get("emphasis", [])))
        except (ValueError, KeyError, TypeError):
            return None

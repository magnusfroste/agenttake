"""What a storyboard shot keeps out of frame and in it.

The TechTarget test film (2026-10-28) showed both: a link address in
Chrome's status bubble under the highlight, and the quote cut at the right
edge.
"""

from __future__ import annotations

import asyncio
import inspect
import types

import pytest

from reel_studio.camera import AIM_PAD, Camera
from reel_studio.engine import SUBTITLE_LIFT, BrowserSession, _POINTER_PARK_JS


# ---------------------------------------------------------------------------
# The quote stays whole

# TechTarget's standfirst as the marked range measured it: two lines of a
# 720-pixel column, at 1280x720.
STANDFIRST = {"x": 65.0, "y": 495.0, "width": 720.0, "height": 70.0}


def test_fit_zoom_lowers_the_zoom_until_a_wide_quote_fits():
    camera = Camera(1280, 720, 1.5)
    zoom = camera.fit_zoom(STANDFIRST, 1.8, SUBTITLE_LIFT)
    assert 1.0 < zoom < 1.8
    usable_w = 1280 / zoom * (1 - 2 * AIM_PAD)
    assert STANDFIRST["width"] <= usable_w + 1e-6
    # At that zoom the camera frames the box whole, from edge to edge.
    cx, _ = camera.aim(STANDFIRST, zoom, lead=True)
    view = camera.view((zoom, cx, 360))
    assert view["x"] <= STANDFIRST["x"]
    assert STANDFIRST["x"] + STANDFIRST["width"] <= view["x"] + view["w"]


def test_fit_zoom_keeps_a_short_quote_close():
    camera = Camera(1280, 720)
    short = {"x": 200, "y": 300, "width": 300, "height": 30}
    assert camera.fit_zoom(short, 1.8, SUBTITLE_LIFT) == 1.8


def test_fit_zoom_never_goes_below_the_whole_page():
    camera = Camera(1280, 720)
    assert camera.fit_zoom({"x": 0, "y": 0, "width": 1270, "height": 40}, 1.8) == 1.0


def test_fit_zoom_leaves_room_above_the_subtitles_for_a_tall_quote():
    camera = Camera(1280, 720)
    tall = {"x": 100, "y": 200, "width": 300, "height": 200}
    lifted = camera.fit_zoom(tall, 2.5, SUBTITLE_LIFT)
    assert lifted < camera.fit_zoom(tall, 2.5)
    # Lifted, the box's middle sits SUBTITLE_LIFT of the frame above the
    # frame's middle, and its top stays inside the padded frame.
    view_h = 720 / lifted
    assert (0.5 - SUBTITLE_LIFT) * view_h - tall["height"] / 2 >= AIM_PAD * view_h - 1e-6


class ShotStandIn:
    """Just what set_shot reads from a session."""

    def __init__(self, box):
        self.width, self.height = 1280, 720
        self.camera = Camera(1280, 720, 1.5)
        self.refs: dict[str, str] = {}
        self.refs_stale = False
        self.pending_shot = None
        self.pending_ease = 1.0
        self.box = box

    async def _visible_text_target(self, text, exact=False):
        return object()

    async def _text_box(self, target, text):
        return dict(self.box)


def test_set_shot_with_fit_frames_the_whole_quote():
    session = ShotStandIn(STANDFIRST)
    result = asyncio.run(BrowserSession.set_shot(
        session, "medium", 1.8, None, "struggling to track", 1.2, lift=True, fit=True))
    zoom = session.pending_shot[0]
    assert zoom < 1.8 and result["camera_zoom"] == zoom and "camera_fit" in result
    frame = result["camera_frame"]
    assert frame["x"] <= STANDFIRST["x"]
    assert STANDFIRST["x"] + STANDFIRST["width"] <= frame["x"] + frame["w"]


def test_set_shot_without_fit_keeps_the_asked_zoom():
    session = ShotStandIn(STANDFIRST)
    result = asyncio.run(BrowserSession.set_shot(
        session, "medium", 1.8, None, "struggling to track", 1.2, lift=True))
    assert session.pending_shot[0] == 1.8 and "camera_fit" not in result


# ---------------------------------------------------------------------------
# No link address in frame


class PointerStandIn:
    def __init__(self, pointer, held=False, page=None):
        self.pointer = pointer
        self.pointer_held = held
        self.page = page
        self.parked: list[tuple[float, float]] = []

    async def _park_pointer(self, x, y):
        self.parked.append((x, y))


@pytest.mark.parametrize("pointer, held, parked", [
    (None, False, []),               # nothing has moved it: Chrome hovers nothing
    ((400.0, 300.0), True, []),      # a hover step's menu stays open
    ((400.0, 300.0), False, [(400.0, 300.0)]),
])
def test_clear_pointer_parks_only_a_pointer_left_behind(pointer, held, parked):
    session = PointerStandIn(pointer, held)
    asyncio.run(BrowserSession._clear_pointer(session))
    assert session.parked == parked


def test_every_visible_step_clears_the_pointer_before_its_footage_starts():
    source = inspect.getsource(BrowserSession.act)
    clear = source.index("await self._clear_pointer()")
    assert source.rindex("if not action.offscreen", 0, clear) < clear
    # Before the step's first frame, not during it.
    assert clear < source.index("offset = time.monotonic() - self.t0")


def test_a_consent_click_and_a_hover_note_where_the_pointer_went():
    # The pointer a consent banner's button left behind is the one that sat
    # on TechTarget's ad. The click is in _click_labelled, which
    # dismiss_consent uses for both its refuse and its notice buttons.
    assert "self._note_pointer(button)" in inspect.getsource(BrowserSession._click_labelled)
    assert "self._click_labelled(" in inspect.getsource(BrowserSession.dismiss_consent)
    assert "self._note_pointer(target, held=True)" in inspect.getsource(BrowserSession.act)


AD_PAGE = """<body style="margin:0;font:16px sans-serif">
  <p style="position:absolute;left:40px;top:40px;width:500px">An article's quote, which is plain text.</p>
  <iframe id="ad" srcdoc="<a href='https://ads.example/click'>Buy</a>"
    style="position:absolute;left:600px;top:100px;width:300px;height:250px;border:0"></iframe>
  <script>addEventListener('mousemove', (e) => { window.lastMove = [e.clientX, e.clientY]; });</script>
</body>"""


def test_the_pointer_leaves_an_ad_frame_with_room_to_spare():
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:  # no browser in this environment
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(AD_PAGE)
            # A consent click left the pointer here; the ad loaded under it.
            await page.mouse.move(750, 225)
            under = await page.evaluate("() => document.elementFromPoint(750, 225).tagName")
            session = PointerStandIn((750.0, 225.0), page=page)
            session._park_pointer = types.MethodType(BrowserSession._park_pointer, session)
            await BrowserSession._clear_pointer(session)
            moved_to = await page.evaluate("() => window.lastMove")
            plain = await page.evaluate(_POINTER_PARK_JS, [200, 50])
            await browser.close()
            return under, session.pointer, moved_to, plain

    under, (x, y), moved_to, plain = asyncio.run(run())
    assert under == "IFRAME"
    # The mouse itself went there, clear of the ad by the margin.
    assert abs(moved_to[0] - x) <= 1 and abs(moved_to[1] - y) <= 1
    margin = 16
    assert not (600 - margin < x < 900 + margin and 100 - margin < y < 350 + margin)
    assert plain is None  # on plain text the pointer stays put


LOOSE_PAGE = """<body style="font:16px sans-serif">
  <h2 style="text-transform:uppercase">Dry&#8209;run &#8211; test&nbsp;a prompt</h2>
  <p>He said &#8220;keep it local&#8221;&#8230; and it was.</p>
  <p style="display:none">Hidden &mdash; copy</p>
</body>"""


def test_text_is_found_loosely_when_get_by_text_misses_it():
    """The Sluss film waited for "Dry-run — test a prompt" and never matched
    it (2026-10-28)."""
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:  # no browser in this environment
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page()
            await page.set_content(LOOSE_PAGE)
            session = types.SimpleNamespace(page=page)
            session._loose_text_target = types.MethodType(BrowserSession._loose_text_target, session)
            find = types.MethodType(BrowserSession._visible_text_target, session)
            found = {}
            for text in ("Dry-run — test a prompt", 'said "keep it local"...', "Hidden - copy"):
                target = await find(text)
                found[text] = await target.evaluate("(el) => el.tagName") if target else None
            exact = await find("Dry-run — test a prompt", exact=True)
            await browser.close()
            return found, exact

    found, exact = asyncio.run(run())
    assert found == {"Dry-run — test a prompt": "H2", 'said "keep it local"...': "P", "Hidden - copy": None}
    assert exact is None

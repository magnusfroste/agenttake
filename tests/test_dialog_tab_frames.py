"""What the certera.ink film (2026-11-10) taught the camera and the review.

Hermes shot it end to end, and four things needed a person afterwards: the
bulk-sign dialog filled a fifth of a dark frame; the close-up on the finished
diploma pushed in on the middle of the screen, because focus_ref "iframe" is no
ref; the "Open" link of a signed diploma opened a new tab that was recorded over
the session's page; and the template's line played before the template was drawn
in its iframe. The review called the dialog's fade-in a page that had not loaded.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from reel_studio import review
from reel_studio.camera import Camera
from reel_studio.engine import (
    MODAL_BOX_JS,
    SAME_TAB_JS,
    BrowserSession,
)

from test_measured_review import WHITE, categories, film, page


def two_steps(second_url: str):
    steps = [
        {"idx": 0, "action_type": "click", "offset_seconds": 1.0, "offscreen": False,
         "narration_text": "Open the studio.", "url": "https://app.test/studio"},
        {"idx": 1, "action_type": "click", "offset_seconds": 5.0, "offscreen": False,
         "narration_text": "Paste the class list.", "url": second_url},
    ]
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 4.0},
             {"kind": "step", "step": 1, "start": 4.0, "duration": 4.0}]
    return steps, parts


DARK = bytes([12]) * len(WHITE)


def test_a_dialog_fading_in_on_the_same_page_is_not_a_page_that_had_not_loaded():
    frames = film((page(), 5.0), (DARK, 1.0), (page(1), 2.0))
    steps, parts = two_steps("https://app.test/studio")
    findings = review.film_findings(frames, parts, steps)
    assert not categories(findings, "blank_flash")
    dim = categories(findings, "dim_frame")
    assert len(dim) == 1 and dim[0]["severity"] == "low" and dim[0]["location"] == "step[1]"


def test_an_empty_frame_on_a_new_page_is_still_a_blank_flash():
    frames = film((page(), 5.0), (DARK, 1.0), (page(1), 2.0))
    steps, parts = two_steps("https://app.test/signed")
    assert categories(review.film_findings(frames, parts, steps), "blank_flash")


DIALOG_PAGE = """<html><body style="margin:0;background:#111;height:100vh">
<div id="backdrop" style="position:fixed;inset:0;background:rgba(0,0,0,.8)"></div>
<div role="dialog" style="position:fixed;left:540px;top:260px;width:360px;height:300px;background:#222">
  <h2>Bulk Sign Diplomas</h2><textarea></textarea></div>
<div role="dialog" id="popover" style="position:absolute;left:20px;top:20px;width:200px;height:80px;background:#333">Website URL</div>
</body></html>"""

POPOVER_PAGE = """<html><body style="margin:0">
<div role="dialog" style="position:absolute;left:20px;top:20px;width:200px;height:80px">Website URL</div>
</body></html>"""

ARIA_MODAL_PAGE = """<html><body style="margin:0">
<section role="alertdialog" aria-modal="true" style="position:fixed;left:100px;top:100px;width:300px;height:200px">Sure?</section>
</body></html>"""


async def _browser(p):
    try:
        return await p.chromium.launch()
    except Exception as exc:  # no browser in this environment
        pytest.skip(f"chromium unavailable: {exc}")


def test_a_modal_dialog_is_found_and_a_popover_is_not():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1440, "height": 810})
            found = {}
            for name, html in (("backdrop", DIALOG_PAGE), ("popover", POPOVER_PAGE), ("aria", ARIA_MODAL_PAGE)):
                await tab.set_content(html)
                found[name] = await tab.evaluate(MODAL_BOX_JS)
            await browser.close()
            return found

    found = asyncio.run(run())
    assert found["backdrop"] == {"x": 540, "y": 260, "width": 360, "height": 300}
    assert found["popover"] is None
    assert found["aria"]["width"] == 300


def _stand_in(tab, width=1440, height=810):
    session = types.SimpleNamespace(
        page=tab, camera=Camera(width, height, 1.0), width=width, height=height,
        pending_shot=None, pending_ease=1.0, pending_facts=None, dialog_framed=False,
        refs={}, refs_stale=False, popups=[],
    )
    for name in ("_frame_dialog", "_frames_settled", "_adopt_popups", "set_shot", "_shot_facts",
                 "_frame_panels", "_visible_text_target", "_loose_text_target", "_text_box",
                 "_above_band", "_iframe_content_box"):
        setattr(session, name, types.MethodType(getattr(BrowserSession, name), session))
    return session


def test_the_camera_frames_a_small_dialog_and_goes_wide_when_it_closes():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1440, "height": 810})
            await tab.set_content(DIALOG_PAGE)
            session = _stand_in(tab)
            await session._frame_dialog()
            opened = session.pending_shot
            session.camera.move(1.0, *opened)
            session.pending_shot = None
            await session._frame_dialog()  # still open: the same frame, no new move
            again = session.pending_shot
            await tab.evaluate("() => document.querySelectorAll('[role=dialog], #backdrop').forEach(e => e.remove())")
            await session._frame_dialog()
            closed = session.pending_shot
            await browser.close()
            return opened, again, closed, session.dialog_framed

    opened, again, closed, framed = asyncio.run(run())
    zoom, cx, cy = opened
    assert 1.15 <= zoom <= 1.6
    # The dialog is in the frame, whole, above the subtitle band.
    view_w, view_h = 1440 / zoom, 810 / zoom
    left, top = cx - view_w / 2, cy - view_h / 2
    assert left <= 540 and left + view_w >= 900
    assert top <= 260 and 560 <= top + view_h * 0.83
    assert again is not None and again[0] == pytest.approx(zoom)
    assert closed == (1.0, None, None) and not framed


BULK_SIGN_PAGE = """<html><body style="margin:0;background:#121212;height:100vh">
<div style="position:fixed;inset:0;background:rgba(0,0,0,.8)"></div>
<div role="dialog" style="position:fixed;left:704px;top:304px;width:512px;height:472px;background:#1c1c1c">
  Bulk Sign Diplomas</div></body></html>"""


def test_the_bulk_sign_dialog_of_take_2_is_framed():
    """512x472 at 1920x1080: lifted the subtitle_lift way it came out at 1.14x
    and was left wide."""
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1920, "height": 1080})
            await tab.set_content(BULK_SIGN_PAGE)
            session = _stand_in(tab, 1920, 1080)
            await session._frame_dialog()
            await browser.close()
            return session.pending_shot

    zoom, cx, cy = asyncio.run(run())
    assert zoom >= 1.5
    view_h = 1080 / zoom
    top = cy - view_h / 2
    assert top <= 304 and 304 + 472 <= top + view_h * 0.85


def test_a_shot_the_director_framed_is_left_alone():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1440, "height": 810})
            await tab.set_content(DIALOG_PAGE)
            session = _stand_in(tab)
            session.camera.move(1.0, 2.0, 300.0, 300.0)
            await session._frame_dialog()
            await browser.close()
            return session.pending_shot

    assert asyncio.run(run()) is None


IFRAME_PAGE = """<html><body style="margin:0;background:#111">
<div style="height:120px">Studio</div>
<iframe id="preview" style="position:absolute;left:560px;top:160px;width:840px;height:600px;border:0"
  srcdoc="<body style='margin:0'><div class='wrapper' style='padding:20px;min-height:560px'><div class='diploma'
  style='width:800px;height:380px;background:#eee'><h1 style='margin:0'>Kestrel Bay Academy</h1></div></div></body>"></iframe>
</body></html>"""


def test_a_css_selector_frames_the_element_whole():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1440, "height": 810})
            await tab.set_content(IFRAME_PAGE)
            session = _stand_in(tab)
            await tab.wait_for_timeout(300)
            result = await session.set_shot("close", focus_ref="iframe")
            missing = await session.set_shot("close", focus_ref="no-such-ref")
            await browser.close()
            return result, session, missing

    result, session, missing = asyncio.run(run())
    frame = result["camera_frame"]
    # The diploma the iframe shows (580..1380 x 180..560), whole and above the
    # subtitles: not the middle of the screen at 2x, nor the whole frame at 1.07x.
    assert frame["x"] <= 580 and frame["x"] + frame["w"] >= 1380
    assert frame["y"] <= 180 and 560 <= frame["y"] + frame["h"] * 0.85
    assert 1.3 < result["camera_zoom"] < 2.0
    assert "no-such-ref" in missing["camera_note"]


def test_a_click_waits_until_its_iframe_is_drawn():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page()
            await tab.set_content(IFRAME_PAGE)
            await tab.evaluate("""() => setTimeout(() => {
                document.getElementById('preview').srcdoc = '<h1>Graduation 2026</h1><p>' + 'x'.repeat(500) + '</p>';
            }, 300)""")
            session = _stand_in(tab)
            await session._frames_settled()
            drawn = await tab.evaluate("() => document.getElementById('preview').contentDocument.body.innerText")
            await browser.close()
            return drawn

    assert "Graduation 2026" in asyncio.run(run())


def test_a_link_to_a_new_tab_opens_in_the_films_tab():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            context = await browser.new_context()
            await context.add_init_script(SAME_TAB_JS)

            async def serve(route):
                path = route.request.url.rsplit("/", 1)[-1]
                body = ('<a id="open" href="/diploma" target="_blank">Open</a>'
                        '<button id="win" onclick="window.open(\'/verify\')">Verify</button>'
                        if path == "list" else f"<h1>{path}</h1>")
                await route.fulfill(content_type="text/html", body=body)

            await context.route("http://certera.test/**", serve)
            tab = await context.new_page()
            await tab.goto("http://certera.test/list")
            await tab.click("#open")
            await tab.wait_for_url("**/diploma")
            await tab.goto("http://certera.test/list")
            await tab.click("#win")
            await tab.wait_for_url("**/verify")
            pages = len(context.pages)
            await browser.close()
            return pages

    assert asyncio.run(run()) == 1


def test_a_tab_opened_anyway_is_loaded_in_the_films_tab_and_closed():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            context = await browser.new_context()

            async def serve(route):
                await route.fulfill(content_type="text/html", body="<h1>diploma</h1>")

            await context.route("http://certera.test/**", serve)
            tab = await context.new_page()
            session = _stand_in(tab)
            context.on("page", lambda popup: session.popups.append(popup))
            await tab.set_content("<p>studio</p>")
            other = await context.new_page()
            await other.goto("http://certera.test/diploma/1")
            url = await session._adopt_popups()
            state = (url, tab.url, len(context.pages), session.refs_stale)
            await browser.close()
            return state

    url, here, pages, stale = asyncio.run(run())
    assert url == here == "http://certera.test/diploma/1"
    assert pages == 1 and stale

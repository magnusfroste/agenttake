"""A cookie banner's button is clicked; the page's own buttons with the same
label are not. On 2026-11-16 a rehearsal of a FlowWink film opened
/admin/approvals, took the approval card's "Reject" for a cookie banner's and
rejected the purchase order the film was about."""
import asyncio
import types

import pytest

from reel_studio.engine import BrowserSession
from reel_studio.storyboard import CONSENT_NOTICE, CONSENT_REFUSE

APPROVAL_CARD = """<html><body>
<main><h1>Approvals</h1>
<div class="card"><code>create_purchase_order</code> requires approval
  <button onclick="document.title='approved'">Approve</button>
  <button onclick="document.title='rejected'">Reject</button></div>
<div class="notice">Saved. <button onclick="document.title='dismissed'">Dismiss</button></div>
</main></body></html>"""

COOKIE_BANNER = """<html><body>
<main><h1>Approvals</h1>
<div class="card"><button onclick="document.title='rejected the card'">Reject</button></div></main>
<div style="position:fixed;bottom:0;left:0;right:0;height:120px;background:#eee">
  We use cookies to measure traffic.
  <button onclick="document.title='banner refused'">Reject all</button>
  <button>Accept all</button></div>
</body></html>"""

NAMED_BANNER = """<html><body>
<form><button type="button" onclick="document.title='form ok'">OK</button></form>
<div id="cookie-notice"><span>We keep only what the site needs.</span>
  <button onclick="document.title='notice closed'">OK</button></div>
</body></html>"""


async def _browser(p):
    try:
        return await p.chromium.launch()
    except Exception as exc:  # no browser in this environment
        pytest.skip(f"chromium unavailable: {exc}")


def _session(tab):
    session = types.SimpleNamespace(page=tab)

    async def note_pointer(_button):
        return None

    session._note_pointer = note_pointer
    for name in ("dismiss_consent", "_click_labelled"):
        setattr(session, name, types.MethodType(getattr(BrowserSession, name), session))
    return session


def _run(html):
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await _browser(p)
            tab = await browser.new_page(viewport={"width": 1280, "height": 720})
            await tab.set_content(html)
            await tab.evaluate("document.title = 'untouched'")
            clicked = await _session(tab).dismiss_consent(CONSENT_REFUSE, (), CONSENT_NOTICE)
            title = await tab.title()
            await browser.close()
            return clicked, title

    return asyncio.run(run())


def test_an_approval_cards_reject_is_never_taken_for_a_cookie_banner():
    clicked, title = _run(APPROVAL_CARD)
    assert clicked is None
    assert title == "untouched"


def test_a_cookie_banners_refuse_button_is_clicked_and_the_cards_is_not():
    clicked, title = _run(COOKIE_BANNER)
    assert clicked == "Reject all"
    assert title == "banner refused"


def test_a_notice_named_for_cookies_is_closed_and_a_forms_ok_is_not():
    clicked, title = _run(NAMED_BANNER)
    assert clicked == "OK"
    assert title == "notice closed"

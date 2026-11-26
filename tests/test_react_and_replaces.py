"""Two things from the Kotten film (skolappar.com, 2026-11-24).

A beat that taps a game ended with its line, and Lillstaden's oven card flew in
just as the beat was cut: react keeps the camera on the game after the last
press. Take 4 then replaced the published take 2 by a withdraw and an approve,
and the link already posted still showed take 2, marked as awaiting approval:
an approval can now name the film it replaces, and that film's link leads to
the new one."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from reel_studio import storyboard as boards

LILL = "https://lillstaden.example/"
OVEN = {"shot": "system", "label": "ugnen", "url": LILL, "line": "Kotten gräddar bullarna.",
        "taps": [{"x": 0.47, "y": 0.68, "to_x": 0.79, "to_y": 0.58, "ms": 1000}],
        "expect": "SCENES.bakery.bake > 0", "react": 2.5}


def test_react_keeps_the_camera_on_the_game_after_the_last_press():
    beats, items = boards.parse_storyboard([dict(OVEN), {**OVEN, "label": "utan", "line": "Utan.", "react": None}])
    assert not [item for item in items if item["severity"] == "fix"], items
    tap = next(op for op in boards.recipe(beats[0]) if op.get("action", {}).get("type") == "tap")
    assert tap["action"]["then_ms"] == 2500
    # The expect is still read after it: the presses and the answer.
    assert boards.recipe(beats[0])[-1]["op"] == "expect"
    plain = next(op for op in boards.recipe(beats[1]) if op.get("action", {}).get("type") == "tap")
    assert "then_ms" not in plain["action"]
    # The estimate counts it: the last press at four fifths of the line.
    line = boards.line_seconds(OVEN["line"], beats[0].get("language", "en"))
    assert boards.beat_seconds(beats[0]) == pytest.approx(max(line, line * 0.8 + 2.5))


def test_react_without_taps_is_flagged_and_too_long_refused():
    _, items = boards.parse_storyboard([{"shot": "system", "url": LILL, "line": "Bara en bild.", "react": 2}])
    assert [item["category"] for item in items if item["category"] == "react_not_used"] == ["react_not_used"]
    _, items = boards.parse_storyboard([{**OVEN, "react": 9}])
    assert any(item["severity"] == "fix" for item in items)


def test_a_director_sees_expect_and_react_in_the_beat_schema():
    properties = boards.beats_json_schema()["properties"]
    assert {"expect", "react"} <= set(properties)
    # Every field of a beat is in the schema an MCP client is given.
    assert set(boards.StoryBeat.model_fields) - set(properties) <= {"language"}


def _browser_session(page, directory):
    from reel_studio.engine import BrowserSession
    from reel_studio.render import RenderConfig

    return BrowserSession(
        "s-browser", "about:blank", 800, 600, "en-US-JennyNeural", "edge", None, RenderConfig(),
        directory, 0, None, None, None, page.context, page, None, time.monotonic(), refs_stale=False,
    )


def _in_browser(html, scenario):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:  # no browser in this environment
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 800, "height": 600})
            await page.set_content(html)
            try:
                return await scenario(page)
            finally:
                await browser.close()

    return asyncio.run(run())


def test_a_tap_step_holds_until_then_ms_after_its_last_press(tmp_path):
    from reel_studio.schema import Action

    async def scenario(page):
        session = _browser_session(page, tmp_path)
        point = {"x": 0.5, "y": 0.5}
        held, _ = await session.act(Action(type="tap", points=[point, point], ms=300, settle_ms=0, then_ms=1500))
        plain, _ = await session.act(Action(type="tap", points=[point], settle_ms=0))
        return held, plain, session.timeline

    held, plain, timeline = _in_browser("<canvas width=800 height=600></canvas>", scenario)
    # Two presses 300 ms apart, then 1.5 s on the page: about 1.9 s from the first.
    assert 1.7 < held["annotation_duration"] < 2.6, held
    assert timeline[0][2] == pytest.approx(held["annotation_duration"], abs=0.01)
    assert not plain.get("annotation_duration")


# ---- Replacing a published film --------------------------------------------

TOKEN = "test-admin-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "replace.db"))
    from reel_studio import server, store

    store.init_schema()
    app = server.BearerAuthMiddleware(server.mcp.streamable_http_app(), TOKEN)
    # Not started as a context: the MCP session manager runs once a process.
    return TestClient(app, follow_redirects=False), store


def _film(store, base: Path, session_id: str, title: str = "Kotten") -> str:
    folder = base / session_id
    store.create_session(session_id, "https://www.skolappar.com/", "en-US-JennyNeural", 1920, 1080,
                         str(folder), title=title)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "video.mp4").write_bytes(b"fake")
    store.finish_session(session_id, str(folder / "video.mp4"), None, 60.0)
    return session_id


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_a_new_take_replaces_the_published_one_and_its_link_follows(client, tmp_path):
    http, store = client
    take2, take4 = _film(store, tmp_path, "aaaa0002"), _film(store, tmp_path, "aaaa0004")
    assert http.post(f"/api/videos/{take2}/approve", headers=_auth()).json()["approved"] is True
    # Waiting for approval, take 4 offers the published take 2 to replace.
    page = http.get(f"/watch/{take4}").text
    assert f'data-replaces-for="{take4}"' in page and f'<option value="{take2}">' in page

    answer = http.post(f"/api/videos/{take4}/approve?replaces={take2}", headers=_auth()).json()
    assert answer == {"approved": True, "session_id": take4, "replaces": take2}
    listed = [film["id"] for film in http.get("/api/videos").json()]
    assert take4 in listed and take2 not in listed
    # The link in the post leads to take 4; ?stay=1 shows take 2 and says so.
    moved = http.get(f"/watch/{take2}")
    assert moved.status_code == 302 and moved.headers["location"] == f"/watch/{take4}"
    stay = http.get(f"/watch/{take2}?stay=1").text
    assert "Replaced." in stay and f'href="/watch/{take4}"' in stay

    # A take 5 replaces take 4: take 2's link goes on to take 5.
    take5 = _film(store, tmp_path, "aaaa0005")
    http.post(f"/api/videos/{take5}/approve?replaces={take4}", headers=_auth())
    assert http.get(f"/watch/{take2}").headers["location"] == f"/watch/{take5}"
    # Take 5 withdrawn: nothing published to lead to, each page shows itself.
    http.post(f"/api/videos/{take5}/approve?revoke=true", headers=_auth())
    assert http.get(f"/watch/{take4}").status_code == 200
    assert http.get(f"/watch/{take2}").status_code == 200
    # Published again by itself, take 2's link is its own.
    http.post(f"/api/videos/{take2}/approve", headers=_auth())
    assert http.get(f"/watch/{take2}").status_code == 200


def test_what_replaces_will_not_name(client, tmp_path):
    http, store = client
    take = _film(store, tmp_path, "bbbb0001")
    for query in (f"replaces={take}", "replaces=ffff9999", "replaces=../x", f"revoke=true&replaces={take}"):
        answer = http.post(f"/api/videos/{take}/approve?{query}", headers=_auth())
        assert answer.status_code == 400 and answer.json()["reason"] == "invalid_replaces", query
    assert http.post(f"/api/videos/{take}/approve?replaces={take}").status_code == 401


def test_a_tap_presses_a_button_by_its_text_wherever_the_page_put_it(tmp_path):
    """Tiokamrater shuffles its answers: a point hit a different one each
    time (skolappar video 1, 2026-11-26)."""
    from reel_studio.schema import Action, TapPoint

    page_html = ("<div style='display:flex;gap:8px;padding:40px'>"
                 + "".join(f"<button style='width:120px;height:60px' onclick=\"window.hit='{n}'\">{n}</button>"
                           for n in ("7", "10", "1", "9"))
                 + "</div>")

    async def scenario(page):
        session = _browser_session(page, tmp_path)
        pressed, _ = await session.act(Action(type="tap", points=[{"text": "1"}], settle_ms=0))
        hit = await page.evaluate("window.hit")
        missing, _ = await session.act(Action(type="tap", points=[{"text": "42"}], settle_ms=0))
        return pressed, hit, missing

    pressed, hit, missing = _in_browser(page_html, scenario)
    # Exactly "1", not "10".
    assert hit == "1", pressed
    assert not missing["ok"] and missing["error"]["type"] == "element_not_found"
    with pytest.raises(Exception):
        TapPoint(y=0.5)
    assert TapPoint(text="1").text == "1"


def test_the_engine_has_one_method_of_each_name():
    """A tap's text lookup was named as an older method and replaced it, so
    every beat framed on words failed (skolappar video 1 take 5, 2026-11-26)."""
    import ast
    import inspect
    from reel_studio import engine

    tree = ast.parse(inspect.getsource(engine))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names = [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            assert len(names) == len(set(names)), {name for name in names if names.count(name) > 1}

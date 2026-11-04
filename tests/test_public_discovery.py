from reel_studio.server import (
    build_llms_txt,
    build_sitemap,
    clean_display_title,
    page_shell,
    sanitize_public_url,
    watch_page,
)


def test_page_shell_contains_discovery_metadata():
    page = page_shell(
        "Test page",
        "<p>content</p>",
        "A useful description.",
        "/test",
        "https://example.test",
    )

    assert 'property="og:image"' in page
    assert 'name="twitter:card"' in page
    assert 'rel="canonical" href="https://example.test/test"' in page
    assert 'rel="icon" href="/favicon.svg?v=' in page


def test_llms_and_sitemap_builders():
    llms = build_llms_txt("https://example.test")
    assert "https://example.test/mcp" in llms
    assert "Authorization: Bearer <REEL_API_TOKEN>" in llms
    assert "scroll_to_text" in llms
    assert "update_step_narration" in llms

    sitemap = build_sitemap("https://example.test")
    for path in ("/", "/theater", "/backlog", "/bug_report", "/docs"):
        assert f"https://example.test{path}" in sitemap


def test_clean_display_title_sanitizes_urls_and_tokens():
    assert clean_display_title("https://app.example.com/admin/contacts?auth=secret123") == "Contacts"
    assert clean_display_title("https://saas.io/deals-pipeline") == "Deals Pipeline"
    assert clean_display_title("My App https://example.com/token=xyz") == "My App https://example.com/token=[REDACTED]"
    assert clean_display_title("") == "Untitled Demo"


def test_sanitize_public_url_strips_query_and_fragment():
    assert (
        sanitize_public_url(
            "https://app.example.com/admin#access_token=eyJhbGciOi123&refresh_token=abc"
        )
        == "https://app.example.com/admin"
    )
    assert (
        sanitize_public_url("https://app.example.com/login?next=/admin&token=xyz")
        == "https://app.example.com/login"
    )
    assert sanitize_public_url("not-a-url") == ""
    assert sanitize_public_url("") == ""
    assert sanitize_public_url(None) == ""


def test_watch_page_renders_theater_and_steps(tmp_path, monkeypatch):
    from reel_studio import store
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "test.db"))
    session_id = "a1b2c3d4"
    store.create_session(
        session_id, "https://crm.test/leads", "en-US-JennyNeural", 1920, 1080,
        str(tmp_path / session_id), title="Leads Overview Demo"
    )
    store.append_step(session_id, "click_and_wait", "button:create-lead", "https://crm.test/leads", "Leads", "Let's create a lead.", 2.5, 0.0, None, True, None)
    store.finish_session(session_id, str(tmp_path / session_id / "video.mp4"), None, 5.0)

    listed = store.list_finished_sessions()
    assert listed[0]["title"] == "Leads Overview Demo"

    html = watch_page(session_id, "https://example.test")
    assert html is not None
    assert "Leads Overview Demo" in html
    assert "Let&#x27;s create a lead." in html or "Let's create a lead." in html
    assert f"/videos/{session_id}/video.mp4" in html
    assert "/theater" in html


def test_watch_page_tolerates_legacy_step_types(monkeypatch):
    from reel_studio import store
    session_id = "deadbeef"
    monkeypatch.setattr(
        store,
        "get_session",
        lambda requested_id: {
            "id": requested_id,
            "status": "finished",
            "title": "Legacy Demo",
            "start_url": "https://crm.test/auth",
            "duration_seconds": 5.0,
            "finished_at": "2026-08-22T00:00:00+00:00",
            "steps": [{
                "idx": 0,
                "action_type": 123,
                "target": 456,
                "narration_text": "Continue",
                "offset_seconds": "2.5",
            }],
        },
    )

    html = watch_page(session_id, "https://example.test")

    assert html is not None
    assert "Step 1: 123 456" in html
    assert "2.5s" in html


def test_watch_and_theater_pages_offer_a_share_link(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "share.db"))
    from reel_studio import server, store

    store.init_schema()
    store.create_session("abc123", "https://example.com", "en-US-JennyNeural", 1920, 1080,
                         str(tmp_path), "edge", None, None, "Demo", "", "#1f2a44", "", "Learn more", "none")
    store.finish_session("abc123", str(tmp_path / "video.mp4"), None, 12.0)
    watch = server.watch_page("abc123")
    assert 'data-share-path="/watch/abc123"' in watch
    assert "navigator.clipboard.writeText" in watch
    assert 'data-share-path="/watch/abc123"' in server.theater_page()


def test_watch_page_shares_its_own_title_and_poster(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "poster.db"))
    from reel_studio import server, store
    session_id = "beef01"
    out = tmp_path / session_id
    out.mkdir()
    video = out / "video.mp4"
    # A 4:5 clip: the poster must still come out 1200x630.
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=720x900:rate=10",
                    "-t", "3", "-pix_fmt", "yuv420p", str(video)], check=True)
    store.create_session(session_id, "https://panel.example.eu", "en-US-JennyNeural", 720, 900, str(out))
    store.finish_session(session_id, str(video), None, 3.0)

    before = server.watch_page(session_id, "https://reel.test")
    assert "· AgentTake" in before
    assert f'/videos/{session_id}/poster.jpg?v=' in before
    assert 'content="https://reel.test/og.png"' not in before

    store.update_watch_page(session_id, page_title="AgentHotel — your agents, always on",
                            page_description="Any agent, any model, on your own server.", poster_at=1.5)
    session = store.get_session(session_id)
    poster = server.ensure_poster(session, force=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(poster)],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "1200,630"
    assert server.poster_seconds(session) == 1.5

    page = server.watch_page(session_id, "https://reel.test")
    assert '<meta property="og:title" content="AgentHotel — your agents, always on">' in page
    assert 'content="Any agent, any model, on your own server."' in page
    assert store.list_finished_sessions()[0]["page_title"] == "AgentHotel — your agents, always on"
    # The title card is untouched: page_title is only the page.
    assert session["title"] == ""


def test_public_video_url_follows_the_current_address(monkeypatch):
    from reel_studio import server
    monkeypatch.setenv("REEL_PUBLIC_BASE_URL", "https://take.example.com/")
    assert server.public_video_url("abc") == "https://take.example.com/videos/abc/video.mp4"
    monkeypatch.delenv("REEL_PUBLIC_BASE_URL")
    assert server.public_video_url("abc") is None


def test_the_brand_mark_is_the_at_monogram_and_its_files_are_versioned():
    from reel_studio import server

    assert ">AT</text>" in server.FAVICON_SVG
    assert server.ASSET_VERSION in server.favicon_png_path().name
    assert server.ASSET_VERSION in server.og_image_path().name


def test_a_rerender_gets_a_new_video_url_and_the_link_is_cached_briefly(tmp_path, monkeypatch):
    # Cloudflare kept serving the old MP4 after a rerender: the URL never
    # changed and the response had no Cache-Control.
    import os
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("REEL_PUBLIC_BASE_URL", "https://take.example.com")
    from starlette.testclient import TestClient
    from reel_studio import server, store

    session_id = "cafe01"
    out = tmp_path / session_id
    out.mkdir()
    video = out / "video.mp4"
    video.write_bytes(b"first render")
    os.utime(video, (1_000_000, 1_000_000))
    store.init_schema()
    store.create_session(session_id, "https://example.com", "en-US-JennyNeural", 1920, 1080, str(out))
    store.finish_session(session_id, str(video), f"https://take.example.com/videos/{session_id}/video.mp4", 5.0)

    first = server.public_video_url(session_id)
    assert first == f"https://take.example.com/videos/{session_id}/video.mp4?v=1000000"
    video.write_bytes(b"second render")
    os.utime(video, (2_000_000, 2_000_000))
    second = server.public_video_url(session_id)
    assert second != first and second.endswith("?v=2000000")
    assert store.get_session(session_id)["video_url"].endswith("video.mp4")  # stored bare
    import asyncio
    assert asyncio.run(server.get_session(session_id))["video_url"] == second
    assert asyncio.run(server.list_sessions())[0]["video_url"] == second
    assert f'src="/videos/{session_id}/video.mp4?v=2000000"' in server.watch_page(session_id)

    client = TestClient(server.BearerAuthMiddleware(server.mcp.streamable_http_app(), "secret"))
    versioned = client.get(f"/videos/{session_id}/video.mp4?v=2000000")
    assert versioned.status_code == 200 and versioned.content == b"second render"
    assert "immutable" in versioned.headers["cache-control"]
    bare = client.get(f"/videos/{session_id}/video.mp4")
    assert bare.headers["cache-control"] == "public, max-age=60"
    feed = client.get("/api/videos").json()
    assert feed[0]["video_url"] == f"/videos/{session_id}/video.mp4?v=2000000"


def _own_store(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "reel-studio.db"))
    monkeypatch.delenv("REEL_GITHUB_TOKEN", raising=False)


def test_a_backlog_item_can_be_proposed_upstream_with_one_click(tmp_path, monkeypatch):
    """Each instance keeps its own backlog, so what agents ask for never
    reached the project unless a central instance ran. The roadmap they share
    is the upstream issue tracker; an item gets there with a prefilled link
    the instance owner reads first (2026-11-04)."""
    import asyncio
    import html

    from reel_studio import server

    _own_store(tmp_path, monkeypatch)
    item = asyncio.run(server.submit_backlog(
        "Hold a result longer", "edit_step hold_seconds tops out at 20 s", "friction", "low",
    ))
    assert item["issue_url"] is None and "upstream_error" not in item
    assert item["propose_url"].startswith("https://github.com/magnusfroste/agenttake/issues/new?")
    assert "title=Hold+a+result+longer" in item["propose_url"]
    assert "labels=from-take" in item["propose_url"]
    assert "Severity%3A+low" in item["propose_url"]
    # Without a token nothing is sent anywhere.
    assert asyncio.run(server.file_upstream_issue(item)) == (None, None)

    page = server.backlog_page("https://example.test")
    assert "Propose upstream" in page and html.escape(item["propose_url"]) in page
    assert 'href="https://github.com/magnusfroste/agenttake/issues"' in page
    assert 'href="https://github.com/magnusfroste/agenttake" rel="noopener">GitHub' in page  # the nav


def test_with_a_token_the_item_is_filed_upstream_and_a_refusal_keeps_it_local(tmp_path, monkeypatch):
    import asyncio

    from reel_studio import server, store

    _own_store(tmp_path, monkeypatch)
    posted = []

    async def accepted(item):
        posted.append(item["title"])
        return "https://github.com/magnusfroste/agenttake/issues/7", None

    monkeypatch.setattr(server, "file_upstream_issue", accepted)
    item = asyncio.run(server.submit_backlog("Mux dies on finish", "SIGABRT in xfade", "bug", "high"))
    assert posted == ["Mux dies on finish"]
    assert item["issue_url"].endswith("/issues/7") and "propose_url" not in item
    assert store.list_backlog(category="bug")[0]["issue_url"].endswith("/issues/7")
    page = server.bug_report_page()
    assert "Filed upstream" in page and "Propose upstream" not in page

    async def refused(item):
        return None, "GitHub answered 401"

    monkeypatch.setattr(server, "file_upstream_issue", refused)
    kept = asyncio.run(server.submit_backlog("Second", "", "friction", "normal"))
    assert kept["issue_url"] is None and kept["upstream_error"] == "GitHub answered 401"
    assert kept["propose_url"].startswith("https://github.com/magnusfroste/agenttake/issues/new?")


def test_every_public_page_renders(tmp_path, monkeypatch):
    """The pages an installer and a directing agent read must answer, not 500.
    /docs had answered 500 since 2026-08-30: its f-string carried an unescaped
    ``{session_id}`` in the delete endpoint's HTTP line, and no test opened it."""
    _own_store(tmp_path, monkeypatch)
    from starlette.testclient import TestClient
    from reel_studio import server, store

    store.init_schema()
    client = TestClient(server.BearerAuthMiddleware(server.mcp.streamable_http_app(), "secret"))
    for path in ("/", "/docs", "/theater", "/backlog", "/bug_report", "/llms.txt"):
        answer = client.get(path)
        assert answer.status_code == 200, (path, answer.status_code)
    docs = client.get("/docs").text
    assert "DELETE /api/videos/{session_id}?confirm=true" in docs
    assert '{"action": ..., "narration": ...}' in docs

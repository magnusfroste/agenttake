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
    assert 'rel="icon" href="/favicon.svg"' in page


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

"""A person hears the voices before a film is shot. The Kotten film's voice
(2026-11-24) was chosen by ear, twice: Knox, a warmer Knox and Baron, then
four ElevenLabs models reading Swedish. Each round was an operator calling
the API by hand; audition_voice gives the director a page to send instead."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "audition.db"))
    monkeypatch.setenv("REEL_PUBLIC_BASE_URL", "https://take.example")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    from reel_studio import server, store

    store.init_schema()
    spoken = []

    async def synthesize(text, voice, folder, provider=None):
        spoken.append((text, voice, provider))
        path = Path(folder) / f"narration-{len(spoken)}.mp3"
        path.write_bytes(b"ID3" + b"\0" * 64)
        return path

    async def left():
        return 10_000

    monkeypatch.setattr(server, "synthesize", synthesize)
    monkeypatch.setattr(server, "elevenlabs_characters_left", left)
    monkeypatch.setattr(server, "probe_duration", lambda path: 3.24)
    server.spoken = spoken
    return server


KNOX = "dPah2VEoifKnZT37774q?stability=0.35&style=0.35"


def test_each_voice_reads_the_same_text_onto_one_page(server, tmp_path):
    text = "En stund tillsammans. Varje dag, fram till jul."
    result = asyncio.run(server.audition_voice(text, [KNOX, KNOX + "&language=sv&model=eleven_turbo_v2_5"]))
    assert result["ok"] and result["provider"] == "elevenlabs" and result["language"] == "sv"
    assert [voice for _, voice, _ in server.spoken] == [KNOX, KNOX + "&language=sv&model=eleven_turbo_v2_5"]
    assert all(said == text for said, _, _ in server.spoken)
    first, second = result["clips"]
    page = result["page"]
    assert page == f"https://take.example/auditions/{result['audition_id']}"
    assert first["url"] == f"{page}/1.mp3" and first["seconds"] == 3.2
    # Swedish text, and the first voice leaves the language to the model.
    assert "language=sv" in first["note"] and "note" not in second
    # Behind the token, as it runs: a person without one listens.
    client = TestClient(server.BearerAuthMiddleware(server.mcp.streamable_http_app(), "secret"))
    html = client.get(f"/auditions/{result['audition_id']}").text
    assert "Which voice?" in html and text in html and 'src="' + result["audition_id"] + '/2.mp3"' in html
    assert "language=sv&amp;model=eleven_turbo_v2_5" in html
    clip = client.get(f"/auditions/{result['audition_id']}/1.mp3")
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mpeg"
    assert client.get(f"/auditions/{result['audition_id']}/9.mp3").status_code == 404
    assert client.get("/auditions/../../etc").status_code in (401, 404)
    assert client.get(f"/auditions/{result['audition_id']}/audition.json").status_code == 401


@pytest.mark.parametrize("text, voices, kind", [
    ("", [KNOX], "invalid_text"),
    ("x" * 301, [KNOX], "invalid_text"),
    ("Hej.", [], "invalid_voices"),
    ("Hej.", [KNOX] * 5, "invalid_voices"),
    ("Hej.", ["knox?warmth=1"], "invalid_voice"),
    ("Hej.", ["knox?model=eleven_v9"], "invalid_voice"),
])
def test_what_it_refuses_before_anything_is_bought(server, text, voices, kind):
    assert asyncio.run(server.audition_voice(text, voices))["error"]["type"] == kind
    assert server.spoken == []


def test_it_will_not_overspend_the_voice_quota(server, monkeypatch):
    async def left():
        return 30

    monkeypatch.setattr(server, "elevenlabs_characters_left", left)
    result = asyncio.run(server.audition_voice("Ge er AI-agent adressen.", [KNOX, KNOX + "&language=sv"]))
    assert result["error"]["type"] == "tts_quota" and server.spoken == []


def test_one_failing_voice_leaves_the_others(server, monkeypatch):
    async def synthesize(text, voice, folder, provider=None):
        if "bad" in voice:
            raise RuntimeError("ElevenLabs request failed (404): voice not found")
        path = Path(folder) / "n.mp3"
        path.write_bytes(b"ID3")
        return path

    monkeypatch.setattr(server, "synthesize", synthesize)
    result = asyncio.run(server.audition_voice("Hello there.", ["bad", KNOX]))
    assert result["ok"] and [clip["ok"] for clip in result["clips"]] == [False, True]
    assert "404" in result["clips"][0]["error"] and "url" not in result["clips"][0]
    assert result["language"] == "en" and "note" not in result["clips"][1]


def test_only_the_latest_auditions_are_kept(server, tmp_path):
    for n in range(server.AUDITIONS_KEPT + 2):
        asyncio.run(server.audition_voice(f"Line {n}.", [KNOX]))
    assert len(list(tmp_path.glob("audition-*"))) == server.AUDITIONS_KEPT


def test_a_swedish_line_without_a_or_o_is_still_swedish():
    from reel_studio import storyboard as boards

    for line in ["En stund tillsammans. Varje dag, fram till jul.", "Vad vill ni bygga?"]:
        assert boards.film_language([{"line": line}]) == "sv", line
    for line in ["Hello there.", "Build an app together with your child.", "Till the end of the day."]:
        assert boards.film_language([{"line": line}]) == "en", line

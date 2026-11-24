"""A dark voice read warm needed other settings than its default (Knox Dark
for skolappar.com, 2026-11-24). They ride in the voice string, so a session
stores, reuses and rerenders them with the voice."""
import asyncio
import json

import pytest

from reel_studio import tts
from reel_studio.tts import TTSProviderError, elevenlabs_voice


def test_a_bare_id_has_no_settings():
    assert elevenlabs_voice("dPah2VEoifKnZT37774q") == ("dPah2VEoifKnZT37774q", {})


def test_settings_follow_the_id():
    voice_id, settings = elevenlabs_voice("dPah2VEoifKnZT37774q?stability=0.35&style=0.35&use_speaker_boost=true")
    assert voice_id == "dPah2VEoifKnZT37774q"
    assert settings == {"stability": 0.35, "style": 0.35, "use_speaker_boost": True}


@pytest.mark.parametrize("voice", ["abc?warmth=1", "abc?style=2", "abc?speed=fast", "?style=0.3",
                                   "abc?use_speaker_boost=maybe"])
def test_a_wrong_setting_is_refused_before_any_request(voice):
    with pytest.raises(TTSProviderError):
        elevenlabs_voice(voice)


def test_the_request_names_the_id_and_sends_the_settings(monkeypatch, tmp_path):
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"mp3"

    def urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    monkeypatch.setattr(tts.urllib.request, "urlopen", urlopen)
    asyncio.run(tts._synthesize_elevenlabs("Hej.", "knox?stability=0.35&style=0.35", tmp_path / "a.mp3"))
    assert seen["url"].endswith("/text-to-speech/knox")
    assert seen["body"]["voice_settings"] == {"stability": 0.35, "style": 0.35}
    asyncio.run(tts._synthesize_elevenlabs("Hej.", "knox", tmp_path / "b.mp3"))
    assert "voice_settings" not in seen["body"]

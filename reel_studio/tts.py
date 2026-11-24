"""Pluggable text-to-speech support."""

import asyncio
import json
import os
import shutil
from pathlib import Path
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qsl

import edge_tts


class TTSProviderError(RuntimeError):
    """Raised when a configured TTS provider cannot synthesize audio."""


def normalize_provider(provider: str | None) -> str:
    selected = (provider or os.environ.get("REEL_TTS_PROVIDER", "edge")).strip().lower()
    if selected not in {"edge", "elevenlabs"}:
        raise TTSProviderError(f"Unknown TTS provider: {selected}")
    return selected


def validate_provider(provider: str) -> None:
    provider = normalize_provider(provider)
    if provider == "elevenlabs" and not os.environ.get("ELEVENLABS_API_KEY"):
        raise TTSProviderError(
            "ELEVENLABS_API_KEY must be set when TTS provider is elevenlabs"
        )


# ElevenLabs voice settings an id may carry after "?", with their ranges. A
# dark voice read warm needed lower stability and more style than its default
# (Knox Dark at stability 0.35, style 0.35 for skolappar.com, 2026-11-24).
VOICE_SETTINGS: dict[str, tuple[float, float]] = {
    "stability": (0.0, 1.0),
    "similarity_boost": (0.0, 1.0),
    "style": (0.0, 1.0),
    "speed": (0.7, 1.2),
}


# The model and the language ride in the voice string too. An English voice
# reading Swedish kept its accent under eleven_multilingual_v2, which guesses
# the language from the text (the skolappar.com Kotten film, 2026-11-24);
# language=sv sends language_code, which every model below accepted then.
ELEVENLABS_MODELS = (
    "eleven_multilingual_v2",
    "eleven_v3",
    "eleven_turbo_v2_5",
    "eleven_flash_v2_5",
)
DEFAULT_ELEVENLABS_MODEL = ELEVENLABS_MODELS[0]


def elevenlabs_request(voice: str) -> tuple[str, dict]:
    """(voice id, the request's fields besides text) from an ElevenLabs voice.

    "id?stability=0.35&style=0.35&language=sv&model=eleven_v3" names the
    voice, its voice_settings, an ISO 639-1 language_code and the model.
    Everything rides in the voice string so a session stores, reuses and
    rerenders it with the voice: a line spoken otherwise is another clip."""
    voice_id, _, query = (voice or "").partition("?")
    settings: dict = {}
    fields: dict = {"model_id": DEFAULT_ELEVENLABS_MODEL}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "model":
            if value not in ELEVENLABS_MODELS:
                raise TTSProviderError(f"Unknown ElevenLabs model {value!r}: use {', '.join(ELEVENLABS_MODELS)}")
            fields["model_id"] = value
            continue
        if key == "language":
            code = value.strip().lower()
            if not (len(code) == 2 and code.isascii() and code.isalpha()):
                raise TTSProviderError("language is a two-letter ISO 639-1 code, such as sv or en")
            fields["language_code"] = code
            continue
        if key == "use_speaker_boost":
            if value.lower() not in {"true", "false", "1", "0"}:
                raise TTSProviderError("use_speaker_boost is true or false")
            settings[key] = value.lower() in {"true", "1"}
            continue
        if key not in VOICE_SETTINGS:
            raise TTSProviderError(
                f"Unknown voice setting {key!r}: use {', '.join(VOICE_SETTINGS)}, "
                "use_speaker_boost, language or model"
            )
        low, high = VOICE_SETTINGS[key]
        try:
            number = float(value)
        except ValueError:
            raise TTSProviderError(f"Voice setting {key} must be a number from {low} to {high}") from None
        if not low <= number <= high:
            raise TTSProviderError(f"Voice setting {key} must be a number from {low} to {high}")
        settings[key] = number
    if not voice_id.strip():
        raise TTSProviderError("An ElevenLabs voice needs its id before any settings")
    if settings:
        fields["voice_settings"] = settings
    return voice_id.strip(), fields


def elevenlabs_voice(voice: str) -> tuple[str, dict]:
    """(voice id, voice_settings) from "id" or "id?stability=0.35&style=0.35"."""
    voice_id, fields = elevenlabs_request(voice)
    return voice_id, fields.get("voice_settings", {})


async def _synthesize_elevenlabs(text: str, voice: str, path: Path) -> Path:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise TTSProviderError(
            "ELEVENLABS_API_KEY must be set when TTS provider is elevenlabs"
        )
    voice_id, fields = elevenlabs_request(voice)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload: dict = {"text": text, **fields}
    body = json.dumps(payload).encode()

    def request_audio() -> bytes:
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": api_key,
                "User-Agent": "agenttake",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise TTSProviderError(
                f"ElevenLabs request failed ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TTSProviderError(f"ElevenLabs request failed: {exc}") from exc

    audio = await asyncio.to_thread(request_audio)
    path.write_bytes(audio)
    return path


def elevenlabs_configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


async def elevenlabs_characters_left() -> int | None:
    """Characters the ElevenLabs account has left this period, or None when it
    cannot tell (no key, the subscription endpoint did not answer).

    A free-tier account ran out two beats into the Agentanbud film and the
    take died with seven beats unspoken (2026-10-30): the voice check before
    recording costs one word, so it passed.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        return None

    def fetch() -> dict:
        request = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key, "Accept": "application/json", "User-Agent": "agenttake"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode())

    try:
        data = await asyncio.to_thread(fetch)
        return max(0, int(data["character_limit"]) - int(data["character_count"]))
    except Exception:
        return None


async def list_elevenlabs_voices() -> list[dict]:
    """The voices on the ElevenLabs account the host's key belongs to."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise TTSProviderError("ELEVENLABS_API_KEY is not set on this AgentTake")

    def fetch() -> dict:
        request = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key, "Accept": "application/json", "User-Agent": "agenttake"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise TTSProviderError(f"ElevenLabs voices request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise TTSProviderError(f"ElevenLabs voices request failed: {exc}") from exc

    data = await asyncio.to_thread(fetch)
    voices = []
    for voice in data.get("voices", []):
        labels = voice.get("labels") or {}
        voices.append({
            "voice": voice.get("voice_id"),
            "name": voice.get("name"),
            "gender": (labels.get("gender") or "").capitalize(),
            "accent": labels.get("accent") or "",
            "age": labels.get("age") or "",
            "description": labels.get("description") or labels.get("descriptive") or "",
            "use_case": labels.get("use_case") or "",
            "category": voice.get("category") or "",
        })
    return voices


async def synthesize(
    text: str,
    voice: str,
    output_dir: Path,
    provider: str | None = None,
) -> Path:
    """Synthesize text with the selected provider and return an MP3 path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"narration-{uuid.uuid4().hex}.mp3"
    selected = normalize_provider(provider)
    # A line some session already has in this voice is copied, not bought
    # again (store.spoken_clip).
    from . import store

    try:
        spoken = await asyncio.to_thread(store.spoken_clip, text, voice, selected)
    except Exception:
        spoken = None
    if spoken is not None:
        await asyncio.to_thread(shutil.copyfile, spoken, path)
        return path
    validate_provider(selected)
    if selected == "elevenlabs":
        await _synthesize_elevenlabs(text, voice, path)
    else:
        await _synthesize_edge(text, voice, path)
    return path


# Microsoft's free endpoint refuses a share of requests with 403 for minutes at
# a time from some hosts — it held up an agent's take for seven minutes and
# failed a rerender outright (2026-10-10); the next request often succeeds.
EDGE_ATTEMPTS = 5
EDGE_BACKOFF_SECONDS = (2, 4, 8, 16)


async def _synthesize_edge(text: str, voice: str, path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(EDGE_ATTEMPTS):
        try:
            communicator = edge_tts.Communicate(text, voice)
            await communicator.save(str(path))
            if path.is_file() and path.stat().st_size > 0:
                return
            last_error = TTSProviderError("Edge returned no audio")
        except Exception as exc:  # aiohttp's 403, a dropped socket, no audio
            last_error = exc
        path.unlink(missing_ok=True)
        if attempt < len(EDGE_BACKOFF_SECONDS):
            await asyncio.sleep(EDGE_BACKOFF_SECONDS[attempt])
    raise TTSProviderError(
        f"Edge TTS failed {EDGE_ATTEMPTS} times; the free endpoint is refusing "
        f"this host for now. Last error: {last_error}"
    )

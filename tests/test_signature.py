"""The signature: the house style every film an instance makes carries. A
web agency is hired for a look nobody else has, and so is a director; without
a signature every AgentTake film looked the same, so the signature is one
document the instance owns, the director reads at the end of the handbook,
and every card, caption and source card is set from."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from reel_studio import signature as sig
from reel_studio import render


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A fresh instance whose signature file is under tmp_path."""
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "reel-studio.db"))
    monkeypatch.delenv("REEL_SIGNATURE", raising=False)
    sig._cached.cache_clear()
    yield tmp_path
    sig._cached.cache_clear()


HERMES = json.loads((Path(__file__).resolve().parent.parent / "docs" / "signatures" / "hermes.json").read_text())


def test_without_a_file_the_instance_has_the_look_it_always_had(house):
    current = sig.current()
    assert current.is_default
    assert current.accent == "#1f2a44" and current.ground == "#1f2a44"
    assert current.display_font == "DejaVu Sans"
    assert sig.load() == (sig.DEFAULT, [])


def test_the_example_signature_parses_whole():
    parsed, problems = sig.parse(HERMES)
    assert problems == []
    assert parsed.name == "hermes" and parsed.ground == "#0B1B2B" and parsed.paper == "#F6F1E7"
    assert parsed.signature_line.startswith("Directed by Hermes")
    assert len(parsed.tone) == 8 and not parsed.is_default


def test_a_bad_colour_or_font_keeps_the_default_and_is_named():
    parsed, problems = sig.parse({"name": "Acme Co!", "accent": "red", "ink": "0B1B2B",
                                  "display_font": "<script>", "tone": "One.\nTwo.\n"})
    assert parsed.name == "acme-co"
    assert parsed.accent == "#1f2a44" and parsed.ink == "#0B1B2B"
    assert parsed.display_font == "DejaVu Sans"
    assert parsed.tone == ("One.", "Two.")
    assert any("accent" in problem for problem in problems)
    assert any("display_font" in problem for problem in problems)


def test_saving_bumps_the_version_and_keeps_a_changelog(house):
    first, _ = sig.save(HERMES, "first")
    assert first.version == 1 and sig.current().name == "hermes"
    second, problems = sig.save({**HERMES, "accent": "#112233"}, "a bluer accent")
    assert problems == []
    assert second.version == 2 and second.accent == "#112233"
    assert [entry["note"] for entry in second.changelog] == [
        "The Hermes signalement: ink, gold, Inter and Plex Mono, signed and labelled.", "first", "a bluer accent",
    ]
    # The file is the truth and is read again on the next look.
    assert json.loads((house / "signature.json").read_text())["version"] == 2
    assert sig.current().version == 2


def test_the_director_reads_the_house_style_at_the_end_of_the_handbook(house):
    from reel_studio import server

    sig.save(HERMES, "set")
    guide = asyncio.run(server.director_guide())
    assert "## The house style (signature)" in guide
    assert "signs as **hermes**" in guide
    assert "Address the viewer as you." in guide
    assert "Directed by Hermes" in guide
    # And the tools tell the same story.
    shown = asyncio.run(server.get_signature())
    assert shown["name"] == "hermes" and shown["is_default"] is False and "problems" not in shown
    changed = asyncio.run(server.set_signature({**HERMES, "paper": "paper"}, "typo"))
    assert changed["version"] == 2 and changed["problems"]


def test_the_accent_a_film_does_not_name_is_the_house_accent(house):
    from reel_studio import server

    assert server._render_config("", "", "", "", "", "none").accent == "#1f2a44"
    sig.save(HERMES, "set")
    assert server._render_config("", "", "", "", "", "none").accent == "#E3A23B"
    assert server._render_config("", "", "#123456", "", "", "none").accent == "#123456"


def test_cards_and_captions_are_set_in_the_house_type(house):
    sig.save(HERMES, "set")
    filters = render.card_filters(1280, 720, [("Learn more", 40), ("sluss.eu", 28, "/tmp/mono.ttf")],
                                  "white", False, rule=True,
                                  small_print=["Directed and narrated by an AI agent", "Directed by Hermes"])
    joined = "\n".join(filters)
    assert "text='Learn more'" in joined and "fontfile=/tmp/mono.ttf:text='sluss.eu'" in joined
    assert "drawbox" in joined and "#E3A23B" in joined  # the rule under the title
    assert "Directed by Hermes" in joined and "narrated by an AI agent" in joined
    assert "Default,Inter," in render.subtitles_ass([(0.0, 1.0, "Hej")], 1280, 720)


def test_source_cards_and_mid_film_cards_carry_the_signature(house):
    import urllib.parse
    from reel_studio import storyboard

    def page(url: str) -> str:
        return urllib.parse.unquote(url.split(",", 1)[1])

    before = page(storyboard.source_card_page("Quoted words", "https://example.org/a"))
    assert "--accent:#c8102e" in before  # the documentary red the default keeps
    sig.save(HERMES, "set")
    after = page(storyboard.source_card_page("Quoted words", "https://example.org/a"))
    assert "--accent:#E3A23B" in after and '"IBM Plex Mono"' in after and '"Inter"' in after
    assert 'font-family:"Inter"' in page(storyboard.card_page("A card"))


def test_a_missing_font_falls_back_so_a_card_always_renders():
    sig.font_file.cache_clear()
    assert sig.font_file("No Such Face 9000") == sig.FALLBACK_FONT
    if shutil.which("fc-match"):
        assert Path(sig.font_file("DejaVu Sans")).is_file()


media = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not Path(sig.FALLBACK_FONT).is_file(),
    reason="ffmpeg and a font are required",
)


@media
def test_the_closing_card_renders_with_the_house_sign_off(house, tmp_path):
    sig.save(HERMES, "set")
    out = tmp_path / "outro.mp4"
    config = render.RenderConfig(cta_url="https://sluss.eu", cta_text="Measure first", closing_qr=False)
    render._closing_card(out, 640, 360, config, None, tmp_path)
    assert out.is_file() and out.stat().st_size > 0
    assert render.probe_video_size(out) == (640, 360)


def _capture_card(monkeypatch):
    seen = []
    monkeypatch.setattr(render, "_card", lambda *args, **kwargs: seen.append((args, kwargs)))
    return seen


def test_a_brand_card_carries_the_house_rule_and_only_a_house_has_one(house, monkeypatch, tmp_path):
    seen = _capture_card(monkeypatch)
    render.render_card_segment({"text": "Measure first.", "background": "brand"}, 3.0, tmp_path / "a.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["rule"] is False
    sig.save(HERMES, "set")
    render.render_card_segment({"text": "Measure first.", "background": "brand"}, 3.0, tmp_path / "b.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["rule"] is True and seen[-1][0][3] == "#0B1B2B"
    render.render_card_segment({"text": "On a colour.", "background": "#334455"}, 3.0, tmp_path / "c.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["rule"] is False


def test_the_title_card_of_a_segmented_film_is_set_from_the_signature(house, monkeypatch, tmp_path):
    sig.save(HERMES, "set")
    seen = _capture_card(monkeypatch)
    config = render.RenderConfig(title="Measure first", accent="#1f2a44")
    render._title_card(tmp_path / "intro.mp4", 640, 360, config, None)
    args, kwargs = seen[-1]
    assert args[3] == "#0B1B2B" and kwargs["rule"] is True and kwargs["text_colour"] == "#F6F1E7"


def test_a_solid_house_sets_title_and_closing_cards_on_ink(house, monkeypatch, tmp_path):
    stills = []
    monkeypatch.setattr(render, "_still", lambda video, at_end, out: stills.append(out) or out)
    monkeypatch.setattr(render, "_download_image", lambda url, out: out)
    first, last = tmp_path / "first.mp4", tmp_path / "last.mp4"

    assert all(render.card_backgrounds(render.RenderConfig(), first, last, tmp_path))
    assert len(stills) == 2  # no signature: the film's own frames, as before

    sig.save(HERMES, "set")
    assert sig.current().cards == "solid"
    assert render.card_backgrounds(render.RenderConfig(), first, last, tmp_path) == (None, None)
    assert len(stills) == 2
    image = render.RenderConfig(title_background="https://example.org/hero.jpg")
    assert all(render.card_backgrounds(image, first, last, tmp_path))  # a film's own image still wins


def test_a_cards_value_that_is_neither_is_named():
    parsed, problems = sig.parse({"name": "x", "cards": "marble"})
    assert parsed.cards == "footage" and "cards" in problems[0]

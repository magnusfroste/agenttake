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


MARKED = {**HERMES, "marks": ["window", "trace", "count_up"]}
UNMARKED = {**HERMES, "marks": []}


def test_marks_are_named_kept_and_checked(house):
    saved, problems = sig.save(MARKED, "marks")
    assert problems == [] and saved.marks == ("window", "trace", "count_up")
    assert json.loads((house / "signature.json").read_text())["marks"] == ["window", "trace", "count_up"]
    parsed, problems = sig.parse({"name": "x", "marks": ["window", "glitter"]})
    assert parsed.marks == ("window",) and "glitter" in problems[0]
    assert sig.Signature().marks == ()


def test_the_window_is_the_films_own_shape():
    inner_w, inner_h, left, top = render.window_geometry(1920, 1080)
    assert inner_w % 2 == inner_h % 2 == 0 and inner_w / inner_h == pytest.approx(16 / 9, abs=0.01)
    assert left + inner_w + left == 1920 and top + inner_h + top == 1080 and top > 0


@pytest.mark.parametrize("lines,expected", [
    (["7845 metres", "of bridge."], "7845"),
    (["47% of users"], "47"),
    (["8 km above.", "4 km beneath."], None),
    (["Since 2026"], None),
    (["1.2 million"], None),
    (["3,000 users"], None),
    (["No numbers"], None),
])
def test_only_a_card_with_one_whole_number_counts_up(lines, expected):
    found = render._count_up_line(lines)
    assert (found[1].group() if found else None) == expected


def test_a_count_starts_at_the_same_number_of_digits():
    assert "floor(1000+(6845)" in render._count_up_expression(7845)
    assert "floor(0+(7)" in render._count_up_expression(7)


def test_a_brand_card_counts_up_only_with_the_mark(house, monkeypatch):
    seen = _capture_card(monkeypatch)
    sig.save(UNMARKED, "no marks")
    card = {"text": "47% of users", "background": "brand"}
    render.render_card_segment(card, 3.0, house / "a.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["count_up"] is False
    sig.save(MARKED, "marks")
    render.render_card_segment(card, 3.0, house / "b.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["count_up"] is True
    render.render_card_segment({**card, "background": "#334455"}, 3.0, house / "c.mp4", (640, 360), "#1f2a44")
    assert seen[-1][1]["count_up"] is False


def test_the_trace_is_drawn_only_for_a_house_with_the_mark(house):
    from reel_studio import storyboard as boards

    beat = {"shot": "system", "url": "https://e.example", "wait_for_text": "Headline", "line": "A line."}
    assert not any(op.get("action", {}).get("style") == "trace" for op in boards.recipe(beat))
    sig.save(MARKED, "marks")
    traced = [op for op in boards.recipe(beat) if op.get("action", {}).get("style") == "trace"]
    assert len(traced) == 1 and traced[0]["action"]["target_text"] == "Headline" and traced[0]["narration"] == "A line."


def _join_command(house, monkeypatch, cards):
    seen = []
    monkeypatch.setattr(render, "run_media", lambda command, **kwargs: seen.append(command))
    monkeypatch.setattr(render, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(render, "probe_video_size", lambda path: (640, 360))
    monkeypatch.setattr(render, "_mux_segment_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "choose_transitions", lambda paths, pages, temporary: [0.5] * (len(paths) - 1))
    monkeypatch.setattr(render, "window_frame", lambda out, width, height, ink: out)
    paths = [house / f"part-{number}.mp4" for number in range(3)]
    render._assemble_smooth(paths, [""] * 3, [None] * 3, house / "out.mp4", None,
                            render.RenderConfig(transitions="smooth"), house, cards=cards)
    return seen[0]


def test_pages_are_framed_and_cards_stay_full(house, monkeypatch):
    sig.save(MARKED, "marks")
    command = _join_command(house, monkeypatch, [False, True, False])
    graph = command[command.index("-filter_complex") + 1]
    assert str(house / "window.png") in command
    assert "[w0]" in graph and "[w2]" in graph and "[w1]" not in graph and "[p1]" not in graph
    assert "pad=640:360" in graph and "overlay=0:0" in graph


def test_without_the_mark_the_join_is_as_it_was(house, monkeypatch):
    sig.save(UNMARKED, "no marks")
    command = _join_command(house, monkeypatch, [False, True, False])
    assert "overlay" not in command[command.index("-filter_complex") + 1]


@media
def test_the_window_renders(house, tmp_path):
    sig.save(MARKED, "marks")
    frame = render.window_frame(tmp_path / "window.png", 320, 180, "#0B1B2B")
    corner, middle = [subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(frame), "-vf", f"crop=1:1:{x}:{y},format=rgba", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout for x, y in ((0, 0), (160, 90))]
    assert corner[3] == 255 and middle[3] == 0 and corner[0] == 0x0B

"""A wait_for_text miss says what the page showed instead.

The Sluss film's proof beat waited 300 s for "the data never left the house"
while the chat answered "Stopped by your policy" in 0.2 s; the error only said
the text did not appear (2026-10-28).
"""

from reel_studio.engine import text_that_appeared

BEFORE = """Sluss
Live chat
YOU
security review our internal SSO login flow
Ask anything — simple or hard…"""


def test_only_the_new_lines_are_reported():
    after = BEFORE + """
YOU
Summarise the case for customer 811218-9876 who complained about an invoice.
blocked · fail-closed
Stopped by your policy — nothing was sent to any model."""
    shown = text_that_appeared(BEFORE, after)
    assert "Stopped by your policy — nothing was sent to any model." in shown
    assert "blocked · fail-closed" in shown
    assert "Live chat" not in shown
    assert "security review" not in shown


def test_nothing_new_is_empty():
    assert text_that_appeared(BEFORE, BEFORE) == ""
    assert text_that_appeared(BEFORE, BEFORE + "\n   \n") == ""


def test_long_text_is_clipped_with_an_ellipsis():
    shown = text_that_appeared("", "x" * 2000, limit=50)
    assert len(shown) == 50
    assert shown.endswith("…")

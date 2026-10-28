"""The film-director skill (skills/film-director) is what a Hermes agent
follows to direct a film. It names AgentTake's tools and their arguments, so it
goes stale the moment one is renamed: this holds it to the server."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "film-director"
# The agent's own tools, which the skill names alongside AgentTake's.
AGENT_TOOLS = {"web_search", "web_extract", "write_file", "terminal", "vision_analyze"}


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("skill")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "skill.db")
    from reel_studio import server

    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


def skill_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(SKILL_DIR.rglob("*.md")))


def test_frontmatter_follows_the_skill_format():
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    end = content.index("\n---\n", 3)
    # Top-level keys only; PyYAML is not a dependency here.
    meta = dict(re.findall(r"^([a-z_]+): *(.*)$", content[4:end], re.M))
    assert meta["name"] == SKILL_DIR.name
    assert len(meta["description"]) <= 60 and meta["description"].endswith(".")
    assert meta["platforms"] and meta["license"] and meta["version"]
    assert content[end + 5:].strip()


def test_every_tool_the_skill_calls_exists(tools):
    called = set(re.findall(r"`([a-z_]+)\(", skill_text()))
    called |= set(re.findall(r"`([a-z_]+)`", skill_text())) & {
        "director_guide", "plan_storyboard", "shoot_storyboard", "shoot_beat", "apply_retakes",
        "start_session", "review_session", "finish", "finish_status", "rerender", "contact_sheet",
    }
    assert {"plan_storyboard", "shoot_storyboard", "shoot_beat", "contact_sheet"} <= called
    missing = called - AGENT_TOOLS - set(tools)
    assert not missing, f"the skill names tools AgentTake does not have: {sorted(missing)}"


def test_every_argument_the_skill_passes_exists(tools):
    text = skill_text()
    for tool, args in re.findall(r"`([a-z_]+)\(([^`)]*)\)", text):
        if tool in AGENT_TOOLS:
            continue
        properties = tools[tool].inputSchema["properties"]
        for name in re.findall(r"([a-z_]+)=", args):
            assert name in properties, f"{tool} has no argument {name}"
    start = tools["start_session"].inputSchema["properties"]
    for name in ("start_url", "title", "subtitles", "color_scheme", "mask", "cta_url", "width", "height"):
        assert re.search(rf"\b{name}\b", text) and name in start, name


def test_the_shot_menu_matches_the_storyboard_shots():
    from reel_studio.storyboard import SHOT_TYPES

    reference = (SKILL_DIR / "references" / "contact-sheet-review.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", reference, re.M))
    assert listed == set(SHOT_TYPES)

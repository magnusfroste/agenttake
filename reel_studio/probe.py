"""Scouting a page before a film: where its buttons are, what a press does,
and what a game's own state says, without a session or a recording.

Every point of the Kotten film (skolappar.com, 2026-11-24) was found with
Playwright scripts an operator wrote: the rolling pin at (0.267, 0.8457), the
oven, the julkalender's first door, and the bakery's SCENES.bakery.rollDone.
A director that is only an MCP client has no such scripts, so the tool shows
the page with a grid of the fractions start, taps and expect use.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PROBE_READ_MAX = 500
PROBE_READS_MAX = 6
PROBE_PRESSES_MAX = 8
PROBE_ELEMENTS_MAX = 40
PROBE_GLOBALS_MAX = 80

# A grid at every tenth of the screen, labelled, and each press marked where
# it lands (a ring, and a line to where a drag ends). Drawn into the page for
# the still only, then taken away.
GRID_JS = """(marks) => {
  const root = document.createElement("div");
  root.id = "__take_probe";
  root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;font:600 12px/1 monospace";
  const W = window.innerWidth, H = window.innerHeight;
  const line = (css) => { const d = document.createElement("div"); d.style.cssText = "position:absolute;" + css; root.appendChild(d); };
  const label = (text, css) => { const d = document.createElement("div"); d.textContent = text;
    d.style.cssText = "position:absolute;padding:2px 3px;background:rgba(0,0,0,.72);color:#fff;border-radius:3px;" + css; root.appendChild(d); };
  for (let i = 1; i < 10; i++) {
    const f = i / 10, strong = i === 5;
    const colour = strong ? "rgba(255,0,170,.85)" : "rgba(255,0,170,.45)";
    line(`left:${f * W}px;top:0;bottom:0;border-left:1px ${strong ? "solid" : "dashed"} ${colour}`);
    line(`top:${f * H}px;left:0;right:0;border-top:1px ${strong ? "solid" : "dashed"} ${colour}`);
    label("x " + f.toFixed(1), `left:${f * W + 3}px;top:3px`);
    label("y " + f.toFixed(1), `top:${f * H + 3}px;left:3px`);
  }
  (marks || []).forEach((m, n) => {
    const x = m.x * W, y = m.y * H;
    line(`left:${x - 14}px;top:${y - 14}px;width:28px;height:28px;border:3px solid #00e0ff;border-radius:50%`);
    label(String(n + 1), `left:${x + 16}px;top:${y - 8}px;background:#00a7c4`);
    if (m.to_x != null) {
      const tx = m.to_x * W, ty = m.to_y * H, len = Math.hypot(tx - x, ty - y), ang = Math.atan2(ty - y, tx - x);
      line(`left:${x}px;top:${y}px;width:${len}px;border-top:3px solid #00e0ff;transform-origin:0 0;transform:rotate(${ang}rad)`);
      line(`left:${tx - 6}px;top:${ty - 6}px;width:12px;height:12px;background:#00e0ff;border-radius:50%`);
    }
  });
  document.documentElement.appendChild(root);
}"""

UNGRID_JS = "() => { const g = document.getElementById('__take_probe'); if (g) g.remove(); }"

# What can be pressed and where, as fractions of the screen: the middle of
# each visible button, link, field and canvas.
ELEMENTS_JS = """(limit) => {
  const W = window.innerWidth, H = window.innerHeight, out = [];
  const seen = new Set();
  const nodes = document.querySelectorAll(
    "button, a[href], [role=button], input, select, textarea, summary, [onclick], canvas, [tabindex]:not([tabindex='-1'])");
  for (const el of nodes) {
    if (out.length >= limit) break;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.right < 0 || r.top > H || r.left > W) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || +style.opacity === 0) continue;
    const text = (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().replace(/\\s+/g, " ").slice(0, 60);
    const kind = el.tagName.toLowerCase();
    const key = kind + text + Math.round(r.left) + Math.round(r.top);
    if (seen.has(key)) continue;
    seen.add(key);
    const f = (v) => Math.round(v * 10000) / 10000;
    out.push({kind, text, x: f((r.left + r.width / 2) / W), y: f((r.top + r.height / 2) / H),
              w: f(r.width / W), h: f(r.height / H)});
  }
  return out;
}"""

# Names a page's scripts put on window that a blank page has not: a game's
# var and function globals. Top-level const and let (SCENES in Lillstaden)
# are not on window: read them by name.
GLOBALS_JS = """(limit) => {
  const frame = document.createElement("iframe");
  frame.style.display = "none";
  document.documentElement.appendChild(frame);
  const blank = new Set(Object.getOwnPropertyNames(frame.contentWindow));
  frame.remove();
  return Object.getOwnPropertyNames(window)
    .filter((name) => !blank.has(name) && !name.startsWith("__") && !/^on[a-z]+$/.test(name) && !/^webkit/i.test(name))
    .slice(0, limit);
}"""

# Compiled by devtools, not eval, as a beat's expect is: the same reading.
READ_JS = """() => {
  let value;
  try { value = (%s); } catch (e) { return "an error: " + e; }
  let shown;
  try { shown = JSON.stringify(value); } catch (e) { shown = String(value); }
  return (shown === undefined ? String(value) : shown).slice(0, %d);
}"""


async def read_values(page: Any, expressions: list[str]) -> dict[str, str]:
    """Each expression's value in the page, as JSON text, or why not."""
    values: dict[str, str] = {}
    for expression in expressions[:PROBE_READS_MAX]:
        try:
            values[expression] = await page.evaluate(READ_JS % (expression, PROBE_READ_MAX))
        except Exception as exc:
            values[expression] = f"not readable: {str(exc).splitlines()[0][:160]}"
    return values


async def elements(page: Any) -> list[dict]:
    try:
        return await page.evaluate(ELEMENTS_JS, PROBE_ELEMENTS_MAX)
    except Exception:
        return []


async def page_globals(page: Any) -> list[str]:
    try:
        return await page.evaluate(GLOBALS_JS, PROBE_GLOBALS_MAX)
    except Exception:
        return []


async def gridded_still(page: Any, path: Path, marks: list[dict] | None = None) -> Path | None:
    """A screenshot of the page with the grid and the presses drawn on it."""
    try:
        await page.evaluate(GRID_JS, marks or [])
        await page.screenshot(path=str(path), type="jpeg", quality=80)
        return path
    except Exception:
        return None
    finally:
        try:
            await page.evaluate(UNGRID_JS)
        except Exception:
            pass


def press_mark(press: Any) -> dict | None:
    """Where a press lands, for the still: a point or a drag, not a text."""
    if isinstance(press, str):
        return None
    point = press if isinstance(press, dict) else press.model_dump()
    return {key: point.get(key) for key in ("x", "y", "to_x", "to_y")}

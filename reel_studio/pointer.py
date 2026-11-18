"""The pointer the film shows: drawn in the page, not grabbed from the screen.

Playwright moves the mouse through Chrome's DevTools protocol, which never
moves the X server's pointer, so the arrow x11grab drew stood still wherever
the display put it while the clicks happened elsewhere (backlog 6c2444bd,
2026-11-10). This pointer follows the page's own mouse events instead:

- It glides there on a critically damped spring (tension 470, friction 70,
  mass 3: Cap's "mellow" default), so a jump of the mouse reads as a hand
  moving, not a teleport.
- A click shows a faint pulse, 260 ms at low opacity. Rings, glows and
  confetti read as cheap (research 2026-11-18); the yellow halo every click
  used to get is gone.
- It fades out after 2.5 s without moving, so a film is not one stray arrow
  in every frame, and fades back in on the next move.

Only the top frame draws it. The pulse is drawn whether the arrow is shown or
not: a click with nothing to mark it was easy to miss.
"""

from __future__ import annotations

import json

# How long the arrow takes to reach where the mouse went (the spring above
# settles in about this), so a click can wait for it.
GLIDE_SECONDS = 0.5
IDLE_HIDE_MS = 2500
PULSE_MS = 260


def pointer_script(arrow: bool, accent: str = "#ffffff") -> str:
    """The init script that draws the pointer on every page."""
    options = json.dumps({"arrow": bool(arrow), "idle": IDLE_HIDE_MS, "pulse": PULSE_MS, "accent": accent})
    return "(" + _POINTER_JS + ")(" + options + ");"


_POINTER_JS = r"""
(options) => {
  if (window.top !== window || window.__takePointer) return;
  window.__takePointer = true;
  const NS = 'http://www.w3.org/2000/svg';
  let root = null, arrow = null, x = null, y = null, vx = 0, vy = 0, tx = null, ty = null;
  let last = 0, shown = false, frame = 0;
  const KEY = 'take-pointer';
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    if (saved) { x = tx = saved[0]; y = ty = saved[1]; }
  } catch (e) {}
  const build = () => {
    if (root || !document.documentElement) return;
    root = document.createElement('div');
    root.dataset.takePointer = 'true';
    Object.assign(root.style, {position: 'fixed', inset: '0', pointerEvents: 'none', zIndex: '2147483647'});
    if (options.arrow) {
      arrow = document.createElementNS(NS, 'svg');
      arrow.setAttribute('width', '26'); arrow.setAttribute('height', '26'); arrow.setAttribute('viewBox', '0 0 26 26');
      Object.assign(arrow.style, {position: 'absolute', left: '0', top: '0', opacity: '0',
        transition: 'opacity 220ms ease', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.35))', willChange: 'transform'});
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', 'M3 2 L3 20.5 L8 15.8 L11.4 23.4 L14.6 22 L11.3 14.6 L18 14.6 Z');
      path.setAttribute('fill', '#111');
      path.setAttribute('stroke', '#fff');
      path.setAttribute('stroke-width', '1.6');
      path.setAttribute('stroke-linejoin', 'round');
      arrow.appendChild(path);
      root.appendChild(arrow);
    }
    document.documentElement.appendChild(root);
  };
  const place = () => {
    if (arrow && x !== null) arrow.style.transform = `translate(${x - 3}px, ${y - 2}px)`;
  };
  const show = (on) => {
    if (!arrow || shown === on) return;
    shown = on;
    arrow.style.opacity = on ? '1' : '0';
  };
  const step = (now) => {
    frame = 0;
    if (tx === null) return;
    const dt = Math.min(Math.max((now - (step.t || now)) / 1000, 0), 1 / 30);
    step.t = now;
    // tension 470, friction 70, mass 3
    const ax = (-470 * (x - tx) - 70 * vx) / 3, ay = (-470 * (y - ty) - 70 * vy) / 3;
    vx += ax * dt; vy += ay * dt; x += vx * dt; y += vy * dt;
    const resting = Math.abs(x - tx) < 0.3 && Math.abs(y - ty) < 0.3 && Math.abs(vx) + Math.abs(vy) < 2;
    if (resting) { x = tx; y = ty; vx = vy = 0; }
    place();
    if (!resting) frame = requestAnimationFrame(step);
    else step.t = 0;
  };
  const kick = () => { if (!frame) { step.t = 0; frame = requestAnimationFrame(step); } };
  const moved = (event) => {
    build();
    if (x === null) { x = event.clientX; y = event.clientY; }
    tx = event.clientX; ty = event.clientY;
    try { sessionStorage.setItem(KEY, JSON.stringify([tx, ty])); } catch (e) {}
    last = performance.now();
    show(true);
    kick();
  };
  const pulse = (event) => {
    build();
    if (!root) return;
    // The arrow lands where the press is: the click is where it points.
    x = tx = event.clientX; y = ty = event.clientY; vx = vy = 0; place();
    last = performance.now();
    show(true);
    const ring = document.createElement('div');
    const size = 30;
    Object.assign(ring.style, {position: 'absolute', left: `${event.clientX - size / 2}px`,
      top: `${event.clientY - size / 2}px`, width: `${size}px`, height: `${size}px`, borderRadius: '50%',
      background: 'rgba(255,255,255,.28)', boxShadow: '0 0 0 1.5px rgba(17,17,17,.22)',
      transform: 'scale(.55)', opacity: '1',
      transition: `transform ${options.pulse}ms cubic-bezier(.2,.7,.3,1), opacity ${options.pulse}ms ease-out`});
    root.insertBefore(ring, root.firstChild);
    requestAnimationFrame(() => { ring.style.transform = 'scale(1.5)'; ring.style.opacity = '0'; });
    setTimeout(() => ring.remove(), options.pulse + 80);
  };
  window.addEventListener('mousemove', moved, true);
  window.addEventListener('mousedown', pulse, true);
  setInterval(() => { if (shown && performance.now() - last > options.idle) show(false); }, 250);
  if (document.documentElement) build();
  else document.addEventListener('DOMContentLoaded', build);
}
"""

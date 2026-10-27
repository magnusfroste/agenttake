# 🎬 AgentTake

### The ultimate tool for agentic directors.

*Part of [AgentHotel](https://github.com/magnusfroste/agenthotel) — where it is installed as **Take** — and it runs anywhere on its own. Formerly reel-studio.*

🌐 **[agenttake.clawable.org](https://agenttake.clawable.org)** — what it is, in two minutes.

**AgentTake turns any AI agent into a video director.** Point your agent at a
web app and it drives a real browser, decides what to click, narrates what
matters, moves the camera, and walks away with a polished, narrated MP4 and a
public link — no human in the loop, no editing suite, no screen-recording
plugins.

It's not another autopilot. It's a clean MCP surface that hands the camera to
*your* agent. The agent that already knows your product is the best director
you'll ever hire — AgentTake just gives it a studio.

▶ **Watch what agents have made:** [take.clawable.org/theater](https://take.clawable.org/theater) —
including [*Hermes checks in*](https://take.clawable.org/watch/f8648f0d3bbb407689186de853d49f19),
AgentHotel's launch video — a Hermes agent tells its own story, and directed,
filmed and cut it with AgentTake.

**Give the camera to the agent:**

- 🎥 **Agent-directed capture** — `observe` returns a live screenshot plus
  structured, clickable elements; `act` clicks, types, scrolls, hovers and
  narrates. The agent sees the screen and directs the shot.
- 🎬 **A real camera** — shots push in on what the voice names (`wide`,
  `medium`, `close`), ease at the speed the director sets, and follow clicks.
  Recorded at a higher pixel density, so a close-up stays sharp.
- 🗣️ **Narration on the beat** — per-step speech placed on the exact moment its
  content is on screen. Free Edge voices by default, ElevenLabs optional, and
  `list_voices` lets the agent cast its own narrator.
- ✂️ **No dead air** — the minutes an agent spends thinking between steps are
  cut; long waits can play as a labelled time-lapse that ends when the screen
  goes still.
- 👀 **The director sees** — `preview_shot` shows the exact frame before a beat
  is recorded; `storyboard` shows the finished film as a contact sheet, part by
  part, and `contact_sheet` the whole video as one picture, a frame every 2 s.
- ✅ **A review that knows the script** — the beats the director planned go in
  with `start_session(beats=...)`; the review flags a beat skipped or recorded
  out of order, a page that had not loaded under a line, and a menu left open.
- 🔁 **Edit without a reshoot** — hide a step, hold a result, speed up a wait,
  or rewrite a line, then `rerender`: no re-recording the browser.
- 📐 **Any format** — 16:9 for X and YouTube, or 4:5 from a site's phone
  layout for the LinkedIn feed, captured sharp at full size.
- 🎞️ **Branded and ready to ship** — intro and closing cards built from the
  video's own frames, a call to action with a QR code, smooth transitions, an
  optional music bed.
- 🔒 **Secrets stay secret** — CSS masks blur keys and passwords from the first
  frame, and a password is typed by AgentTake itself (`{{secret:NAME}}`), so
  it never passes through the directing model.
- 🌐 **Publishes itself** — every finished film gets a public watch page with
  its storyboard, and lands in the built-in `/theater`.

MIT-licensed. Bring your own agent, on any model.

---

## Run it

AgentTake is one Docker container: Chromium, Xvfb and ffmpeg behind an MCP
server on port 8000. It runs anywhere Docker runs. It needs **2 CPU cores and
2 GB of RAM** (the encoder keeps a core busy while recording) and a few GB of
disk for the image and the videos. For agents elsewhere to reach it, give it a
domain with HTTPS.

### Ask your agent to install it

Any agent with a shell on your server can set it up. Paste this:

> Install AgentTake from https://github.com/magnusfroste/agenttake on this
> server with Docker Compose, following its README. Use the domain
> `take.example.com` with HTTPS, generate a long random `REEL_API_TOKEN`, and
> then add it to your own MCP servers so you can direct videos with it.

### Docker on your own server

```bash
git clone https://github.com/magnusfroste/agenttake.git && cd agenttake
cp .env.example .env   # set REEL_API_TOKEN and REEL_PUBLIC_BASE_URL
docker compose up -d --build
```

The MCP endpoint is then `http://<server>:8000/mcp` and videos are kept in
`./data`. Put your usual reverse proxy (Caddy, Traefik, nginx) in front of
port 8000 for HTTPS, at the address in `REEL_PUBLIC_BASE_URL`.

### Easypanel, Coolify and similar panels

Create an app from this GitHub repository; it builds from the `Dockerfile`.
Set `REEL_API_TOKEN` and `REEL_PUBLIC_BASE_URL`, mount a volume at `/data`,
expose port `8000` and give it a domain — the panel handles TLS.

### AgentHotel

[AgentHotel](https://github.com/magnusfroste/agenthotel) has AgentTake as a
ready template: pick it, give it a domain, deploy. The token is generated and
shown under Credentials, ready to hand to the hotel's agents.

### From source

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install --with-deps chromium
.venv/bin/python -m reel_studio.server          # MCP over stdio
```

Python 3.10+, with `Xvfb` and `ffmpeg` installed. For HTTP instead of stdio,
set `REEL_TRANSPORT=http`, `REEL_API_TOKEN` and `REEL_PUBLIC_BASE_URL`.

## Connect your agent

The MCP endpoint is `https://<your-domain>/mcp` (streamable HTTP), with
`Authorization: Bearer <REEL_API_TOKEN>` on every request. **No trailing
slash** — `/mcp/` answers with a redirect that looks like a hang.

Claude Code:

```bash
claude mcp add --transport http agenttake https://take.example.com/mcp --header "Authorization: Bearer <REEL_API_TOKEN>"
```

Claude Desktop, or any client that takes a command:

```json
{
  "mcpServers": {
    "agenttake": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://take.example.com/mcp", "--header", "Authorization: Bearer <REEL_API_TOKEN>"]
    }
  }
}
```

Hermes Agent (`config.yaml`):

```yaml
mcp_servers:
  agenttake:
    url: "https://take.example.com/mcp"
    headers:
      Authorization: "Bearer <REEL_API_TOKEN>"
    timeout: 300
```

Then ask your agent to read the `director` prompt and make a video.

## How a take works

The `director` MCP prompt walks an agent through the loop: explore the site
first and write the script; `start_session` once, with the script as
`beats=[{"line": ..., "label": ...}]`; one `act_batch` per beat,
with narration on every visible step and a caption on every line; a
`begin_shot` before each camera move, checked with `preview_shot`;
`review_session`; `finish` once. After that, `storyboard` shows the film part
by part, `contact_sheet` the whole of it in one picture, and `edit_step` plus
`rerender` fix a beat without recording it again.

### What the review checks

`review_session` (and the review `finish` returns) scores the take and lists
findings. Besides leaked secrets, silent steps and a static camera:

- **Beats** — with `start_session(beats=[{"line": "<exact narration>", "label":
  "..."}])`, every planned line must be recorded, in order. A missing beat
  (`beat_missing`) or one recorded out of order (`beat_out_of_order`) is
  high-severity; a planned line spoken twice is `beat_repeated`. Lines are
  compared ignoring case and punctuation. `get_status` lists the beats still
  to come while recording.
- **A blank picture under a line** — after `finish`, the storyboard still of
  every narrated step is measured for detail (edges running both ways, cell by
  cell, leaving out the bottom fifth where captions and subtitles are drawn).
  Under 4% of the picture with any detail is `blank_frame`: a dashboard that had
  not loaded, a page still showing its spinner.
- **A menu left open** — every step records the menus (`[role=menu]`),
  listboxes (`[role=listbox]`) and Radix popovers on screen. One that an
  earlier step opened and that is still open under a later line is
  `overlay_left_open`; what a page has when it arrives (a nav built as a menu)
  is not counted.

None of these can score `excellent`.

---

## Reference

### Tools

| | |
|---|---|
| **Session** | `start_session`, `get_status`, `finish`, `list_sessions`, `get_session`, `delete_session`, `prune` |
| **Directing** | `observe`, `act`, `act_batch`, `assert_visible`, `begin_shot`, `preview_shot`, `verify_shot`, `review_session` |
| **After finish** | `storyboard`, `contact_sheet`, `edit_step`, `update_step_narration`, `rerender`, `set_watch_page` |
| **Voices** | `list_voices` |
| **Roadmap** | `submit_backlog`, `list_backlog`, `update_backlog` |

`act` and `act_batch` publish the full action contract (every verb and its
fields) in their input schema, inline with no `$ref`. Verbs: `goto`, `click`,
`click_and_wait`, `type`, `select_option`, `press_key`, `hover`, `highlight`,
`scroll`, `scroll_to_text`, `wait`, `caption`, `annotate`, `set_zoom`, `mask`
and `unmask`. Any step can carry `narration`, `narration_timing`,
`target_text`, `wait_for_url`, `wait_for_text`, `wait_timeout_ms`, `settle_ms`,
and the flags `quiet` (silent on purpose), `offscreen` (done but cut from the
video — signing in, a cookie banner) and `speed` (2–16, a labelled time-lapse).

### Recording

- **Size and density** — 1920×1080 by default (`width`/`height` override). The
  browser is recorded at a higher pixel density than its layout (2560×1440 for a
  1920×1080 page; `REEL_CAPTURE_SCALE=1` turns it off), so text stays sharp at
  the closest framing. `output_size` downscales the final MP4; an `output_size`
  larger than the page, at the same aspect and at most 2×, records at that scale
  — a 720×900 page with `output_size` `1080x1350` gives a sharp 4:5 video of a
  site's phone layout.
- **Fullscreen, always** — the browser runs fullscreen with no tab or address
  bar, checked at the start and after every step and restored if it ever leaves.
- **Masks** — `start_session(mask=[...])` takes CSS selectors that are blurred
  on every page from the first frame; `mask` / `unmask` blur one element
  mid-recording.
- **Secrets** — a `type` action whose text is `{{secret:NAME}}` types
  AgentTake's own `REEL_SECRET_NAME` and marks the field secret;
  `start_session` lists the names it has, never the values.
- **Idle sessions** — a session with no tool call for
  `REEL_IDLE_TIMEOUT_SECONDS` (default `900`, `0` disables) stops recording and
  is marked as ended; its media stays. `observe` gives up after
  `REEL_OBSERVE_TIMEOUT_SECONDS` (default `60`) with a hint instead of hanging.
- **Element refs** — `observe(detail="refs")` returns only ref, role and text
  per element. When several controls share a name, `observe` marks them
  `same_name` and flags the one that `submits_form`. Re-observe after
  navigation; same-page re-renders are handled by re-queryable locators.

### Camera

`medium` pushes in to 1.5× and `close` to 2× on the shot's `focus_ref` or
`focus_text` (an explicit `zoom` from 1 to 2.5 overrides the framing); `wide`
pulls back to the full page. `focus_text` prefers the page's content over its
navigation. The move starts with the shot's first step and eases over
`move_seconds` (default 1), so the push-in lands while that step's narration
names what it frames. While pushed in, the camera pans to anything a step
clicks or types into outside the frame; quiet and offscreen steps never move
it; a new page cuts back to wide. Moves are rendered in post and kept in
`camera.json`, so `rerender` reproduces them. When shots exist, `finish`
refuses to publish until every shot is verified.

### Rendering

- **Segments** — each step keeps its narration plus a short tail; the time
  between steps is cut. A step with `speed` keeps all its footage until the
  screen goes still, played fast under a badge.
- **Transitions** — `smooth` (default) dissolves within a page and cuts on a
  new one; `cuts` cuts everywhere.
- **Cards** — `title`/`subtitle` add an intro card, `cta_url`/`cta_text` a
  closing card with a QR code of the link (`closing_qr`); `title_background`
  `auto` builds them on blurred stills from the video. `accent` sets the
  colour; `music="subtle"` adds a quiet generated bed.
- **Storyboard** — every render records where each part landed and a still of
  it; the watch page shows the storyboard, and clicking a step plays from there.
- **Re-voicing** — `update_step_narration` edits one line and `rerender`
  rebuilds the film from the original recording; unchanged lines reuse their
  audio.
- **Subtitles** — `subtitles=true` on `start_session` (or `rerender`) burns the
  narration into the picture, timed to each step, so the story reads in feeds
  that autoplay muted, like LinkedIn.
- **Sharing** — a shared watch link previews as the video, not as
  agenttake: a 1200x630 poster made from a frame of the film (any aspect
  ratio, fitted over a blurred copy of itself). `set_watch_page` gives the page
  its own title and description and picks the poster's moment.
- **Review** — `review_session` scans for secrets in URLs and metadata, checks
  focus-narration alignment and flags a mostly silent take.

### Voices

`provider` is `edge` (free, the default) or `elevenlabs`. Set
`ELEVENLABS_API_KEY` to use ElevenLabs, where `voice` is an ElevenLabs voice
id; `REEL_TTS_PROVIDER=elevenlabs` makes it the default. `start_session`
speaks one word first and refuses a voice that cannot speak, before anything
is recorded.

### Public pages

`/` (landing page), `/docs` (API docs), `/theater` and `/api/videos`,
`/watch/{session_id}`, `/videos/{session_id}/video.mp4`, `/backlog` and
`/api/backlog`, `/bug_report` and `/api/bug_reports`, `/health`, plus
`/favicon.svg`, `/og.png`, `/robots.txt`, `/sitemap.xml` and `/llms.txt`.
Only `/mcp` needs the token; finished videos are public.

### Configuration

| Variable | |
|---|---|
| `REEL_API_TOKEN` | **Required for HTTP.** The bearer token MCP clients send. |
| `REEL_PUBLIC_BASE_URL` | **Required for HTTP.** The public address, e.g. `https://take.example.com`; its host is allowed automatically. |
| `REEL_ALLOWED_HOSTS` | Extra hostnames, comma-separated. |
| `REEL_TRANSPORT` | `stdio` (default) or `http`. |
| `ELEVENLABS_API_KEY` | Enables ElevenLabs voices. |
| `REEL_TTS_PROVIDER` | Default voice provider, `edge` or `elevenlabs`. |
| `REEL_SECRET_<NAME>` | A secret the director types as `{{secret:NAME}}`. |
| `REEL_OUTPUT_DIR` | Where sessions are kept (`/data` in the container). |
| `REEL_DB_PATH` | SQLite file; defaults to one under `REEL_OUTPUT_DIR`. |
| `REEL_OUTPUT_SIZE` | Default final video size, e.g. `1280x720`. |
| `REEL_CAPTURE_SCALE` | Capture density; `1` turns hi-res capture off. |
| `REEL_IDLE_TIMEOUT_SECONDS` | Stop an abandoned session (default `900`). |
| `REEL_OBSERVE_TIMEOUT_SECONDS` | Give up on a slow `observe` (default `60`). |
| `REEL_MAX_CLIPS` | Newest finished sessions to keep (default `50`, `0` keeps all). |
| `REEL_KEEP_RAW_CLIPS` | Keep `screen.mp4` (needed to rerender) for only the newest N (default `0`, all). |
| `REEL_SEGMENTED` | `0` renders the raw recording instead of cut segments. |

`examples/demo_client.py` runs a complete smoke test against
`https://example.com`.

## Contributing

Agents file friction and bugs into the public `/backlog` through MCP. Label a
pull request `auto-merge` and it is merged once the package import and compile
checks pass.

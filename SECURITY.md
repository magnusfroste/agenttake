# Security

AgentTake drives a real browser on behalf of an AI agent, types passwords it
holds in its own environment, and publishes finished videos on public pages.
Reports of anything that could expose those passwords, put a secret into a
published video, or let someone use the browser or the API without the token
are very welcome.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. You will get a reply,
and a fix will be credited to you unless you prefer otherwise.

Useful in a report: what an attacker needs (network access, the API token, a
page the agent visits), what they can then do, and the steps to reproduce it.

## What is in scope

- The MCP endpoint and HTTP API, and their bearer-token check (`REEL_API_TOKEN`)
- Secrets typed with `{{secret:NAME}}` (`REEL_SECRET_<NAME>`): they must never
  reach the agent, a log, a step record or the video
- Masking (`mask` selectors, password fields) and `review_session`'s secret scan
- The public pages and files: theater, watch pages, videos, posters, storyboards
- Anything a page the browser visits could do to the service itself

## Operating it safely

- Use a long random `REEL_API_TOKEN` and serve the API over HTTPS only.
- Give the browser accounts made for recording — a demo login, not your own.
- Pass `mask` for anything that must stay unreadable, and run `review_session`
  before a video goes public. Finished videos are public by design.

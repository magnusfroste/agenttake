---
name: film-director
description: Direct sourced short films in AgentTake from storyboards.
version: 0.2.0
author: Magnus Froste (magnusfroste), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [video, film, storyboard, demo, narration, agenttake, mcp]
    related_skills: []
---

# Film Director Skill

You direct a short film. You do the research, choose the story and write every
line. AgentTake shoots it with its recipes, so you never issue browser actions
yourself. The tools come from the AgentTake MCP server: `director_guide`,
`plan_storyboard`, `shoot_storyboard`, `shoot_beat`, `apply_retakes` and the
session tools.

Sixteen takes of one film failed on procedure and never on writing: a skipped
intro, a menu left open, a glide reported as moving when it did not. This skill
keeps procedure out of your hands and puts judgment back in them: sources,
story, and looking at the finished film.

## When to Use

- "Make a 60-second film about X", "a LinkedIn video of our product", "you are
  the director": a narrated web film with sources, cards, a product tour, a
  sign-in, live proof and a call to action.
- Retaking or fixing a film that was shot from a storyboard.

Don't use for:
- A free-form screen recording with clicks the shot menu has no recipe for.
  Use `act` / `act_batch` directly instead, following the rules AgentTake's
  instructions give.
- Generated video, avatars or voice clones. AgentTake films real pages.

## Prerequisites

- The AgentTake MCP server is configured under `mcp_servers` (an
  `https://<host>/mcp` URL and a bearer token), with a tool `timeout` of at
  least 300 s. One `shoot_storyboard` call records for up to about 240 s.
- `plan_storyboard` is in your tool list. If it is not, that AgentTake predates
  storyboards: say so and stop. Do not fall back to seventy `act` calls.
- A password is held by AgentTake as `REEL_SECRET_<NAME>`. You only ever pass
  the NAME. `start_session` lists the names it has under `secrets`.
- Your own `web_search`, `web_extract` and browser tools for research, and
  `vision_analyze` for the contact sheet.

## Procedure

1. **The one sentence.** Write the audience, the single claim, the proof and
   the one action down in one sentence. *Done when* it fits in one sentence
   and the film's length is set (60 s unless asked).
2. **Read the handbook once.** Call `director_guide()`. It has the shot menu,
   the fields of each shot, the 60-second template and the rules. *Done when*
   you know which shots the story needs.
3. **Research the claims.** Use `web_search` and then `web_extract` on each
   source. For each claim, keep three things: the URL, the headline exactly as
   the page shows it (this becomes `wait_for_text`), and the quote copied
   character for character from the page, about a dozen words (this becomes
   `text`). If you cannot find a public source, cut the claim; never round,
   merge or invent a figure. *Done when* every number and quote in your lines
   has a URL, and its `text` was found verbatim in that page's extract.
4. **Scout the product offscreen** with your browser tools. Note the tour
   URLs, the sign-in URL and what the page after sign-in shows, the system
   page and a word on it, and the chat page. For a live answer, ask the
   question once yourself and pick words only the answer contains. Ask the
   operator for the secret's NAME, never the password. *Done when* every
   `url` and `wait_for_text` in the plan was seen on a real page.
5. **Write the beats.** Follow the template: hook, problem with a source, the
   turn, solution, proof in the system, CTA. That is about ten beats and 150
   words for a minute. Each line is one thought and at most 35 words, spoken
   once, naming what is on screen. A `page_tour` glides over a whole page,
   so give each page at most one; a line about one section of it is a
   `system` shot with that section's heading as `wait_for_text`. Save them with `write_file` to
   `storyboard.json` in your working directory. Retakes must reuse identical
   lines: AgentTake reuses the voice clip of an unchanged line, so a retake
   costs no TTS. *Done when* the file holds every beat.
6. **Plan.** Call `plan_storyboard(beats, target_seconds=60)`. Fix every
   checklist item marked `fix` in `storyboard.json`, weigh each `warn`, and
   call it again with the same `plan_id` until `ready` is true. *Done when*
   `ready` is true and `estimated_seconds` is within 10 s of the target.
7. **Show the plan when someone commissioned it.** Send the operator a table
   of beat, shot, line and source, and wait for a go. A fixed line costs
   seconds; a retake costs half an hour. Skip this step only when told to run
   unattended. *Done when* you have a go, or were told to skip it.
8. **Open one session.** Call `start_session` with:
   - `start_url`: the first page the plan opens
   - `title`, and `subtitles=true` (feeds autoplay muted)
   - `color_scheme`
   - `mask=["input[type=password]", "[data-secret]"]`
   - `cta_url`: the cta beat's url

   On a host with two cores, use `width=1280, height=720`. Keep the
   `session_id`: it is not your chat session's id. *Done when* you hold
   exactly one open session id.
9. **Shoot.** Call `shoot_storyboard(session_id, plan_id)`, and again while
   `to_come` is not empty. Note each beat's `ok` or `failed` and its reason.
   *Done when* `to_come` is empty.
10. **Retake what failed**, at most twice per beat:
    1. Read the reason.
    2. Fix the beat in `storyboard.json`. A wrong `wait_for_text` or a quote
       not on the page are the usual causes.
    3. Call `plan_storyboard(beats, plan_id=...)`, then `shoot_beat(session_id,
       beat=n)`.

    A beat that fails twice is cut from the story or brought to the operator,
    never retried a third time. A failed `login` stops the shoot: check the
    secret name and the post-sign-in text, then retake it and resume
    `shoot_storyboard`. *Done when* every beat is ok or deliberately cut.
11. **Review, finish, place the retakes.**
    1. Call `review_session` and fix every high-severity finding. A planned
       line that is missing or out of order is one.
    2. Call `finish` once. Follow `finish_status` until it is `done`.
    3. If you retook any beat, call `apply_retakes(session_id)`, then
       `rerender(session_id)`.

    *Done when* the render is done with the retakes in place.
12. **Look at the film.** Call `contact_sheet(session_id)`, then
    `vision_analyze` on its image against your beat list, using the checklist
    in `references/contact-sheet-review.md`. *Done when* every beat is
    accounted for on the sheet. If a flaw shows, return to step 10 for that
    beat.
13. **Deliver.** Download the MP4 at once with
    `terminal(command="curl -fLo film.mp4 <video_url>")`, and measure it with
    `ffprobe`. Report:
    - the watch URL and the local path,
    - the duration,
    - the beats shot, retaken and cut,
    - every source URL.

    *Done when* the file is on disk and its duration is within 15 % of
    `estimated_seconds` plus the title and closing cards.

## Rules

- **A source for every claim**, named on the beat. No source, no claim.
- **A source's beat says what the source says.** The viewer reads the
  source's name under the quote, so its line reports the source; your own
  point goes on the next beat.
- **Headlines and short quotes only.** One marked sentence, never an article
  read out. For a paywalled page, the headline only.
- **Never a synthetic real person.** No generated faces, voices or quotes
  attributed to someone. An `image` is a real photo or a graphic you may use,
  with its source named.
- **Open on the claim.** The first three seconds decide whether anyone
  watches. Sign-ins are offscreen by design.
- **One session per film.** Never start a second one to get around a problem
  in the first. Fix the beat and retake it.

## Pitfalls

- **`review_session` scoring 95 does not make a film good.** It has passed takes
  with skipped beats and blank pages. Only the contact sheet is evidence, and
  step 12 is not optional.
- **The text has to match the page exactly.** `wait_for_text` and a
  highlight's `text` must match what the page renders. Curly quotes,
  non-breaking spaces, a CSS uppercase heading and a truncated teaser all
  differ from the article body. Copy from `web_extract`, not from a search
  snippet.
- **A tour does not stop at its anchor.** Four `page_tour`s of one site,
  from four anchors, each glided to the footer: the footer played under four
  lines, and lines about memory played over the model section. The plan now
  refuses a page toured twice; use `system` per section.
- **AgentTake probes the URLs from its own server.** A site that blocks
  datacenter addresses shows a bot check in the plan even though it opens in
  your browser. Treat `bot_check` as a likely failed beat and pick another
  source.
- **A shot changed after it was shot** shows as `changed` in the plan. Only
  `shoot_beat` records it again; `shoot_storyboard` moves on.
- **`finish` answering `rendering` is not a failure.** Follow `finish_status`.
  Calling `finish` again does not help.
- **A finished session can be pruned later.** The downloaded MP4 is the
  delivery; a `video_url` in a reply is not.

## Verification

- `plan_storyboard` reported `ready: true`, and every beat ended `ok` or was
  cut by decision.
- The contact sheet shows each planned beat, in order, with nothing blank,
  open or covered.
- The MP4 is on disk, `ffprobe` gives a duration within 15 % of
  `estimated_seconds` plus the title and closing cards, and the report lists
  every source URL.

---
name: film-director
description: Direct sourced short films in AgentTake from storyboards.
version: 0.9.0
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
yourself. The tools come from the AgentTake MCP server; `director_guide` is
the whole procedure, and this skill adds what depends on your own tools: the
research, the writing and showing the plan to a person.

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
  least 300 s. Every shoot call answers within about 200 s; a shoot still
  running answers `status: "shooting"` and `shoot_status` follows it.
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
   the fields of each shot, the 60-second template and the rules, and it ends
   with the house style: the **signature** this instance's films carry. The
   look (colours, type, the closing card's sign-off and AI label) is the
   instance's and not yours, so pass no `accent`; the signature's writing
   rules are yours to write by, and its voice is the default voice. Friction
   with the look or the voice goes to `submit_backlog(category="signature")`,
   never into the storyboard. *Done when* you know which shots the story needs
   and the rules you write by.
3. **Research the claims.** Use `web_search` and then `web_extract` on each
   source. For each claim, keep three things: the URL, the headline exactly as
   the page shows it (this becomes `wait_for_text`), and the quote copied
   character for character from the page, about a dozen words (this becomes
   `text`). If you cannot find a public source, cut the claim; never round,
   merge or invent a figure. *Done when* every number and quote in your lines
   has a URL, and its `text` was found verbatim in that page's extract.
4. **Scout the product offscreen** with your browser tools, or after planning
   with `rehearse_beats`, which also works behind a sign-in. Note the tour
   URLs, the sign-in URL and what the page after sign-in shows, the system
   page and a word on it, and the chat page. A live answer needs no
   predicted words: AgentTake waits until it stops growing and frames its
   start. Give `wait_for_text` only for words the answer must contain to
   prove the line, from asking the question once yourself. Ask the
   operator for the secret's NAME, never the password. *Done when* every
   `url` and `wait_for_text` in the plan was seen on a real page.
5. **Write the beats.** Follow the template: hook, problem with a source, the
   turn, solution, proof in the system, CTA. That is about ten beats and 150
   words for a minute. Each line is one thought and at most 35 words, spoken
   once, naming what is on screen. A `page_tour` glides down from the hero,
   slow at first; give it `until_text`, the heading of the section the line
   is about, so it stops there instead of rushing on. At most one per page. Open the product on a `system` shot of its hero, then one
   `system` shot per section a line names, with the section's heading as
   `wait_for_text`. Save them with `write_file` to
   `storyboard.json` in your working directory. Retakes must reuse identical
   lines: AgentTake reuses the voice clip of an unchanged line, so a retake
   costs no TTS. *Done when* the file holds every beat.
6. **Plan.** Call `plan_storyboard(beats, target_seconds=60)`. Fix every
   checklist item marked `fix` in `storyboard.json`, weigh each `warn`, and
   call it again with the same `plan_id` until `ready` is true. *Done when*
   `ready` is true and `estimated_seconds` is within 10 s of the target.
   Then try the beats that depend on a live page with
   `rehearse_beats(plan_id, beats=[...])`: nothing is recorded, and each beat
   answers with what the page showed and a still of the frame.
7. **Show the plan when someone commissioned it.** Send the operator a table
   of beat, shot, line and source, and wait for a go. A fixed line costs
   seconds; a retake costs half an hour. Skip this step only when told to run
   unattended. *Done when* you have a go, or were told to skip it.
8. **Run the film.** The handbook's "The whole film, start to done" and each
   tool's `next` carry the procedure from here: open one session, shoot,
   retake, `finish`, judge with `review_session` and `contact_sheet`, mend,
   deliver. Pass the picture to `vision_analyze` if you cannot see it in the
   reply, with the checklist in `references/contact-sheet-review.md`. Stop at
   the `verdict`: `ready_for_approval` or `needs_human` goes to the operator.

## Rules

The rules for sources, quotes and real people are in the handbook. Yours to
keep here: do your research with your own tools, never put a password in a
brief, and never publish a film yourself.

## Verification

- `plan_storyboard` reported `ready: true`.
- `review_session` gave a `verdict`, and your report states it with what is open.
- The MP4 is on disk and the report lists every source URL.

# The director's handbook: storyboards

You write the film. AgentTake shoots it.

A director model is good at research and at writing lines. It is not good at
issuing seventy browser actions in the right order: sixteen takes of one film
failed on procedure, never on writing — an intro skipped, a menu left open, a
glide reported as moving when it did not. So you hand over a **storyboard**,
a list of beats, and AgentTake shoots each beat with a recipe that has been
shot before.

```
director_guide()                       ← this handbook
plan_storyboard(beats=[...])           → plan_id, checklist, estimated_seconds
  fix every item marked "fix", plan_storyboard(beats, plan_id=...) again
start_session(start_url=<first beat's url>, title=..., subtitles=true, ...)
shoot_storyboard(session_id, plan_id=...)   → per beat: ok, or failed and why
  call again while "to_come" is not empty (one call records a few minutes)
shoot_beat(session_id, beat=n)         ← a retake, for a beat that failed
review_session(session_id), finish(session_id)
apply_retakes(session_id), rerender(session_id)   ← only if you retook a beat
contact_sheet(session_id)              ← look at the film before you share it
```

## A beat

```json
{"shot": "highlight", "line": "Gartner calls it the fastest-growing blind spot in security.",
 "text": "fastest-growing blind spot", "source": "https://example.org/shadow-ai-report",
 "label": "the quote"}
```

- `shot` — one of the shots below.
- `line` — the narration, exactly as it is spoken, once. One thought, at most
  about 35 words. Every shot is narrated except `login`.
- `source` — the URL a claim comes from. Required on `source`, `highlight` and
  on a `card` that states a number. A `source` shot's own url counts, and so
  does a `highlight`'s, or the article a `source` beat opened before it; a
  `highlight` on a page another shot opened (a product tour) names its source.
- `label` — a short name, used in reports.
- plus the shot's own fields.

## The shot menu

| shot | fields | what AgentTake does |
|---|---|---|
| `source` | `url`, `wait_for_text` (the headline) | Opens the article offscreen, closes a cookie banner, waits for the headline, then holds it wide with a slow push-in while the line plays. |
| `highlight` | `text` (a short quote), `url` optional, `source`, `caption` optional | Scrolls to the words, marks them with the source's name, close shot kept above the subtitle band. The close-up is never tighter than the whole quote: a quote that wraps across a column gets a gentler zoom instead of losing its end off the edge. Without `url` it stays on the page before. |
| `card` | `text` (`\n` for a second line), `background` optional, `source` when it states a number | A full-screen text card: `auto` follows the session's colour scheme; or a colour (`#0f1420`, `navy`), or an https image dimmed under the text. A sourced card names its source in the corner. |
| `image` | `url` (an https image), `caption` optional | The still with a slow Ken Burns drift; `caption` is a lower third, above the subtitles. |
| `page_tour` | `url`, `wait_for_text` optional | A glide from the hero to the footer, as long as the line. **Once per page**: a second tour of it, from whatever anchor, glides over the same sections again. |
| `login` | `url`, `username`, `password_secret`, `wait_for_text` | Signs in offscreen and lands on the page after it. `password_secret` is the NAME of a secret AgentTake has (start_session lists them); the password never passes through you. `wait_for_text` is something the page after sign-in shows. The line is optional. |
| `system` | `url`, `wait_for_text`, `zoom` optional | A page in the product, or one section of any page, held with a gentle push-in on `wait_for_text` (the section's heading). `zoom` (0.5–2) makes small text readable. |
| `live_answer` | `input_text`, `wait_for_text`, `timeout` (seconds, default 120), `url` optional | Types the question and sends it offscreen, waits for the answer (minutes if need be), then shows it close, above the subtitles. `wait_for_text` must be words only the answer has. |
| `cta` | `url`, `wait_for_text` optional | The page the viewer should go to. Pair it with start_session's `cta_url` for the closing card and its QR code. |

Every narrated step waits for its text before the line plays, so a page that
has not loaded is a failed beat with a reason, not a blank frame in the film.
The session's `color_scheme` is honoured on every page, cards included.

## What plan_storyboard checks

Nothing is recorded. The checklist has two severities:

- **fix** — shoot_storyboard refuses until it is gone: a claim without a
  source; a URL that does not answer, answers 4xx/5xx, or 401/403 (a viewer
  cannot open it); a secret AgentTake does not have; a missing field; a line
  planned twice; a page toured twice.
- **warn** — yours to weigh: a plan longer than the film (`target_seconds`,
  60 by default, flagged above 70 s; 150 words is a minute), a page that
  looks paywalled, a bot check, a long quote, a figure in a line with no
  source on the beat.

## A 60-second story

Six movements, about ten beats, about 150 words.

1. **Hook** (0–5 s) — the claim or the stake, not a logo. A `source` shot of
   a headline, or a `card` with one striking number (sourced).
2. **Problem, with a source** (5–20 s) — `source`, then a `highlight` of the
   sentence that proves it.
3. **The turn** (20–25 s) — a `card`: "What if …?", "There is another way."
4. **The solution** (25–40 s) — a `system` shot of the product's hero, then
   one for each section a line names (its heading as `wait_for_text`), so
   the picture is what the voice is talking about. A `page_tour` sweeps
   past every section and the next shots show them again: keep it for a
   film where the page as a whole is the point.
5. **Proof, in the system** (40–55 s) — `login`, then `system` and a
   `live_answer`: the product doing it, live.
6. **Call to action** (55–60 s) — `cta`.

Example, for "how sluss.eu and demo.sluss.eu let a CISO sleep better" (the
angle brackets are what your research fills in; every figure in a line comes
from the beat's source):

```json
[
 {"shot": "source", "url": "<an article on shadow AI>", "wait_for_text": "<its headline>",
  "line": "<the finding, in one sentence, as the article states it>"},
 {"shot": "highlight", "text": "<a dozen words of it>", "line": "Security teams have a name for it: shadow AI."},
 {"shot": "card", "text": "You can't block it.\nYou can govern it.", "line": "Blocking does not work. Governing does."},
 {"shot": "page_tour", "url": "https://sluss.eu", "line": "<what Sluss is, in the viewer's terms>"},
 {"shot": "login", "url": "<demo.sluss.eu's sign-in page>", "username": "<the demo account>",
  "password_secret": "<the NAME of its secret>", "wait_for_text": "<what the page after sign-in shows>"},
 {"shot": "system", "url": "<the policy page>", "wait_for_text": "<a policy's name>",
  "line": "<what the CISO decides here>"},
 {"shot": "live_answer", "url": "<the chat page>", "input_text": "<a request the policy stops>",
  "wait_for_text": "<words only the answer has>", "line": "<what the viewer just saw happen>"},
 {"shot": "cta", "url": "https://sluss.eu", "line": "<one action, and where>"}
]
```

## The rules

- **A source for every claim.** A number, a quote, a finding: the beat names
  the URL it comes from. If you cannot find a public source, cut the claim.
- **A source's beat says what the source says.** Under a `source` or a
  marked quote the viewer reads the source's name, so the line reports the
  source; your own argument ("and that means…") goes on the next beat, a
  `card` or the product. Under a `highlight` the line says what the marked
  words say.
- **Headlines and short quotes only.** Show a headline and one sentence, not
  an article: a quote of a dozen words, marked, is fair use; a page read out
  is not. Paywalled pages: the headline only.
- **Never a synthetic real person.** No generated faces, voices or quotes
  attributed to real people. An `image` is a real photograph or a graphic you
  have the right to use, and its source is named.
- **Speak what the picture shows.** The line names what is on screen; the
  camera is already framing it. A tour is for the page as a whole; a line
  about one section belongs on a `system` shot of that section.
- **Open on the claim, not on a login.** The first three seconds decide
  whether anyone watches. Sign-ins are offscreen by design.

## When a beat fails

shoot_storyboard reports each beat `ok`, or `failed` with the reason — text
that never appeared, a field it could not find, a URL that did not open, or
`focus_covered`: the text the line waits for is there, but half or more of it
is under something else, and the reason names it (a consent dialog, a
newsletter box, an ad frame). The shoot carries on with the next beat (a
failed `login` stops it: what follows needs the sign-in).

A `focus_covered` beat needs a change before a retake can pass: another
`wait_for_text` further down the article, below the box, or another source.
AgentTake already refuses or hides the consent managers it knows; the one it
names is one it does not.

1. If the reason is in the beat (wrong `wait_for_text`, a quote that is not on
   the page), fix it: plan_storyboard(beats, plan_id=...) again. Beats already
   shot and unchanged keep their takes.
2. `shoot_beat(session_id, beat=n)` records it again, at the end of the
   session, opening its page itself.
3. After finish, `apply_retakes(session_id)` leaves the earlier take out and
   plays the retake where the beat belongs; then `rerender(session_id)`.

A beat that shot fine but could look better can be retaken the same way.

### After finish

`shoot_beat` only works before `finish`. A flaw you find on the contact
sheet afterwards is mended in the edit when the footage is right
(`edit_step`: `hidden`, `hold_seconds`, `position`; `update_step_narration`;
then `rerender`), or in a fresh take when it is not. A fresh take is a new
session shot from the same beats. Unchanged lines reuse their voice clips,
so it costs minutes, not quota. Delete the flawed session once the new
take passes.

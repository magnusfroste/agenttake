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
rehearse_beats(plan_id, beats=[...])   ← try the beats that depend on a live page, nothing recorded
start_session(start_url=<first beat's url>, title=..., subtitles=true, show_pointer=false, ...)
shoot_storyboard(session_id, plan_id=...)   → per beat: ok, or failed and why
  call again while "to_come" is not empty (one call records a few minutes)
  status "shooting"? shoot_status(session_id) until status is "done"
shoot_beat(session_id, beat=n)         ← a retake, for a beat that failed
review_session(session_id), finish(session_id)
apply_retakes(session_id), rerender(session_id)   ← only if you retook a beat
contact_sheet(session_id)              ← look at the film before you share it
```

## The whole film, start to done

You need nothing but these tools. Every reply says what comes next under
`next`.

1. **Plan.** `plan_storyboard(beats, target_seconds=60)`, and again with its
   `plan_id` until `ready` is true.
2. **Rehearse.** `rehearse_beats(plan_id, beats=[...])` for the beats on live
   pages. Nothing is recorded. Each answers with the page's text around the
   beat's words, what the frame cuts and a still. Use it to read a source too.
3. **Open one session.** `start_session` with `start_url`, `subtitles=true`,
   `color_scheme`, `show_pointer=false`, `mask=["input[type=password]",
   "[data-secret]"]`, `cta_url`, and `width` and `height` if not 1920x1080.
   A `title` adds a title card. The look is the signature's: pass no `accent`.
4. **Shoot.** `shoot_storyboard(session_id, plan_id)`, and again while
   `to_come` is not empty. `status: "shooting"` means `shoot_status` until
   `done`.
5. **Retake** a failed beat at most twice with `shoot_beat`, after fixing what
   its reason names. A beat that fails twice is cut or goes to a person.
6. **Finish.** `finish` once, then `finish_status` while it answers
   `rendering`. If you retook a beat: `apply_retakes`, then `rerender`.
7. **Judge.** `review_session` and `contact_sheet`. The verdict says where the
   film stands:

   | `verdict` | what it means |
   |---|---|
   | `fix` | Findings of high or medium severity are open and you may still mend them. `next` says what. |
   | `ready_for_approval` | Nothing high or medium is open. Low findings are yours to weigh. |
   | `needs_human` | What is open has used its reshoots and fresh take, and more is a person's call. Report it. |

8. **Mend.** In the edit when the footage is right: `edit_step`, `edit_cards`,
   `update_step_narration`, then `rerender`. When one beat's footage is wrong,
   change it with `edit_beat(beat=n, changes={...}, session_id=...)` and
   `reshoot_beat(session_id, beat=n)`: only that beat is shot again, and the
   film is cut with it. When most of the film is wrong, one `fresh_take`. It
   can break what was fine, so compare both. Then judge the film again.
9. **Deliver.** Download the film from `video_url`, because a finished session
   can be pruned later. Report the watch URL, the duration, each beat's status
   and every source URL. A person approves a film on its watch page, and only
   then is it listed in the theater. Re-rendering withdraws the approval.

**A score is not a verdict on the film.** The measures find what can be
measured. Go through the contact sheet beat by beat as well. `contact_sheet`
carries the picture in its reply; if you cannot see images, say so in your
report instead of calling the film seen. On every beat:

- It is there, in order, and its page had loaded.
- Nothing is left open: a cookie banner, a menu, a chat bubble, a link address.
- The picture is what the line names, and nothing plays twice.
- Nothing secret is readable.

**Text has to match the page.** `wait_for_text` and a highlight's `text` come
from what the page renders, not from a search snippet. AgentTake opens pages
from its own server, so a site that blocks data centres shows a bot check
even if it opens for you: pick another source.

**A friction with AgentTake itself** goes to `submit_backlog`, one thing each.

## A beat

```json
{"shot": "highlight", "line": "Gartner calls it the fastest-growing blind spot in security.",
 "text": "fastest-growing blind spot", "source": "https://example.org/shadow-ai-report",
 "label": "the quote"}
```

- `shot` — one of the shots below.
- `line` — the narration, exactly as it is spoken, once. One thought, at most
  about 35 words. Every shot is narrated except `login`.
- `source` — the URL a claim comes from. Required on `source`, `highlight`,
  `source_card` and on a `card` that states a number. A `source` shot's own url counts, and so
  does a `highlight`'s, or the article a `source` beat opened before it; a
  `highlight` on a page another shot opened (a product tour) names its source.
- `label` — a short name, used in reports.
- plus the shot's own fields.

## The shot menu

| shot | fields | what AgentTake does |
|---|---|---|
| `source` | `url`, `wait_for_text` (the headline) | Opens the article offscreen, closes a cookie banner, waits for the headline, then holds it wide with a slow push-in while the line plays. |
| `highlight` | `text` (a short quote), `url` optional, `source`, `caption` optional | Scrolls to the words, marks them with the source's name, close shot kept above the subtitle band. The close-up is never tighter than the whole quote: a quote that wraps across a column gets a gentler zoom instead of losing its end off the edge. Without `url` it stays on the page before. |
| `source_card` | `source`, `text` (the headline or a short quote, word for word), `publication` optional (default: the site's name), `date` optional | The quote on a clean card, as a television documentary shows a source: the publication and the date above, the quote large, the address below. For a source the camera cannot show: behind a paywall, answering 403, or buried under dialogs and ads. The real page when it can be read, the card when it cannot. |
| `card` | `text` (`\n` for a second line), `background` optional, `source` when it states a number | A full-screen text card: `auto` follows the session's colour scheme; or a colour (`#0f1420`, `navy`), or an https image dimmed under the text. A sourced card names its source in the corner. |
| `image` | `url` (an https image), `caption` optional | The still with a slow Ken Burns drift; `caption` is a lower third, above the subtitles. |
| `page_tour` | `url`, `wait_for_text` optional, `until_text` optional | A glide down from the hero, as long as the line: slow at first, then a steady pace a viewer can follow. With `until_text` (a heading further down, as the page shows it) it stops with that heading near the top; without, it goes about 400 px a second and stops where the line ends, not at the footer. **Once per page**: a second tour of it glides over the same sections again. |
| `login` | `url`, `username`, `password_secret`, `wait_for_text` | Signs in offscreen and lands on the page after it. `password_secret` is the NAME of a secret AgentTake has (start_session lists them); the password never passes through you. `wait_for_text` is something the page after sign-in shows. The line is optional. |
| `system` | `url`, `wait_for_text`, `zoom` optional | A page in the product, or one section of any page, held with a gentle push-in on `wait_for_text` (the section's heading). `zoom` (0.5–2) is the page's own zoom. |
| `live_answer` | `input_text`, `wait_for_text` optional, `timeout` (seconds, default 120), `url` optional | Types the question and sends it offscreen, reads the page until the answer has stopped growing for 3 s (minutes if need be), then shows it close, above the subtitles. With `wait_for_text` (words only the answer has) the answer must contain them and is framed on them; without, it is framed on its first words. |
| `cta` | `url`, `wait_for_text` optional | The page the viewer should go to. Pair it with start_session's `cta_url` for the closing card and its QR code. |

**`camera`** (1–2.5) says how close the camera goes on `source`, `highlight`,
`system`, `live_answer` and `cta`, which default to 1.12, 1.8, 1.3, 1.5 and
1.12. A frame that cuts text wants it lower, small text wants it higher. On a
`system` shot `zoom` changes the page and `camera` the framing:
`rehearse_beats` shows what you get.

Every narrated step waits for its text before the line plays, so a page that
has not loaded is a failed beat with a reason, not a blank frame in the film.
The session's `color_scheme` is honoured on every page, cards included.

## What plan_storyboard checks

Nothing is recorded. The checklist has two severities:

- **fix** — shoot_storyboard refuses until it is gone: a claim without a
  source; a URL that does not answer, answers 4xx/5xx, or 401/403 (the
  camera cannot open it: find a public source, or quote it on a
  `source_card`); a secret AgentTake does not have; a missing field; a line
  planned twice; a page toured twice; a page that sends a visitor to the
  sign-in, shot before the login beat that signs in there
  (`before_sign_in`: move the login first, it is shot offscreen).
- **warn** — yours to weigh: a plan longer than the film (`target_seconds`,
  60 by default, flagged above 70 s; 150 words is a minute), a page that
  looks paywalled, a bot check, a long quote, a figure in a line with no
  source on the beat, a page behind a sign-in with no login beat for it
  (`behind_sign_in`). On a `source_card`: a quote that is not in the page's
  text (`quote_not_on_page`: the card says the source says it, so copy it
  word for word), or a source AgentTake cannot read to check it
  (`unverified_quote`, a paywall or a 403: check it yourself).

Text is matched loosely when it is not found as written: dashes, quotes,
spaces, ellipses and case count as alike, so "Dry-run — test a prompt"
finds "DRY-RUN – TEST A PROMPT". Copy it from the page all the same.

## A 60-second story

Six movements, about ten beats, about 150 words.

1. **Hook** (0–5 s) — the claim or the stake, not a logo. A `source` shot of
   a headline, or a `card` with one striking number (sourced).
2. **Problem, with a source** (5–20 s) — `source`, then a `highlight` of the
   sentence that proves it.
3. **The turn** (20–25 s) — a `card`: "What if …?", "There is another way."
4. **The solution** (25–40 s) — a `system` shot of the product's hero, then
   one for each section a line names (its heading as `wait_for_text`), so
   the picture is what the voice is talking about. A `page_tour` with
   `until_text` set to the section the line is about glides from the hero
   past the sections leading up to it — the way in, not the whole page.
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
  "line": "<what the viewer just saw happen>"},
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
that never appeared, a field it could not find, a URL that did not open, a
live answer that never came (`no_answer`) or said something else
(`answer_without_text`, with what it did say), or
`focus_covered`: the text the line waits for is there, but half or more of it
is under something else, and the reason names it (a consent dialog, a
newsletter box, an ad frame). The shoot carries on with the next beat (a
failed `login` stops it: what follows needs the sign-in).

Every call answers within about three minutes, whatever a beat waits for. A
shoot still running then answers with `status: "shooting"`, the beat it is on
and the beats reported so far; `shoot_status(session_id)` waits for it again
and gives the result. Do not call `shoot_storyboard` or `shoot_beat` meanwhile
(they only report the same shoot), and `act` and `finish` refuse until it is
done.

A `source` or `highlight` beat whose text is covered is not failed: the text
was found on the page, so AgentTake leaves the covered take out of the video
and shoots the beat again as a `source_card` quoting it, under the same
line. The report says `fallback: "source_card"` and names what covered the
page. Any other covered beat fails `focus_covered` and needs a change before
a retake can pass: another `wait_for_text` further down the page, below the
box. AgentTake already refuses or hides the consent managers it knows; the
one it names is one it does not.

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
`review_session` measures the film: each finding gives its beat, `at_seconds`,
what it `measured` and a `fix`. `claims_to_check` lists lines stating a share,
with the percentages on their screen: compare them yourself.

(`edit_step`: `hidden`, `hold_seconds`, `position`; `update_step_narration`;
`edit_cards` for a card, the title or the closing card; then `rerender`, and
`finish_status` if it answers with a job_id), or in a fresh take when it is not.
A corrected line or card stays in the storyboard, so a fresh take starts from it.
`fresh_take(session_id)` starts a new session exactly as the first was
started (size, voice, cards, subtitles, mask, colour scheme) with AgentTake's
own copy of the storyboard, every beat still to shoot, and answers with the
beats. Change one with `plan_storyboard(beats, plan_id=<the new plan_id>)`,
then `shoot_storyboard(<the new session_id>)`. Unchanged lines reuse their
voice clips, so it costs minutes, not quota. Delete the flawed session once
the new take passes.

## Data that belongs to someone

A film is a publication. `plan_storyboard` refuses a beat that types or says a
personal number outside Skatteverket's test list, a card number or a secret, and
`review_session` stops at `needs_human` when such a thing is on a screen in the
film. Use test data. Swedish test personal numbers are in
[Skatteverket's open list](https://skatteverket.entryscape.net/rowstore/dataset/b4de7df7-63c0-4e7e-bb59-1f156a591763), and
AgentTake carries a copy, so nothing has to be looked up elsewhere.

`get_plan(session_id)` reads a storyboard back, and `edit_beat` changes one
beat of it. After a retake that replaced something that must not stay,
`purge_replaced_takes(session_id, confirm=True)` paints the old take black in the
recording and deletes its page text, voice clips and screenshots.

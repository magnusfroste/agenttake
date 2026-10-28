# Reviewing the contact sheet

`contact_sheet(session_id)` returns one image: a frame every two seconds, six
to a row, each stamped with its time. Pass it to `vision_analyze` together with
the beat list (number, shot, line, what should be on screen) and ask it to go
beat by beat. AgentTake's own review has passed takes this catches.

## Every beat

Read the sheet against the lines, not only against the shots. A sheet where
every page loaded and nothing is open can still be a bad film, and the first
storyboarded take was one: its own review found nothing.

- **It is there, in order.** Each planned beat has at least one frame showing
  its page, card or image. A missing beat, or two beats swapped, is a retake.
- **The page had loaded.** No blank white or grey frame, no spinner, no
  skeleton placeholders under a narrated line.
- **Nothing left open.** No cookie banner, theme or language menu, dropdown,
  modal, chat widget bubble or browser dialog in frame.
- **The picture is what the line names.** Read each line and look at the
  frames under it: a line about memory over a section about models, or over
  a footer, is a flaw even though both frames are clean.
- **Nothing plays twice.** The same section, footer or heading in frames of
  two different beats is a flaw: the second beat should have shown something
  else, usually a `system` shot of the section its line names.
- **Nothing secret readable.** A password field, key or token is blurred.

## Per shot

| shot | what the sheet must show |
|---|---|
| `source` | The headline readable, wide, with no paywall overlay covering it. |
| `highlight` | The marker on the quoted words with the source label, above the subtitle band. |
| `card` | The text whole, not cut at the edge; a sourced card shows its source. |
| `image` | The image itself, not a broken-image icon; the lower third above the subtitles. |
| `page_tour` | Consecutive frames at different scroll positions. Two identical frames across the whole beat mean the glide never moved. |
| `login` | Nothing: it is offscreen. Any sign-in form in frame is a flaw. |
| `system` | The `wait_for_text` element readable, with no error toast or warning banner. |
| `live_answer` | The answer, readable, not the typing or a "thinking" indicator. |
| `cta` | The destination page, then the closing card with its QR code. |

## The film as a whole

- Subtitles are present on every narrated frame and never cover the subject.
- Something changes at least every 10–20 s (a new page, a push-in, a card).
- The first frame after the title card is the hook, not a logo or a sign-in.

## The file

Measure the downloaded MP4:

```
terminal(command="ffprobe -v error -show_entries format=duration -of csv=p=0 film.mp4")
```

A duration well under the plan means narration was cut. A file that will not
probe is a broken capture: that take cannot be rendered again, so shoot a
fresh session from the same `storyboard.json`. The unchanged lines reuse
their voice clips.

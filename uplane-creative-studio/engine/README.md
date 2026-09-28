# Creative taxonomy — images vs videos

`creative-schema_20260625.csv` holds **two distinct schemas** in its `schema_type` column.
You cannot analyze a video the same way as an image, so each creative is read against the
schema that matches its type.

## `static_image` — a single frame
Read the whole frame at once: composition, subject, colours, the exact on-image text
(`main_text_exact` / `sub_text_exact` / `cta_text_exact`), and the visual/aesthetic fields.
There is no time and no audio, so everything is "what is on the canvas."

## `video_ad` — time + motion + audio (NOT one frame)
A video is a *sequence*, so its schema adds the things a single frame can't capture. Use these
for videos and never infer them from one still:

| Video-only field | Why it can't come from a single frame |
|---|---|
| `video_duration_sec` | length of the clip |
| `opening_hook_type`, `opening_visual` | the first 1–3 seconds (the scroll-stopper) |
| `scene_count`, `pace`, `editing_style` | how the clip is cut and how fast |
| `spoken_script_exact`, `captions_exact`, `music_or_audio_type` | the **audio** track |
| `scene_transcript_compact` | the **synopsis** — a timestamped, scene-by-scene account of actions + on-screen text + VO |

Everything shared (subject, benefit, offer, proof, archetype, aesthetic, emotion, trust) is read
**across the whole video**, not from one frame.

## How Creative Studio applies this
- **Image cards** → a static-frame brief: composition + art direction + message + layout + copy.
- **Video cards** → a *video* brief: a shot **synopsis** (grounded in the creative), **one
  physically-realistic motion**, a UGC/iPhone/9:16 **camera spec**, a basic **dialogue / VO line**,
  no on-screen text, and an optional **anti-AI realism** block. (See `csVideoPrompt` in
  `webapp/app.js`.) The image/video toggle in Creative Studio separates the two so each is read and
  briefed on its own terms.

## Engine status / target
`helper/engine.py` currently reads every creative against the image-style field set, using the
video **poster frame** for videos (interim, so the pipeline is single-pass and cache-friendly). The
target for full video analysis is to populate the `video_ad`-only fields above by sampling multiple
frames (synopsis / scene transcript) and, where audio is available, a transcript for
`spoken_script_exact`. The video **prompt** already reflects the video approach regardless.

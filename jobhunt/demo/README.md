# JobHunt — Cinematic Demo

A self-contained, dependency-free visual walkthrough of the whole platform:
the 7-agent orchestra, live discovery, evidence-cited résumé tailoring,
peer-critique + human approval, the submission router, and the self-moving
tracking board.

## Files

| File | What it is |
|------|------------|
| `demo.html` | The demo itself — **one file, no build step, no network required** (web fonts load when online, fall back to system fonts offline). Open it in any browser. |
| `jobhunt-demo.mp4` | Pre-rendered 1280×720 recording of the full ~44s playback (H.264). |
| `_record.py` | Regenerates the MP4/WebM by screen-recording the live page headlessly. |
| `_capture.py` | Smoke-check: renders one still per scene and fails on any JS error. |

## Watch it

- **Video:** open `jobhunt-demo.mp4`.
- **Interactive:** open `demo.html` in a browser. Hover to reveal the
  scrubber. Controls: **Space** play/pause · **← / →** step scenes ·
  **R** replay. It auto-plays through all 8 scenes and is sized 16:9 for
  clean screen recording.

## Scenes

1. **Intro** — brand reveal, 3D logo assemble.
2. **The Orchestra** — orchestrator + 7 agents as a constellation, animated
   connectors with traveling data sparks.
3. **Discovery** — Greenhouse / Lever / Ashby / Indeed stream live jobs.
4. **Resume Architect** — résumé builds itself; every bullet links to an
   `evidence_id` (no hallucinated experience).
5. **Critique → Approve** — peer-critique fit gauge, human ship verdict.
6. **Submission Router** — auto-routes each application to the right ATS.
7. **Tracking** — inbox classifies replies; the kanban advances itself.
8. **The Payoff** — animated stat roll-up + capability chips.

## Regenerate the video

```bash
pip install playwright imageio-ffmpeg
python jobhunt/demo/_record.py                 # -> jobhunt-demo.webm
# transcode to mp4:
python - <<'PY'
import imageio_ffmpeg, subprocess
ff = imageio_ffmpeg.get_ffmpeg_exe()
subprocess.run([ff,"-y","-i","jobhunt/demo/jobhunt-demo.webm",
  "-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",
  "-crf","20","jobhunt/demo/jobhunt-demo.mp4"], check=True)
PY
```

> The demo uses representative sample data to show the *end-state* UX. It is a
> design/marketing artifact, not a live capture of the running dashboard
> (`python -m jobhunt serve`).

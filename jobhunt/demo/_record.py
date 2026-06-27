"""Record the full cinematic playback to a webm video, headlessly.

Playwright records the real animated playback (vp8/webm) — no ffmpeg needed.
Output: jobhunt/demo/jobhunt-demo.webm
"""
import pathlib, shutil
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
URL = (HERE / "demo.html").as_uri()
VID_DIR = HERE / "_vid"
VID_DIR.mkdir(exist_ok=True)

TOTAL_MS = 45000  # full timeline (~44.2s) + tail

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 720},
        record_video_dir=str(VID_DIR),
        record_video_size={"width": 1280, "height": 720},
    )
    page = ctx.new_page()
    page.goto(URL)
    page.evaluate("setScene(0); play();")  # ensure from the top, playing
    page.wait_for_timeout(TOTAL_MS)
    video_path = page.video.path()
    ctx.close()  # finalizes the webm
    browser.close()

out = HERE / "jobhunt-demo.webm"
shutil.move(video_path, out)
shutil.rmtree(VID_DIR, ignore_errors=True)
print("wrote", out, out.stat().st_size, "bytes")

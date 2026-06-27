"""Drive the cinematic demo headlessly and capture one frame per scene.

Used as a smoke-check that every scene renders without JS errors, and to
produce thumbnails. Not part of the test suite.
"""
import pathlib, sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
URL = (HERE / "demo.html").as_uri()
OUT = HERE / "frames"
OUT.mkdir(exist_ok=True)

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
    page = browser.new_page(viewport={"width": 1280, "height": 720},
                            device_scale_factor=2)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_timeout(500)
    n = page.eval_on_selector_all(".scene", "els => els.length")
    print(f"scenes found: {n}")
    for i in range(n):
        page.evaluate(f"setScene({i}); pause();")
        page.wait_for_timeout(2200)  # let scene animations settle
        page.screenshot(path=str(OUT / f"scene_{i}.png"))
        print(f"captured scene {i}")
    browser.close()

if errors:
    print("JS ERRORS:", *errors, sep="\n  ")
    sys.exit(1)
print("OK — no JS errors")

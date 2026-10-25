"""Optional Playwright E2E against real FastAPI/PostgreSQL with test-only AI fixtures.

Run with a Python containing playwright. APP_PYTHON can select the application venv.
The child server creates/removes its own schema; it never calls Gemini.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8002"


def main():
    default_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    app_python = os.getenv("APP_PYTHON", str(default_python if default_python.exists() else sys.executable))
    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen([app_python, str(ROOT / "tests/browser_server.py")], cwd=ROOT,
            env={**os.environ, "RUN_BROWSER_TESTS": "1"}, stdout=output, stderr=output)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Smoke server exited before startup")
                try:
                    urllib.request.urlopen(BASE + "/", timeout=0.3)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Smoke server failed to start")
            with sync_playwright() as p:
                executable = os.getenv("CHROMIUM_EXECUTABLE")
                browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(BASE, wait_until="networkidle")
                original = "😀 Мен кітап оқиды. Кітап пайдалы. <script>alert(1)</script>"
                page.locator("#title").fill("Климат")
                page.locator("#essay").fill(original)
                page.locator("#consent").check()
                page.locator("#analyze").click()
                page.wait_for_function("()=>document.querySelector('#total-score').textContent==='25'")
                page.locator("#topic-alignment .retry-section").wait_for(state="visible")
                assert page.locator("#annotated-text mark").count() == 2
                assert page.locator("#criteria .criterion-head h3").all_text_contents() == ["Құрылым", "Аргументация", "Логика", "Дәлелдер", "Тілдік сапа"]
                assert page.locator("#annotated-text script").count() == 0
                assert page.locator("#annotated-text").inner_text() == original
                first_id = page.evaluate("()=>activeResult.id")
                page.reload(wait_until="networkidle")
                page.wait_for_function("()=>document.querySelector('#total-score').textContent==='25'")
                page.locator("#topic-alignment .retry-section").click()
                page.get_by_text("Эссе тақырыпқа сай емес", exact=True).wait_for(state="visible")
                counts = page.request.get(BASE + "/__test__/counts").json()
                assert counts == {"grades": 1, "topics": 2}, counts
                page.locator("#annotated-text mark").first.focus()
                page.keyboard.press("Enter")
                page.locator("#annotation-details .secondary").click()
                assert page.locator("#revision-info").is_visible()
                assert not page.locator("#consent").is_checked()
                revised = "😀 Мен кітап оқимын. Кітап пайдалы, себебі ол сөздік қорды байытады. <script>alert(1)</script>"
                page.locator("#essay").fill(revised)
                page.locator("#consent").check()
                page.locator("#analyze").click()
                page.wait_for_function("()=>document.querySelector('#total-score').textContent==='35'")
                page.locator(".score-delta").wait_for(state="visible")
                assert "+10" in page.locator(".score-delta").inner_text()
                assert page.locator(".score-table tbody tr").count() == 5
                second_id = page.evaluate("()=>activeResult.id")
                assert second_id != first_id
                assert page.request.get(f"{BASE}/api/essays/{first_id}").json()["essay_text"] == original
                assert page.request.get(f"{BASE}/api/essays/{second_id}").json()["parent_id"] == first_id
                assert page.locator("#annotated-text mark").count() == 0
                page.get_by_text("Қазіргі нұсқадағы нақты ескертулер", exact=True).click()
                assert page.locator("#comparison-panel script").count() == 0
                page.locator("#analyze").click()
                page.wait_for_timeout(500)
                assert page.evaluate("()=>activeResult.id") == second_id
                counts = page.request.get(BASE + "/__test__/counts").json()
                assert counts == {"grades": 2, "topics": 3}, counts
                page.set_viewport_size({"width": 390, "height": 844})
                assert not page.evaluate("()=>document.documentElement.scrollWidth>innerWidth")
                page.screenshot(path=str(Path(tempfile.gettempdir()) / "esse-workflow-mobile.png"), full_page=True)
                page.reload(wait_until="networkidle")
                page.wait_for_function("()=>document.querySelector('#total-score').textContent==='35'")
                assert page.evaluate("()=>activeResult.id") == second_id
                page.locator("#consent").check()
                page.locator("#analyze").click()
                page.wait_for_timeout(500)
                assert page.request.get(BASE + "/__test__/counts").json() == {"grades": 2, "topics": 3}
                # A delayed response for an older view must not replace the selected version.
                old_data = page.request.get(f"{BASE}/api/essays/{first_id}").json()
                held = []
                page.route(f"**/api/essays/{first_id}", lambda route: held.append(route))
                page.evaluate(f"()=>{{openEssay({first_id});}}")
                for _ in range(30):
                    if held:
                        break
                    page.wait_for_timeout(20)
                assert held
                page.evaluate(f"()=>openEssay({second_id})")
                held[0].fulfill(json=old_data)
                page.wait_for_timeout(250)
                assert page.evaluate("()=>activeResult.id") == second_id
                assert not errors, errors
                browser.close()
            print("PASS: partial result → reload → topic-only retry → keyboard feedback → edit revision → score/text comparison → duplicate-submit protection → mobile/legacy-safe rendering. AI fixtures only.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.returncode not in (0, -15):
                output.seek(0)
                # Logs contain only isolated test inputs; never dump runtime environment.
                print(output.read()[-4000:], file=sys.stderr)


if __name__ == "__main__":
    main()

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
                page.goto(BASE + "/auth", wait_until="networkidle")
                page.locator("#switch-auth").click()
                page.locator("#register-username").fill("browser_user")
                page.locator("#register-email").fill("browser@example.com")
                page.locator("#register-password").fill("abcd")
                page.locator("#register-confirmation").fill("abcd")
                page.locator('[data-password="register-password"]').click()
                assert page.locator("#register-password").get_attribute("type") == "text"
                page.locator('[data-password="register-password"]').click()
                page.locator("#register-form button[type=submit]").click()
                page.locator("#verification-code").wait_for(state="visible")
                code = page.request.get(BASE + "/__test__/mail").json()["code"]
                page.locator("#verification-code").fill(code)
                page.locator("#code-form button[type=submit]").click()
                page.wait_for_url(BASE + "/profile")
                page.locator("#profile-name").wait_for(state="visible")
                assert page.locator("#profile-name").inner_text() == "browser_user"
                page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-profile-desktop.png"), full_page=True)
                page.locator("#profile-toggle").click()
                assert page.locator('#profile-menu a[href="/admin"]').count() == 0
                page.keyboard.press("Escape")
                page.goto(BASE, wait_until="networkidle")
                page.evaluate("()=>document.fonts.ready")
                # OCR remains a keyboard-accessible action after the visual redesign.
                page.locator("#upload-image").focus()
                with page.expect_file_chooser() as chooser:
                    page.keyboard.press("Enter")
                assert not chooser.value.is_multiple()
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
                page.locator("#comparison-panel").screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-editorial-comparison.png"))
                for width in (320, 390, 768, 1024):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert not page.evaluate("()=>document.documentElement.scrollWidth>innerWidth"), width
                page.set_viewport_size({"width": 390, "height": 844})
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
                # Profile changes, verified email replacement and real session invalidation.
                page.goto(BASE + "/profile", wait_until="networkidle")
                page.locator("#profile-username").fill("reader_new")
                page.locator("#profile-form button[type=submit]").click()
                page.get_by_text("Логин сақталды.", exact=True).wait_for()
                page.locator("#current-password").fill("abcd")
                page.locator("#new-password").fill("zzzz")
                page.locator("#new-confirmation").fill("zzzz")
                page.locator("#password-form button[type=submit]").click()
                page.get_by_text("Құпиясөз өзгертілді. Басқа құрылғылардағы сессиялар аяқталды.", exact=True).wait_for()
                page.locator("#new-email").fill("updated@example.com")
                page.locator("#email-password").fill("zzzz")
                page.locator("#email-form button[type=submit]").click()
                page.locator("#verification-code").wait_for(state="visible")
                assert page.request.get(BASE + "/api/auth/me").json()["user"]["email"] == "browser@example.com"
                code = page.request.get(BASE + "/__test__/mail").json()["code"]
                page.locator("#verification-code").fill(code)
                page.locator("#code-form button[type=submit]").click()
                page.get_by_text("Жаңа пошта расталды.", exact=True).wait_for()
                assert page.locator("#profile-email").inner_text() == "updated@example.com"
                page.locator("#profile-toggle").click()
                page.get_by_role("button", name="Аккаунттан шығу", exact=True).click()
                page.wait_for_url(BASE + "/")
                page.goto(BASE + "/auth", wait_until="networkidle")
                page.locator('#login-form [data-mode="forgot"]').click()
                page.locator("#forgot-identifier").fill("updated@example.com")
                page.request.post(BASE + "/__test__/allow-mail")
                page.locator("#forgot-form button[type=submit]").click()
                page.locator("#verification-code").wait_for(state="visible")
                page.locator("#verification-code").fill(page.request.get(BASE + "/__test__/mail").json()["code"])
                page.locator("#reset-password").fill("1111")
                page.locator("#reset-confirmation").fill("1111")
                page.locator("#code-form button[type=submit]").click()
                page.get_by_text("Құпиясөз өзгертілді. Қайта кіріңіз.", exact=True).wait_for()
                page.locator("#login-identifier").fill("reader_new")
                page.locator("#login-password").fill("1111")
                page.locator("#login-form button[type=submit]").click()
                page.wait_for_url(BASE + "/profile")
                # A separate browser context becomes admin only after mailbox proof.
                admin_context = browser.new_context(viewport={"width": 1280, "height": 1000})
                admin_page = admin_context.new_page()
                admin_page.set_default_timeout(15000)
                admin_page.on("dialog", lambda dialog: dialog.accept())
                admin_page.on("pageerror", lambda error: errors.append(str(error)))
                admin_page.goto(BASE + "/auth", wait_until="networkidle")
                admin_page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-auth-login.png"), full_page=True)
                admin_page.locator("#switch-auth").click()
                admin_page.locator("#register-username").fill("owner")
                admin_page.locator("#register-email").fill("owner@example.com")
                admin_page.locator("#register-password").fill("abcd")
                admin_page.locator("#register-confirmation").fill("abcd")
                admin_page.wait_for_timeout(700)
                admin_page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-auth-register.png"), full_page=True)
                for width in (320, 390, 768, 1024):
                    admin_page.set_viewport_size({"width": width, "height": 844})
                    assert not admin_page.evaluate("()=>document.documentElement.scrollWidth>innerWidth"), width
                    admin_page.wait_for_timeout(750 if width > 600 else 50)
                    assert admin_page.locator("#register-username").evaluate("(input)=>{const r=input.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth;}"), width
                admin_page.set_viewport_size({"width": 390, "height": 844})
                admin_page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-auth-mobile.png"), full_page=True)
                admin_page.set_viewport_size({"width": 1280, "height": 1000})
                admin_page.locator("#register-form button[type=submit]").click()
                admin_page.locator("#verification-code").wait_for(state="visible")
                admin_page.locator("#verification-code").fill(admin_page.request.get(BASE + "/__test__/mail").json()["code"])
                admin_page.locator("#code-form button[type=submit]").click()
                admin_page.wait_for_url(BASE + "/profile")
                admin_page.goto(BASE + "/admin", wait_until="networkidle")
                admin_page.locator(".admin-stats").wait_for()
                admin_page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-admin-desktop.png"), full_page=True)
                admin_page.locator('[data-admin="users"]').click()
                row = admin_page.locator("tbody tr").filter(has_text="reader_new")
                row.get_by_role("button", name="Бұғаттау", exact=True).click()
                row.get_by_role("button", name="Ашу", exact=True).wait_for()
                assert page.request.get(BASE + "/api/auth/me").json()["user"] is None
                row.get_by_role("button", name="Ашу", exact=True).click()
                row.get_by_role("button", name="Бұғаттау", exact=True).wait_for()
                for section in ("pending", "services", "audit"):
                    admin_page.locator(f'[data-admin="{section}"]').click()
                    admin_page.locator("#admin-content h2").wait_for()
                for width in (320, 390, 768, 1024):
                    admin_page.set_viewport_size({"width": width, "height": 844})
                    assert not admin_page.evaluate("()=>document.documentElement.scrollWidth>innerWidth"), width
                admin_page.set_viewport_size({"width": 390, "height": 844})
                admin_page.screenshot(path=str(Path(tempfile.gettempdir()) / "tekser-admin-mobile.png"), full_page=True)
                admin_context.close()
                assert not errors, errors
                browser.close()
            print("PASS: verified registration and eye toggles → private essay analysis/revision → profile/password/verified-email changes → logout/reset/login → separate admin, block/unblock and session revocation → responsive layouts. AI and email fixtures only.")
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

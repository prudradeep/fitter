import os
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class DesktopHostedSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("DR_TRANSITION_DESKTOP_SMOKE") != "1":
            raise unittest.SkipTest(
                "Set DR_TRANSITION_DESKTOP_SMOKE=1 to run the desktop Playwright smoke test."
            )
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("Install the browser test extra and Playwright browsers.") from exc

        cls.exe_path = Path(os.getenv("DR_TRANSITION_DESKTOP_EXE", "")).expanduser()
        if not cls.exe_path.is_file():
            raise unittest.SkipTest("Set DR_TRANSITION_DESKTOP_EXE to the installed DrTransition.exe.")
        cls.backend_url = os.getenv("DR_TRANSITION_HOSTED_TEST_BACKEND_URL", "").rstrip("/")
        if not cls.backend_url:
            raise unittest.SkipTest("Set DR_TRANSITION_HOSTED_TEST_BACKEND_URL to a hosted test backend.")

        cls.email = os.getenv("DR_TRANSITION_TEST_EMAIL", "")
        cls.password = os.getenv("DR_TRANSITION_TEST_PASSWORD", "")
        cls.debug_port = int(os.getenv("DR_TRANSITION_DESKTOP_DEBUG_PORT", "9339"))
        cls.PlaywrightTimeoutError = PlaywrightTimeoutError
        cls.playwright = sync_playwright().start()
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.process = None

    @classmethod
    def tearDownClass(cls):
        process = getattr(cls, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.close()
        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            playwright.stop()
        temp_dir = getattr(cls, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def test_desktop_launches_hosted_backend_in_webview(self):
        local_app_data = Path(self.temp_dir.name) / "LocalAppData"
        override_dir = local_app_data / "DrTransition"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / ".env").write_text(
            "\n".join(
                [
                    f"DR_TRANSITION_BACKEND_URL={self.backend_url}/",
                    f"DR_TRANSITION_BACKEND_HEALTH_URL={self.backend_url}/health/ready",
                    f"DR_TRANSITION_BACKEND_AUTH_CHECK_URL={self.backend_url}/api/sessions",
                    "DR_TRANSITION_GROUNDING_ENABLED=false",
                    "OLLAMA_BASE_URL=http://127.0.0.1:11434",
                    f"OLLAMA_MODEL={os.getenv('OLLAMA_MODEL', 'mistral-nemo')}",
                    f"OLLAMA_EMBEDDING_MODEL={os.getenv('OLLAMA_EMBEDDING_MODEL', 'nomic-embed-text')}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(local_app_data)
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={self.debug_port} --remote-allow-origins=*"
        )
        self.__class__.process = subprocess.Popen(
            [str(self.exe_path)],
            cwd=str(self.exe_path.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cdp_url = self._wait_for_cdp_url()
        self.__class__.browser = self.playwright.chromium.connect_over_cdp(cdp_url)
        context = self.browser.contexts[0]
        page = self._first_page(context)
        page.wait_for_load_state("domcontentloaded", timeout=30_000)

        if page.locator("text=Setup needed before Dr Transition can start").count():
            self.fail("Desktop opened diagnostics instead of the hosted backend app.")

        page.wait_for_function(
            "(expected) => window.location.href.startsWith(expected)",
            arg=self.backend_url,
            timeout=30_000,
        )
        if self.email and self.password and page.locator("form.auth-form[action='/login']").count():
            page.locator("form.auth-form[action='/login'] input[name='email']").fill(self.email)
            page.locator("form.auth-form[action='/login'] input[name='password']").fill(self.password)
            page.locator("form.auth-form[action='/login']").evaluate("form => form.submit()")
            page.wait_for_selector("#chatForm", timeout=30_000)
        else:
            page.wait_for_selector("form.auth-form[action='/login'], #chatForm", timeout=30_000)

    def _wait_for_cdp_url(self) -> str:
        endpoint = f"http://127.0.0.1:{self.debug_port}/json/version"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint, timeout=1) as response:
                    if response.status == 200:
                        return f"http://127.0.0.1:{self.debug_port}"
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        self.fail("Timed out waiting for WebView2 remote debugging endpoint.")

    def _first_page(self, context):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if context.pages:
                return context.pages[0]
            time.sleep(0.25)
        self.fail("Desktop WebView did not expose a Playwright page.")

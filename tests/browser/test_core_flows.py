import json
import os
import tempfile
import unittest


class BrowserSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("DR_TRANSITION_BROWSER_TESTS") != "1":
            raise unittest.SkipTest("Set DR_TRANSITION_BROWSER_TESTS=1 to run browser smoke tests.")
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("Install the browser test extra and Playwright browsers.") from exc

        cls.base_url = os.getenv("DR_TRANSITION_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        cls.email = os.getenv("DR_TRANSITION_TEST_EMAIL", "admin@example.com")
        cls.password = os.getenv("DR_TRANSITION_TEST_PASSWORD", "admin123")
        cls.PlaywrightTimeoutError = PlaywrightTimeoutError
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.close()
        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def login(self):
        self.page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        if self.page.locator("#chatForm").count():
            return
        if self.page.locator("#startAnalysisButton").count():
            self.page.locator("#startAnalysisButton").click()
        self.page.locator("form.auth-form[action='/login'] input[name='email']").fill(self.email)
        self.page.locator("form.auth-form[action='/login'] input[name='password']").fill(self.password)
        self.page.locator("form.auth-form[action='/login']").evaluate("form => form.submit()")
        self.page.wait_for_selector("#chatForm", timeout=15_000)

    def wait_for_session_id(self):
        self.page.wait_for_function(
            "() => Boolean(window.localStorage.getItem('dr_transition_session_id'))",
            timeout=15_000,
        )
        return self.page.evaluate("window.localStorage.getItem('dr_transition_session_id')")

    def test_login_chat_option_and_knowledge_dialog(self):
        self.login()
        self.page.locator("#messageInput").fill("Germany")
        self.page.locator("#chatForm").evaluate("form => form.requestSubmit()")
        self.page.wait_for_function(
            "() => document.querySelectorAll('#chatLog .bubble.bot, #chatLog .bubble.assistant').length > 0",
            timeout=30_000,
        )
        option = self.page.locator("#optionTray button").first
        option.wait_for(timeout=30_000)
        option.click()
        self.page.wait_for_timeout(300)

        self.page.locator("#knowledgeButton").click()
        self.page.wait_for_selector("#knowledgeDialog[open], #knowledgeDialog:not([hidden])")
        self.page.locator("#knowledgeSearchInput").fill("transition")
        self.page.locator("#knowledgeSearchForm").evaluate("form => form.requestSubmit()")
        self.page.wait_for_selector("#knowledgeResults", timeout=15_000)

    def test_session_restore_export_and_import(self):
        self.login()
        self.page.locator("#messageInput").fill("Italy")
        self.page.locator("#chatForm").evaluate("form => form.requestSubmit()")
        session_id = self.wait_for_session_id()

        with self.page.expect_download(timeout=15_000) as download_info:
            self.page.locator("#exportSessionButton").click()
        download = download_info.value
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as export_file:
            export_path = export_file.name
        download.save_as(export_path)

        with open(export_path, "r", encoding="utf-8") as handle:
            exported = json.load(handle)
        self.assertFalse(exported.get("error"), exported)
        self.assertEqual(exported.get("session", {}).get("session_id"), session_id)

        self.page.locator("#sessionsButton").click()
        self.page.wait_for_selector("#sessionsPanel:not([hidden])")
        self.page.locator("#sessionsList button").first.wait_for(timeout=15_000)
        self.page.locator("#sessionsList button").first.click()
        self.page.wait_for_selector("#chatForm", timeout=15_000)

        self.page.locator("#importSessionInput").set_input_files(export_path)
        self.page.locator("#importSessionButton").click()
        self.page.wait_for_function(
            "() => /Imported session/i.test(document.querySelector('#exportSessionStatus')?.textContent || '')",
            timeout=20_000,
        )

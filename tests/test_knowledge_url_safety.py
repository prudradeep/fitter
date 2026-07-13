import unittest
from unittest.mock import patch

from app.services.knowledge_base import _fetch_public_url


class FakeRedirectResponse:
    is_redirect = True
    headers = {"location": "http://127.0.0.1/private"}
    url = "https://example.com/start"
    encoding = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        return None


class FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.requested_urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str):
        self.requested_urls.append(url)
        return FakeRedirectResponse()


class KnowledgeUrlSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_target_is_validated_before_following(self) -> None:
        validated_urls: list[str] = []

        def fake_validate(url: str) -> None:
            validated_urls.append(url)
            if "127.0.0.1" in url:
                raise ValueError("URL must resolve to a public network address.")

        with (
            patch("app.services.knowledge_base.httpx.AsyncClient", FakeAsyncClient),
            patch("app.services.knowledge_base._validate_public_http_url", fake_validate),
        ):
            with self.assertRaisesRegex(ValueError, "public network address"):
                await _fetch_public_url("https://example.com/start", 1024)

        self.assertEqual(
            validated_urls,
            ["https://example.com/start", "http://127.0.0.1/private"],
        )


if __name__ == "__main__":
    unittest.main()

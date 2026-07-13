import unittest
import asyncio

from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.security import apply_security_headers, create_csrf_token, csrf_origin_allowed, csrf_request_allowed


def build_request(
    method: str,
    *,
    origin: str | None = None,
    cookie: str | None = None,
    csrf_header: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"host", b"example.com")]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("ascii")))
    if csrf_header is not None:
        headers.append((b"x-csrf-token", csrf_header.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "server": ("example.com", 443),
            "path": "/api/chat",
            "headers": headers,
        }
    )


class CsrfProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            app_env="production",
            app_debug=False,
            secret_key="strong-production-secret",
            database_url="mysql+pymysql://user:strong-password@db.example/app",
            cors_origins="https://example.com",
        )

    def test_authenticated_unsafe_request_requires_origin(self) -> None:
        request = build_request("POST", cookie="dr_transition_auth=value")

        self.assertFalse(csrf_origin_allowed(request, self.settings))

    def test_authenticated_unsafe_request_allows_same_origin(self) -> None:
        request = build_request(
            "POST",
            origin="https://example.com",
            cookie="dr_transition_auth=value",
        )

        self.assertTrue(csrf_origin_allowed(request, self.settings))

    def test_unauthenticated_request_is_not_blocked_by_csrf(self) -> None:
        request = build_request("POST")

        self.assertTrue(csrf_origin_allowed(request, self.settings))

    def test_authenticated_unsafe_request_requires_matching_csrf_token(self) -> None:
        token = create_csrf_token(self.settings)
        request = build_request(
            "POST",
            origin="https://example.com",
            cookie=f"dr_transition_auth=value; dr_transition_csrf={token}",
            csrf_header=token,
        )

        self.assertTrue(asyncio.run(csrf_request_allowed(request, self.settings)))

    def test_authenticated_unsafe_request_rejects_missing_csrf_token(self) -> None:
        token = create_csrf_token(self.settings)
        request = build_request(
            "POST",
            origin="https://example.com",
            cookie=f"dr_transition_auth=value; dr_transition_csrf={token}",
        )

        self.assertFalse(asyncio.run(csrf_request_allowed(request, self.settings)))


class SecurityHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            app_env="production",
            app_debug=False,
            secret_key="strong-production-secret",
            database_url="mysql+pymysql://user:strong-password@db.example/app",
            cors_origins="https://example.com",
        )

    def test_security_headers_are_added_for_https(self) -> None:
        request = build_request("GET")
        response = Response()

        apply_security_headers(response, request, self.settings)

        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("microphone=(self)", response.headers["Permissions-Policy"])
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])

    def test_hsts_is_only_added_for_https(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "server": ("example.com", 80),
                "path": "/",
                "headers": [(b"host", b"example.com")],
            }
        )
        response = Response()

        apply_security_headers(response, request, self.settings)

        self.assertNotIn("Strict-Transport-Security", response.headers)


if __name__ == "__main__":
    unittest.main()

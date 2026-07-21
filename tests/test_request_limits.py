import asyncio
import unittest

from starlette.requests import ClientDisconnect, Request

from app.routes.request_limits import ClientDisconnected, read_limited_json


class RequestLimitTests(unittest.TestCase):
    def test_read_limited_json_reports_client_disconnect(self) -> None:
        async def receive() -> dict[str, object]:
            raise ClientDisconnect()

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "server": ("testserver", 80),
                "path": "/api/sync/exchange",
                "headers": [],
            },
            receive=receive,
        )

        with self.assertRaises(ClientDisconnected) as raised:
            asyncio.run(read_limited_json(request, 1024, "Sync payload"))

        self.assertIn("Sync payload upload was interrupted", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os

import uvicorn

from runtime_stdio import ensure_standard_streams


def main() -> None:
    ensure_standard_streams()
    from app.grounding_servers.reranker import app

    host = os.getenv("DRTRANSITION_RERANKER_HOST", "127.0.0.1")
    port = int(os.getenv("DRTRANSITION_RERANKER_PORT", "8081"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

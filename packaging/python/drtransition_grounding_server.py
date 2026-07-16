from __future__ import annotations

import argparse
import os

import uvicorn

from runtime_stdio import ensure_standard_streams


def main() -> None:
    ensure_standard_streams()
    parser = argparse.ArgumentParser(description="Run a Dr Transition grounding service.")
    parser.add_argument("--service", choices=("reranker", "nli"), required=True)
    args = parser.parse_args()

    if args.service == "reranker":
        from app.grounding_servers.reranker import app

        host = os.getenv("DRTRANSITION_RERANKER_HOST", "127.0.0.1")
        port = int(os.getenv("DRTRANSITION_RERANKER_PORT", "8081"))
    else:
        from app.grounding_servers.nli import app

        host = os.getenv("DRTRANSITION_NLI_HOST", "127.0.0.1")
        port = int(os.getenv("DRTRANSITION_NLI_PORT", "8082"))

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

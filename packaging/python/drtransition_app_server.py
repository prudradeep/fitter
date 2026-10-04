from __future__ import annotations

import os
import sys

import uvicorn

from runtime_stdio import ensure_standard_streams


def main() -> None:
    ensure_standard_streams()
    if "--seed-database" in sys.argv:
        from app.seed_data import main as seed_main

        sys.argv = [sys.argv[0], *[arg for arg in sys.argv[1:] if arg != "--seed-database"]]
        seed_main()
        return

    if "--install-offline-seed-bundle" in sys.argv:
        from app.config import get_settings
        from app.services.seed_index_bundle import install_offline_seed_bundle

        print(install_offline_seed_bundle(get_settings()), flush=True)
        return

    from app.main import app

    host = os.getenv("DRTRANSITION_APP_HOST", "127.0.0.1")
    port = int(os.getenv("DRTRANSITION_APP_PORT", "8000"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

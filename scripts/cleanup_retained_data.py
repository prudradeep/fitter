import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.maintenance import cleanup_retained_data


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        result = cleanup_retained_data(db, settings)
    finally:
        db.close()
    print(
        "Cleanup complete: "
        + ", ".join(f"{name}={count}" for name, count in sorted(result.items()))
    )


if __name__ == "__main__":
    main()

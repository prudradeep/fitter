import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeDocument
from app.resource_paths import resource_path
from app.services.knowledge_base import (
    MAIN_KB_SCOPE,
    KnowledgeBaseService,
    extract_file_chunks,
    file_source_type,
)

logger = logging.getLogger(__name__)

BUNDLED_KB_SOURCE_PREFIX = "bundled-kb/"


async def ingest_bundled_main_kb_pdfs(db: Session) -> dict[str, object]:
    kb_dir = resource_path("kb")
    pdf_paths = sorted(kb_dir.glob("*.pdf")) if kb_dir.exists() else []
    if not pdf_paths:
        logger.info("No bundled KB PDFs found at %s", kb_dir)
        return {"found": 0, "ingested": 0, "skipped": 0, "failures": []}

    source_uris = [bundled_kb_source_uri(path) for path in pdf_paths]
    logger.info("Bundled Main KB PDF ingest starting: found=%s directory=%s", len(pdf_paths), kb_dir)
    existing_source_uris = {
        value
        for value in db.scalars(
            select(KnowledgeDocument.source_uri).where(
                KnowledgeDocument.user_id.is_(None),
                KnowledgeDocument.scope == MAIN_KB_SCOPE,
                KnowledgeDocument.source_uri.in_(source_uris),
            )
        ).all()
        if value
    }

    service = KnowledgeBaseService(db, None, scope=MAIN_KB_SCOPE)
    failures: list[dict[str, str]] = []
    ingested = 0
    skipped = 0
    for path in pdf_paths:
        source_uri = bundled_kb_source_uri(path)
        if source_uri in existing_source_uris:
            logger.info("Bundled Main KB PDF already ingested; skipping: %s", path.name)
            skipped += 1
            continue
        try:
            logger.info("Bundled Main KB PDF ingest started: %s", path.name)
            result = await service.ingest_chunks(
                extract_file_chunks(path.name, path.read_bytes()),
                path.name,
                file_source_type(path.name),
                source_uri,
            )
        except Exception as exc:
            logger.exception("Failed to ingest bundled KB PDF: %s", path)
            failures.append({"source": path.name, "detail": str(exc)})
            continue
        if result.get("error"):
            logger.warning(
                "Bundled Main KB PDF ingest failed: %s detail=%s",
                path.name,
                result.get("detail") or "Could not ingest bundled PDF.",
            )
            failures.append(
                {
                    "source": path.name,
                    "detail": str(result.get("detail") or "Could not ingest bundled PDF."),
                }
            )
            continue
        ingested += 1
        existing_source_uris.add(source_uri)
        logger.info("Bundled Main KB PDF ingest completed: %s", path.name)

    logger.info(
        "Bundled Main KB PDF ingest complete: found=%s ingested=%s skipped=%s failed=%s",
        len(pdf_paths),
        ingested,
        skipped,
        len(failures),
    )
    return {
        "found": len(pdf_paths),
        "ingested": ingested,
        "skipped": skipped,
        "failures": failures,
    }


def bundled_kb_source_uri(path: Path) -> str:
    return f"{BUNDLED_KB_SOURCE_PREFIX}{path.name}"

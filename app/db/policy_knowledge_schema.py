"""Ensure the policy-document link table on installations without auto-migration."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import inspect, insert, select, text
from sqlalchemy.engine import Connection

from app.models import KnowledgeDocument, PolicyKnowledgeDocument


def ensure_policy_knowledge_document_schema(connection: Connection) -> bool:
    """Create the link table and backfill legacy policy documents if needed."""
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if not {"policies", "knowledge_documents"} <= table_names:
        return False

    table = PolicyKnowledgeDocument.__table__
    created = table.name not in table_names
    if created:
        if connection.dialect.name == "mysql":
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS policy_knowledge_documents (
                  id CHAR(36) PRIMARY KEY,
                  policy_id CHAR(36) NOT NULL,
                  knowledge_document_id CHAR(36) NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CONSTRAINT fk_policy_knowledge_policy FOREIGN KEY (policy_id)
                    REFERENCES policies(id) ON DELETE CASCADE,
                  CONSTRAINT fk_policy_knowledge_document FOREIGN KEY (knowledge_document_id)
                    REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                  CONSTRAINT uq_policy_knowledge_document UNIQUE (policy_id, knowledge_document_id),
                  INDEX ix_policy_knowledge_policy (policy_id),
                  INDEX ix_policy_knowledge_document (knowledge_document_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """))
        else:
            table.create(bind=connection, checkfirst=True)

    existing = set(connection.execute(select(
        table.c.policy_id, table.c.knowledge_document_id,
    )).all())
    legacy = connection.execute(select(
        KnowledgeDocument.__table__.c.policy_id,
        KnowledgeDocument.__table__.c.id,
    ).where(
        KnowledgeDocument.__table__.c.policy_id.is_not(None),
        KnowledgeDocument.__table__.c.scope == "policy_document",
    )).all()
    missing = [
        {"id": str(uuid4()), "policy_id": policy_id, "knowledge_document_id": document_id}
        for policy_id, document_id in legacy
        if (policy_id, document_id) not in existing
    ]
    if missing:
        connection.execute(insert(table), missing)
    return created or bool(missing)

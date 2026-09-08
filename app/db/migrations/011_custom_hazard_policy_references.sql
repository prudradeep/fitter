CREATE TABLE IF NOT EXISTS custom_hazard_policy_references (
  custom_hazard_id CHAR(36) NOT NULL,
  knowledge_document_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (custom_hazard_id, knowledge_document_id),
  CONSTRAINT fk_custom_hazard_policy_references_hazard
    FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE CASCADE,
  CONSTRAINT fk_custom_hazard_policy_references_document
    FOREIGN KEY (knowledge_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  INDEX ix_custom_hazard_policy_references_document_id (knowledge_document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO custom_hazard_policy_references (
  custom_hazard_id,
  knowledge_document_id
)
SELECT custom_hazard_id, id
FROM knowledge_documents
WHERE custom_hazard_id IS NOT NULL
  AND scope = 'policy_reference';

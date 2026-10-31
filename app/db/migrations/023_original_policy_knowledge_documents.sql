CREATE TABLE IF NOT EXISTS original_policy_knowledge_documents (
  id CHAR(36) PRIMARY KEY,
  original_policy_id CHAR(36) NOT NULL,
  knowledge_document_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_original_policy_knowledge_policy FOREIGN KEY (original_policy_id) REFERENCES original_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_original_policy_knowledge_document FOREIGN KEY (knowledge_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  CONSTRAINT uq_original_policy_knowledge_document UNIQUE (original_policy_id, knowledge_document_id),
  INDEX ix_original_policy_knowledge_policy (original_policy_id),
  INDEX ix_original_policy_knowledge_document (knowledge_document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

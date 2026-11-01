CREATE TABLE IF NOT EXISTS policy_knowledge_documents (
  id CHAR(36) PRIMARY KEY,
  policy_id CHAR(36) NOT NULL,
  knowledge_document_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_policy_knowledge_policy FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_policy_knowledge_document FOREIGN KEY (knowledge_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  CONSTRAINT uq_policy_knowledge_document UNIQUE (policy_id, knowledge_document_id),
  INDEX ix_policy_knowledge_policy (policy_id),
  INDEX ix_policy_knowledge_document (knowledge_document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO policy_knowledge_documents (id, policy_id, knowledge_document_id)
SELECT UUID(), policy_id, id
FROM knowledge_documents
WHERE policy_id IS NOT NULL AND scope = 'policy_document';

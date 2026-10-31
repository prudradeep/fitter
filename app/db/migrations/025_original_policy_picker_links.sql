CREATE TABLE IF NOT EXISTS policy_original_sources (
  id CHAR(36) PRIMARY KEY,
  policy_id CHAR(36) NOT NULL,
  original_policy_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_policy_original_sources_policy FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_policy_original_sources_original FOREIGN KEY (original_policy_id) REFERENCES original_policies(id) ON DELETE CASCADE,
  CONSTRAINT uq_policy_original_source_policy UNIQUE (policy_id),
  INDEX ix_policy_original_sources_policy (policy_id),
  INDEX ix_policy_original_sources_original (original_policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

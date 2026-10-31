CREATE TABLE IF NOT EXISTS original_policy_mitigation_policy_relations (
  id CHAR(36) PRIMARY KEY,
  original_policy_id CHAR(36) NOT NULL,
  mitigation_measure_policy_id CHAR(36) NOT NULL,
  source_mitigation_title TEXT NOT NULL,
  excel_row_number INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_original_policy_mitigation_original FOREIGN KEY (original_policy_id) REFERENCES original_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_original_policy_mitigation_mitigation FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
  CONSTRAINT uq_original_policy_mitigation_policy UNIQUE (original_policy_id, mitigation_measure_policy_id),
  INDEX ix_original_policy_mitigation_original (original_policy_id),
  INDEX ix_original_policy_mitigation_mitigation (mitigation_measure_policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

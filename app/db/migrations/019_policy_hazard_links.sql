CREATE TABLE IF NOT EXISTS policy_hazard_links (
  id CHAR(36) PRIMARY KEY,
  policy_id CHAR(36) NOT NULL,
  system_hazard_id CHAR(36) NULL,
  additional_hazard_id CHAR(36) NULL,
  rationale TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_policy_hazard_links_policy FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_policy_hazard_links_system FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
  CONSTRAINT fk_policy_hazard_links_additional FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_policy_system_hazard UNIQUE (policy_id, system_hazard_id),
  CONSTRAINT uq_policy_additional_hazard UNIQUE (policy_id, additional_hazard_id),
  CONSTRAINT chk_policy_hazard_target CHECK ((system_hazard_id IS NULL) <> (additional_hazard_id IS NULL)),
  INDEX ix_policy_hazard_links_policy_id (policy_id),
  INDEX ix_policy_hazard_links_system_hazard_id (system_hazard_id),
  INDEX ix_policy_hazard_links_additional_hazard_id (additional_hazard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

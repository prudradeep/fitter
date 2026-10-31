CREATE TABLE IF NOT EXISTS original_policies (
  id CHAR(36) PRIMARY KEY,
  country_id CHAR(36) NOT NULL,
  policy_name VARCHAR(512) NOT NULL,
  objective TEXT NULL,
  policy_document TEXT NULL,
  excel_row_number INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_original_policies_policy_name UNIQUE (policy_name),
  CONSTRAINT fk_original_policies_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
  INDEX ix_original_policies_country (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS original_policy_sectors (
  id CHAR(36) PRIMARY KEY,
  original_policy_id CHAR(36) NOT NULL,
  sector_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_original_policy_sectors_policy FOREIGN KEY (original_policy_id) REFERENCES original_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_original_policy_sectors_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT uq_original_policy_sector UNIQUE (original_policy_id, sector_id),
  INDEX ix_original_policy_sectors_policy (original_policy_id),
  INDEX ix_original_policy_sectors_sector (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS original_policy_codes (
  id CHAR(36) PRIMARY KEY,
  original_policy_id CHAR(36) NOT NULL,
  code VARCHAR(80) NOT NULL,
  excel_row_number INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_original_policy_codes_policy FOREIGN KEY (original_policy_id) REFERENCES original_policies(id) ON DELETE CASCADE,
  CONSTRAINT uq_original_policy_code UNIQUE (original_policy_id, code),
  INDEX ix_original_policy_codes_policy (original_policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS original_policy_system_hazard (
  id CHAR(36) PRIMARY KEY,
  original_policy_code_id CHAR(36) NOT NULL,
  system_hazard_id CHAR(36) NOT NULL,
  relationship VARCHAR(40) NOT NULL,
  excel_row_number INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_original_policy_system_hazard_code FOREIGN KEY (original_policy_code_id) REFERENCES original_policy_codes(id) ON DELETE CASCADE,
  CONSTRAINT fk_original_policy_system_hazard_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_original_policy_code_hazard_relationship UNIQUE (original_policy_code_id, system_hazard_id, relationship),
  INDEX ix_original_policy_system_hazard_code (original_policy_code_id),
  INDEX ix_original_policy_system_hazard_hazard (system_hazard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

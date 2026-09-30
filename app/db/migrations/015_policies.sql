CREATE TABLE IF NOT EXISTS policies (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  country_id CHAR(36) NOT NULL,
  sector_id CHAR(36) NOT NULL,
  policy TEXT NOT NULL,
  policy_url TEXT NULL,
  language VARCHAR(120) NULL,
  policy_type VARCHAR(120) NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
  excel_row_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_policies_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
  CONSTRAINT fk_policies_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  INDEX ix_policies_country_id (country_id),
  INDEX ix_policies_sector_id (sector_id),
  INDEX ix_policies_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

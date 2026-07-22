CREATE TABLE IF NOT EXISTS prompts (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  prompt_key VARCHAR(255) NOT NULL,
  category VARCHAR(80) NOT NULL DEFAULT 'llm',
  model VARCHAR(120) NULL,
  display_name VARCHAR(255) NOT NULL,
  content LONGTEXT NOT NULL,
  source_path VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT uq_prompts_prompt_key UNIQUE (prompt_key),
  INDEX ix_prompts_prompt_key (prompt_key),
  INDEX ix_prompts_category (category),
  INDEX ix_prompts_model (model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

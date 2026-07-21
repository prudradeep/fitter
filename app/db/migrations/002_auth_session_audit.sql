SET @add_session_version_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE app_users ADD COLUMN session_version INT NOT NULL DEFAULT 1 AFTER password_hash',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'app_users'
    AND column_name = 'session_version'
);
PREPARE add_session_version_stmt FROM @add_session_version_sql;
EXECUTE add_session_version_stmt;
DEALLOCATE PREPARE add_session_version_stmt;

CREATE TABLE IF NOT EXISTS audit_logs (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  user_id CHAR(36) NULL,
  action VARCHAR(120) NOT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'success',
  target_type VARCHAR(80) NULL,
  target_id VARCHAR(160) NULL,
  request_id VARCHAR(64) NULL,
  ip_address VARCHAR(80) NULL,
  user_agent VARCHAR(255) NULL,
  details TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_audit_logs_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL,
  INDEX ix_audit_logs_user_id (user_id),
  INDEX ix_audit_logs_action (action),
  INDEX ix_audit_logs_target_type (target_type),
  INDEX ix_audit_logs_target_id (target_id),
  INDEX ix_audit_logs_request_id (request_id),
  INDEX ix_audit_logs_created_at (created_at)
);

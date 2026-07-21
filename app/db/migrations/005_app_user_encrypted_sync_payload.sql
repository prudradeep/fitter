SET @add_sync_encrypted_payload_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE app_users ADD COLUMN sync_encrypted_payload TEXT NULL AFTER role',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'app_users'
    AND column_name = 'sync_encrypted_payload'
);
PREPARE add_sync_encrypted_payload_stmt FROM @add_sync_encrypted_payload_sql;
EXECUTE add_sync_encrypted_payload_stmt;
DEALLOCATE PREPARE add_sync_encrypted_payload_stmt;

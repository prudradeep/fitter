SET @add_system_inquiry_json_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN system_inquiry_json TEXT NULL AFTER target_groups_json',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'system_inquiry_json'
);
PREPARE add_system_inquiry_json_stmt FROM @add_system_inquiry_json_sql;
EXECUTE add_system_inquiry_json_stmt;
DEALLOCATE PREPARE add_system_inquiry_json_stmt;

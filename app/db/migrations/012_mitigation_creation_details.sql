SET @add_mitigation_creation_details_column_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN creation_details_json TEXT NULL AFTER target_groups_json',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'creation_details_json'
);
PREPARE add_mitigation_creation_details_column_stmt FROM @add_mitigation_creation_details_column_sql;
EXECUTE add_mitigation_creation_details_column_stmt;
DEALLOCATE PREPARE add_mitigation_creation_details_column_stmt;

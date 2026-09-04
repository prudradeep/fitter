SET @add_custom_hazard_summary_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD COLUMN summary TEXT NULL AFTER evidence',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND column_name = 'summary'
);
PREPARE add_custom_hazard_summary_stmt FROM @add_custom_hazard_summary_sql;
EXECUTE add_custom_hazard_summary_stmt;
DEALLOCATE PREPARE add_custom_hazard_summary_stmt;

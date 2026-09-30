SET @add_custom_hazard_policy_column_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD COLUMN mitigation_measure_policy_id CHAR(36) NULL AFTER sector_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND column_name = 'mitigation_measure_policy_id'
);
PREPARE add_custom_hazard_policy_column_stmt FROM @add_custom_hazard_policy_column_sql;
EXECUTE add_custom_hazard_policy_column_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_column_stmt;

SET @add_custom_hazard_policy_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD INDEX ix_custom_hazards_mitigation_measure_policy_id (mitigation_measure_policy_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND index_name = 'ix_custom_hazards_mitigation_measure_policy_id'
);
PREPARE add_custom_hazard_policy_index_stmt FROM @add_custom_hazard_policy_index_sql;
EXECUTE add_custom_hazard_policy_index_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_index_stmt;

SET @add_custom_hazard_policy_fk_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD CONSTRAINT fk_custom_hazards_policy FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE SET NULL',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND constraint_name = 'fk_custom_hazards_policy'
);
PREPARE add_custom_hazard_policy_fk_stmt FROM @add_custom_hazard_policy_fk_sql;
EXECUTE add_custom_hazard_policy_fk_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_fk_stmt;

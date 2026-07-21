SET @add_user_session_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN user_session_id CHAR(36) NULL AFTER id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'user_session_id'
);
PREPARE add_user_session_id_stmt FROM @add_user_session_id_sql;
EXECUTE add_user_session_id_stmt;
DEALLOCATE PREPARE add_user_session_id_stmt;

SET @add_custom_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER user_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'custom_hazard_id'
);
PREPARE add_custom_hazard_id_stmt FROM @add_custom_hazard_id_sql;
EXECUTE add_custom_hazard_id_stmt;
DEALLOCATE PREPARE add_custom_hazard_id_stmt;

SET @add_system_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN system_hazard_id CHAR(36) NULL AFTER custom_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'system_hazard_id'
);
PREPARE add_system_hazard_id_stmt FROM @add_system_hazard_id_sql;
EXECUTE add_system_hazard_id_stmt;
DEALLOCATE PREPARE add_system_hazard_id_stmt;

SET @add_additional_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD COLUMN additional_hazard_id CHAR(36) NULL AFTER system_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'additional_hazard_id'
);
PREPARE add_additional_hazard_id_stmt FROM @add_additional_hazard_id_sql;
EXECUTE add_additional_hazard_id_stmt;
DEALLOCATE PREPARE add_additional_hazard_id_stmt;

SET @relax_user_hazard_id_sql = (
  SELECT IF(
    is_nullable = 'NO',
    'ALTER TABLE user_mitigation_measures MODIFY user_hazard_id CHAR(36) NULL',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND column_name = 'user_hazard_id'
  LIMIT 1
);
PREPARE relax_user_hazard_id_stmt FROM @relax_user_hazard_id_sql;
EXECUTE relax_user_hazard_id_stmt;
DEALLOCATE PREPARE relax_user_hazard_id_stmt;

SET @add_user_session_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD INDEX ix_user_mitigation_measures_user_session_id (user_session_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND index_name = 'ix_user_mitigation_measures_user_session_id'
);
PREPARE add_user_session_index_stmt FROM @add_user_session_index_sql;
EXECUTE add_user_session_index_stmt;
DEALLOCATE PREPARE add_user_session_index_stmt;

SET @add_custom_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD INDEX ix_user_mitigation_measures_custom_hazard_id (custom_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND index_name = 'ix_user_mitigation_measures_custom_hazard_id'
);
PREPARE add_custom_hazard_index_stmt FROM @add_custom_hazard_index_sql;
EXECUTE add_custom_hazard_index_stmt;
DEALLOCATE PREPARE add_custom_hazard_index_stmt;

SET @add_system_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD INDEX ix_user_mitigation_measures_system_hazard_id (system_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND index_name = 'ix_user_mitigation_measures_system_hazard_id'
);
PREPARE add_system_hazard_index_stmt FROM @add_system_hazard_index_sql;
EXECUTE add_system_hazard_index_stmt;
DEALLOCATE PREPARE add_system_hazard_index_stmt;

SET @add_additional_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_mitigation_measures ADD INDEX ix_user_mitigation_measures_additional_hazard_id (additional_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_mitigation_measures'
    AND index_name = 'ix_user_mitigation_measures_additional_hazard_id'
);
PREPARE add_additional_hazard_index_stmt FROM @add_additional_hazard_index_sql;
EXECUTE add_additional_hazard_index_stmt;
DEALLOCATE PREPARE add_additional_hazard_index_stmt;

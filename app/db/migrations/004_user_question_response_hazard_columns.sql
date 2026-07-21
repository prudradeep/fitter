SET @add_response_custom_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD COLUMN custom_hazard_id INT NULL AFTER user_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND column_name = 'custom_hazard_id'
);
PREPARE add_response_custom_hazard_id_stmt FROM @add_response_custom_hazard_id_sql;
EXECUTE add_response_custom_hazard_id_stmt;
DEALLOCATE PREPARE add_response_custom_hazard_id_stmt;

SET @add_response_system_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD COLUMN system_hazard_id INT NULL AFTER custom_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND column_name = 'system_hazard_id'
);
PREPARE add_response_system_hazard_id_stmt FROM @add_response_system_hazard_id_sql;
EXECUTE add_response_system_hazard_id_stmt;
DEALLOCATE PREPARE add_response_system_hazard_id_stmt;

SET @add_response_additional_hazard_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD COLUMN additional_hazard_id INT NULL AFTER system_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND column_name = 'additional_hazard_id'
);
PREPARE add_response_additional_hazard_id_stmt FROM @add_response_additional_hazard_id_sql;
EXECUTE add_response_additional_hazard_id_stmt;
DEALLOCATE PREPARE add_response_additional_hazard_id_stmt;

SET @add_response_custom_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD INDEX ix_user_question_responses_custom_hazard_id (custom_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND index_name = 'ix_user_question_responses_custom_hazard_id'
);
PREPARE add_response_custom_hazard_index_stmt FROM @add_response_custom_hazard_index_sql;
EXECUTE add_response_custom_hazard_index_stmt;
DEALLOCATE PREPARE add_response_custom_hazard_index_stmt;

SET @add_response_system_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD INDEX ix_user_question_responses_system_hazard_id (system_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND index_name = 'ix_user_question_responses_system_hazard_id'
);
PREPARE add_response_system_hazard_index_stmt FROM @add_response_system_hazard_index_sql;
EXECUTE add_response_system_hazard_index_stmt;
DEALLOCATE PREPARE add_response_system_hazard_index_stmt;

SET @add_response_additional_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE user_question_responses ADD INDEX ix_user_question_responses_additional_hazard_id (additional_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'user_question_responses'
    AND index_name = 'ix_user_question_responses_additional_hazard_id'
);
PREPARE add_response_additional_hazard_index_stmt FROM @add_response_additional_hazard_index_sql;
EXECUTE add_response_additional_hazard_index_stmt;
DEALLOCATE PREPARE add_response_additional_hazard_index_stmt;

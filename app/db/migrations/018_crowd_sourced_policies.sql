SET @add_policy_owner_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE policies ADD COLUMN created_by_user_id CHAR(36) NULL',
    'SELECT 1')
  FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'policies' AND column_name = 'created_by_user_id'
);
PREPARE add_policy_owner_stmt FROM @add_policy_owner_sql;
EXECUTE add_policy_owner_stmt;
DEALLOCATE PREPARE add_policy_owner_stmt;

SET @add_policy_crowd_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE policies ADD COLUMN is_crowd_sourced BOOLEAN NOT NULL DEFAULT FALSE',
    'SELECT 1')
  FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'policies' AND column_name = 'is_crowd_sourced'
);
PREPARE add_policy_crowd_stmt FROM @add_policy_crowd_sql;
EXECUTE add_policy_crowd_stmt;
DEALLOCATE PREPARE add_policy_crowd_stmt;

SET @add_policy_owner_index_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE policies ADD INDEX ix_policies_created_by_user_id (created_by_user_id)',
    'SELECT 1')
  FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'policies' AND index_name = 'ix_policies_created_by_user_id'
);
PREPARE add_policy_owner_index_stmt FROM @add_policy_owner_index_sql;
EXECUTE add_policy_owner_index_stmt;
DEALLOCATE PREPARE add_policy_owner_index_stmt;

SET @add_policy_owner_fk_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE policies ADD CONSTRAINT fk_policies_created_by_user FOREIGN KEY (created_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL',
    'SELECT 1')
  FROM information_schema.table_constraints
  WHERE constraint_schema = DATABASE() AND table_name = 'policies' AND constraint_name = 'fk_policies_created_by_user'
);
PREPARE add_policy_owner_fk_stmt FROM @add_policy_owner_fk_sql;
EXECUTE add_policy_owner_fk_stmt;
DEALLOCATE PREPARE add_policy_owner_fk_stmt;

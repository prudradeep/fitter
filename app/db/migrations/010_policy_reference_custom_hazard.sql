SET @add_policy_reference_custom_hazard_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD COLUMN custom_hazard_id CHAR(36) NULL AFTER session_key',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND column_name = 'custom_hazard_id'
);
PREPARE add_policy_reference_custom_hazard_stmt FROM @add_policy_reference_custom_hazard_sql;
EXECUTE add_policy_reference_custom_hazard_stmt;
DEALLOCATE PREPARE add_policy_reference_custom_hazard_stmt;

SET @add_policy_reference_custom_hazard_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD INDEX ix_knowledge_documents_custom_hazard_id (custom_hazard_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND index_name = 'ix_knowledge_documents_custom_hazard_id'
);
PREPARE add_policy_reference_custom_hazard_index_stmt FROM @add_policy_reference_custom_hazard_index_sql;
EXECUTE add_policy_reference_custom_hazard_index_stmt;
DEALLOCATE PREPARE add_policy_reference_custom_hazard_index_stmt;

SET @add_policy_reference_custom_hazard_fk_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_custom_hazard FOREIGN KEY (custom_hazard_id) REFERENCES custom_hazards(id) ON DELETE SET NULL',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND constraint_name = 'fk_knowledge_documents_custom_hazard'
);
PREPARE add_policy_reference_custom_hazard_fk_stmt FROM @add_policy_reference_custom_hazard_fk_sql;
EXECUTE add_policy_reference_custom_hazard_fk_stmt;
DEALLOCATE PREPARE add_policy_reference_custom_hazard_fk_stmt;

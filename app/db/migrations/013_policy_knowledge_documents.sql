SET @add_policy_knowledge_document_column_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD COLUMN mitigation_measure_policy_id CHAR(36) NULL AFTER custom_hazard_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND column_name = 'mitigation_measure_policy_id'
);
PREPARE add_policy_knowledge_document_column_stmt FROM @add_policy_knowledge_document_column_sql;
EXECUTE add_policy_knowledge_document_column_stmt;
DEALLOCATE PREPARE add_policy_knowledge_document_column_stmt;

SET @add_policy_knowledge_document_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD INDEX ix_knowledge_documents_mitigation_measure_policy_id (mitigation_measure_policy_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND index_name = 'ix_knowledge_documents_mitigation_measure_policy_id'
);
PREPARE add_policy_knowledge_document_index_stmt FROM @add_policy_knowledge_document_index_sql;
EXECUTE add_policy_knowledge_document_index_stmt;
DEALLOCATE PREPARE add_policy_knowledge_document_index_stmt;

SET @add_policy_knowledge_document_fk_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_mitigation_measure_policy FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE SET NULL',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND constraint_name = 'fk_knowledge_documents_mitigation_measure_policy'
);
PREPARE add_policy_knowledge_document_fk_stmt FROM @add_policy_knowledge_document_fk_sql;
EXECUTE add_policy_knowledge_document_fk_stmt;
DEALLOCATE PREPARE add_policy_knowledge_document_fk_stmt;

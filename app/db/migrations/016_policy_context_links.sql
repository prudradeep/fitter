SET @add_custom_hazard_policy_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD COLUMN policy_id CHAR(36) NULL AFTER mitigation_measure_policy_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND column_name = 'policy_id'
);
PREPARE add_custom_hazard_policy_id_stmt FROM @add_custom_hazard_policy_id_sql;
EXECUTE add_custom_hazard_policy_id_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_id_stmt;

SET @add_custom_hazard_policy_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD INDEX ix_custom_hazards_policy_id (policy_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND index_name = 'ix_custom_hazards_policy_id'
);
PREPARE add_custom_hazard_policy_index_stmt FROM @add_custom_hazard_policy_index_sql;
EXECUTE add_custom_hazard_policy_index_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_index_stmt;

SET @add_custom_hazard_policy_fk_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE custom_hazards ADD CONSTRAINT fk_custom_hazards_policy_id FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE SET NULL',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = DATABASE()
    AND table_name = 'custom_hazards'
    AND constraint_name = 'fk_custom_hazards_policy_id'
);
PREPARE add_custom_hazard_policy_fk_stmt FROM @add_custom_hazard_policy_fk_sql;
EXECUTE add_custom_hazard_policy_fk_stmt;
DEALLOCATE PREPARE add_custom_hazard_policy_fk_stmt;

SET @add_knowledge_document_policy_id_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD COLUMN policy_id CHAR(36) NULL AFTER mitigation_measure_policy_id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND column_name = 'policy_id'
);
PREPARE add_knowledge_document_policy_id_stmt FROM @add_knowledge_document_policy_id_sql;
EXECUTE add_knowledge_document_policy_id_stmt;
DEALLOCATE PREPARE add_knowledge_document_policy_id_stmt;

SET @add_knowledge_document_policy_index_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD INDEX ix_knowledge_documents_policy_id (policy_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND index_name = 'ix_knowledge_documents_policy_id'
);
PREPARE add_knowledge_document_policy_index_stmt FROM @add_knowledge_document_policy_index_sql;
EXECUTE add_knowledge_document_policy_index_stmt;
DEALLOCATE PREPARE add_knowledge_document_policy_index_stmt;

SET @add_knowledge_document_policy_fk_sql = (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_policy_id FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE SET NULL',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND constraint_name = 'fk_knowledge_documents_policy_id'
);
PREPARE add_knowledge_document_policy_fk_stmt FROM @add_knowledge_document_policy_fk_sql;
EXECUTE add_knowledge_document_policy_fk_stmt;
DEALLOCATE PREPARE add_knowledge_document_policy_fk_stmt;

UPDATE knowledge_documents documents
JOIN (
  SELECT legacy.id AS legacy_id, MIN(policies.id) AS policy_id
  FROM mitigation_measure_policies legacy
  JOIN policies
    ON policies.country_id = legacy.country_id
   AND policies.sector_id = legacy.sector_id
   AND LOWER(TRIM(policies.policy)) = LOWER(TRIM(legacy.policy_title))
  GROUP BY legacy.id
  HAVING COUNT(*) = 1
) matched ON matched.legacy_id = documents.mitigation_measure_policy_id
SET documents.policy_id = matched.policy_id
WHERE documents.policy_id IS NULL;

UPDATE custom_hazards hazards
JOIN (
  SELECT legacy.id AS legacy_id, MIN(policies.id) AS policy_id
  FROM mitigation_measure_policies legacy
  JOIN policies
    ON policies.country_id = legacy.country_id
   AND policies.sector_id = legacy.sector_id
   AND LOWER(TRIM(policies.policy)) = LOWER(TRIM(legacy.policy_title))
  GROUP BY legacy.id
  HAVING COUNT(*) = 1
) matched ON matched.legacy_id = hazards.mitigation_measure_policy_id
SET hazards.policy_id = matched.policy_id
WHERE hazards.policy_id IS NULL;

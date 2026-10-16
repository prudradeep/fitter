CREATE TEMPORARY TABLE policy_title_duplicates AS
SELECT id AS duplicate_id, canonical_id
FROM (
  SELECT id,
         FIRST_VALUE(id) OVER (
           PARTITION BY country_id, sector_id,
             LOWER(TRIM(SUBSTRING_INDEX(policy, CHAR(10), 1)))
           ORDER BY CHAR_LENGTH(policy) DESC, created_at, id
         ) AS canonical_id,
         ROW_NUMBER() OVER (
           PARTITION BY country_id, sector_id,
             LOWER(TRIM(SUBSTRING_INDEX(policy, CHAR(10), 1)))
           ORDER BY CHAR_LENGTH(policy) DESC, created_at, id
         ) AS row_num
  FROM policies
) ranked
WHERE row_num > 1;

DELETE duplicate_links
FROM policy_hazard_links duplicate_links
JOIN policy_title_duplicates duplicates
  ON duplicates.duplicate_id = duplicate_links.policy_id
JOIN policy_hazard_links canonical_links
  ON canonical_links.policy_id = duplicates.canonical_id
 AND canonical_links.system_hazard_id <=> duplicate_links.system_hazard_id
 AND canonical_links.additional_hazard_id <=> duplicate_links.additional_hazard_id;

UPDATE policy_hazard_links links
JOIN policy_title_duplicates duplicates ON duplicates.duplicate_id = links.policy_id
SET links.policy_id = duplicates.canonical_id;

UPDATE knowledge_documents documents
JOIN policy_title_duplicates duplicates ON duplicates.duplicate_id = documents.policy_id
SET documents.policy_id = duplicates.canonical_id;

UPDATE custom_hazards hazards
JOIN policy_title_duplicates duplicates ON duplicates.duplicate_id = hazards.policy_id
SET hazards.policy_id = duplicates.canonical_id;

DELETE policies
FROM policies
JOIN policy_title_duplicates duplicates ON duplicates.duplicate_id = policies.id;

DROP TEMPORARY TABLE policy_title_duplicates;

ALTER TABLE policies
  ADD COLUMN policy_title_hash BINARY(32)
    GENERATED ALWAYS AS (
      UNHEX(SHA2(LOWER(TRIM(SUBSTRING_INDEX(policy, CHAR(10), 1))), 256))
    ) STORED,
  ADD UNIQUE INDEX uq_policies_country_sector_title
    (country_id, sector_id, policy_title_hash);

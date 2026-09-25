ALTER TABLE custom_hazards
  ADD COLUMN mitigation_measure_policy_id CHAR(36) NULL AFTER sector_id,
  ADD CONSTRAINT fk_custom_hazards_policy
    FOREIGN KEY (mitigation_measure_policy_id)
    REFERENCES mitigation_measure_policies(id) ON DELETE SET NULL,
  ADD INDEX ix_custom_hazards_mitigation_measure_policy_id (mitigation_measure_policy_id);

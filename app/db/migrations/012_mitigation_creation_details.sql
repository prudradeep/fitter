ALTER TABLE user_mitigation_measures
  ADD COLUMN creation_details_json TEXT NULL AFTER target_groups_json;

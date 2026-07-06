CREATE DATABASE IF NOT EXISTS drtransition
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE drtransition;

CREATE TABLE IF NOT EXISTS countries (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  map_code VARCHAR(8) NULL,
  map_path VARCHAR(255) NULL,
  INDEX ix_countries_map_code (map_code),
  INDEX ix_countries_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS regions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  country_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  CONSTRAINT fk_regions_country
    FOREIGN KEY (country_id) REFERENCES countries(id)
    ON DELETE CASCADE,
  CONSTRAINT uq_country_region UNIQUE (country_id, name),
  INDEX ix_regions_country_id (country_id),
  INDEX ix_regions_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sectors (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  INDEX ix_sectors_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS country_sectors (
  id INT AUTO_INCREMENT PRIMARY KEY,
  country_id INT NOT NULL,
  sector_id INT NOT NULL,
  CONSTRAINT fk_country_sectors_country
    FOREIGN KEY (country_id) REFERENCES countries(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_country_sectors_sector
    FOREIGN KEY (sector_id) REFERENCES sectors(id)
    ON DELETE CASCADE,
  CONSTRAINT uq_country_sector UNIQUE (country_id, sector_id),
  INDEX ix_country_sectors_country_id (country_id),
  INDEX ix_country_sectors_sector_id (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evaluation_questions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  category VARCHAR(120) NOT NULL,
  chart_title VARCHAR(160) NULL,
  question TEXT NOT NULL,
  sort_order INT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT uq_eval_category_sort UNIQUE (category, sort_order),
  INDEX ix_evaluation_questions_category (category),
  INDEX ix_evaluation_questions_sort_order (sort_order),
  INDEX ix_evaluation_questions_active (active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS question_options (
  id INT AUTO_INCREMENT PRIMARY KEY,
  questionId INT NOT NULL,
  `option` VARCHAR(255) NOT NULL,
  CONSTRAINT fk_question_options_question
    FOREIGN KEY (questionId) REFERENCES evaluation_questions(id)
    ON DELETE CASCADE,
  CONSTRAINT uq_question_option UNIQUE (questionId, `option`),
  INDEX ix_question_options_question_id (questionId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  name VARCHAR(160) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  designation VARCHAR(160) NOT NULL,
  organisation_type VARCHAR(160) NOT NULL,
  organisation_name VARCHAR(220) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX ix_app_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  session_key VARCHAR(64) NOT NULL UNIQUE,
  title VARCHAR(220) NULL,
  title_is_manual BOOLEAN NOT NULL DEFAULT FALSE,
  session_data TEXT NULL,
  user_id INT NULL,
  country_id INT NULL,
  region_id INT NULL,
  sector_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_sessions_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_sessions_region FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_sessions_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  INDEX ix_user_sessions_session_key (session_key),
  INDEX ix_user_sessions_title (title),
  INDEX ix_user_sessions_user_id (user_id),
  INDEX ix_user_sessions_country_id (country_id),
  INDEX ix_user_sessions_region_id (region_id),
  INDEX ix_user_sessions_sector_id (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_chat_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_session_id INT NOT NULL,
  role VARCHAR(20) NOT NULL,
  content TEXT NOT NULL,
  is_error BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_chat_messages_session
    FOREIGN KEY (user_session_id) REFERENCES user_sessions(id)
    ON DELETE CASCADE,
  INDEX ix_user_chat_messages_session_id (user_session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS system_hazards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sector_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_system_hazards_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT uq_system_hazard_sector_name UNIQUE (sector_id, name),
  INDEX ix_system_hazards_sector_id (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS additional_hazards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  country_id INT NOT NULL,
  sector_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'csv',
  csv_row_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_additional_hazards_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
  CONSTRAINT fk_additional_hazards_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT uq_additional_hazard_scope_name UNIQUE (country_id, sector_id, name),
  INDEX ix_additional_hazards_country_id (country_id),
  INDEX ix_additional_hazards_sector_id (sector_id),
  INDEX ix_additional_hazards_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS additional_hazard_profiles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  additional_hazard_id INT NOT NULL,
  profile VARCHAR(255) NOT NULL,
  evidence TEXT NULL,
  reference TEXT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'd4_2_pdf',
  csv_row_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_additional_hazard_profiles_hazard
    FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_additional_hazard_profile UNIQUE (additional_hazard_id, profile),
  INDEX ix_additional_hazard_profiles_hazard_id (additional_hazard_id),
  INDEX ix_additional_hazard_profiles_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS additional_hazard_profile_target_populations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  additional_hazard_profile_id INT NOT NULL,
  question_option_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_additional_hazard_profile_target_profile
    FOREIGN KEY (additional_hazard_profile_id)
    REFERENCES additional_hazard_profiles(id) ON DELETE CASCADE,
  CONSTRAINT fk_additional_hazard_profile_target_option
    FOREIGN KEY (question_option_id)
    REFERENCES question_options(id) ON DELETE CASCADE,
  CONSTRAINT uq_additional_hazard_profile_target_option
    UNIQUE (additional_hazard_profile_id, question_option_id),
  INDEX ix_additional_hazard_profile_target_profile (additional_hazard_profile_id),
  INDEX ix_additional_hazard_profile_target_option (question_option_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_hazards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_session_id INT NOT NULL,
  system_hazard_id INT NULL,
  sector_id INT NULL,
  region_id INT NULL,
  name VARCHAR(255) NOT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'custom',
  reason TEXT NULL,
  evidence TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_hazards_session FOREIGN KEY (user_session_id) REFERENCES user_sessions(id) ON DELETE CASCADE,
  CONSTRAINT fk_user_hazards_system_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_hazards_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_hazards_region FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
  CONSTRAINT uq_user_session_hazard UNIQUE (user_session_id, name),
  INDEX ix_user_hazards_session_id (user_session_id),
  INDEX ix_user_hazards_system_hazard_id (system_hazard_id),
  INDEX ix_user_hazards_sector_id (sector_id),
  INDEX ix_user_hazards_region_id (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_hazard_socio_demographics (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_hazard_id INT NOT NULL,
  country_id INT NULL,
  region_id INT NULL,
  sector_id INT NULL,
  variable_name VARCHAR(160) NULL,
  profile TEXT NOT NULL,
  explanation TEXT NULL,
  statistical_basis TEXT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'llm',
  metadata_json TEXT NULL,
  reason TEXT NULL,
  evidence TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_hazard_dgs_hazard FOREIGN KEY (user_hazard_id) REFERENCES user_hazards(id) ON DELETE CASCADE,
  CONSTRAINT fk_user_hazard_dgs_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_hazard_dgs_region FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_hazard_dgs_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  INDEX ix_user_hazard_socio_demographics_hazard_id (user_hazard_id),
  INDEX ix_user_hazard_socio_demographics_country_id (country_id),
  INDEX ix_user_hazard_socio_demographics_region_id (region_id),
  INDEX ix_user_hazard_socio_demographics_sector_id (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS system_hazard_socio_demographics (
  id INT AUTO_INCREMENT PRIMARY KEY,
  system_hazard_id INT NOT NULL,
  sector_id INT NULL,
  variable_name VARCHAR(160) NULL,
  variable_type VARCHAR(40) NOT NULL DEFAULT 'individual',
  profile TEXT NOT NULL,
  explanation TEXT NULL,
  statistical_basis TEXT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'sector_prompt',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_system_hazard_dgs_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
  CONSTRAINT fk_system_hazard_dgs_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  INDEX ix_system_hazard_socio_demographics_hazard_id (system_hazard_id),
  INDEX ix_system_hazard_socio_demographics_sector_id (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS system_hazard_socio_demographic_target_populations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  system_hazard_socio_demographic_id INT NOT NULL,
  question_option_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_system_dg_target_population_system_dg
    FOREIGN KEY (system_hazard_socio_demographic_id)
    REFERENCES system_hazard_socio_demographics(id) ON DELETE CASCADE,
  CONSTRAINT fk_system_dg_target_population_option
    FOREIGN KEY (question_option_id)
    REFERENCES question_options(id) ON DELETE CASCADE,
  CONSTRAINT uq_system_dg_target_population_option
    UNIQUE (system_hazard_socio_demographic_id, question_option_id),
  INDEX ix_system_dg_target_population_system_dg (system_hazard_socio_demographic_id),
  INDEX ix_system_dg_target_population_option (question_option_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_mitigation_measures (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_hazard_id INT NOT NULL,
  measure TEXT NOT NULL,
  reason TEXT NOT NULL,
  target_population TEXT NULL,
  conclusion TEXT NULL,
  target_groups_json TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_mitigations_hazard FOREIGN KEY (user_hazard_id) REFERENCES user_hazards(id) ON DELETE CASCADE,
  INDEX ix_user_mitigation_measures_hazard_id (user_hazard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mitigation_measure_examples (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sector_id INT NOT NULL,
  system_hazard_id INT NULL,
  system_hazard_socio_demographic_id INT NULL,
  profile_label VARCHAR(255) NULL,
  measure TEXT NOT NULL,
  policy_case_study TEXT NULL,
  country_city VARCHAR(255) NULL,
  implementation_summary TEXT NULL,
  evidence TEXT NULL,
  reference_links TEXT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'seed',
  csv_row_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mitigation_examples_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT fk_mitigation_examples_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE SET NULL,
  CONSTRAINT fk_mitigation_examples_profile FOREIGN KEY (system_hazard_socio_demographic_id) REFERENCES system_hazard_socio_demographics(id) ON DELETE SET NULL,
  INDEX ix_mitigation_measure_examples_sector_id (sector_id),
  INDEX ix_mitigation_measure_examples_hazard_id (system_hazard_id),
  INDEX ix_mitigation_measure_examples_profile_id (system_hazard_socio_demographic_id),
  INDEX ix_mitigation_measure_examples_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mitigation_measure_policies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  policy_code VARCHAR(80) NOT NULL,
  policy_title TEXT NOT NULL,
  country_id INT NULL,
  sector_id INT NULL,
  policy_type VARCHAR(120) NULL,
  short_description TEXT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
  excel_row_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mitigation_policies_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
  CONSTRAINT fk_mitigation_policies_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  CONSTRAINT uq_mitigation_policy_code_sector_source UNIQUE (policy_code, sector_id, source),
  INDEX ix_mitigation_policies_policy_code (policy_code),
  INDEX ix_mitigation_policies_country_id (country_id),
  INDEX ix_mitigation_policies_sector_id (sector_id),
  INDEX ix_mitigation_policies_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mitigation_measure_target_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mitigation_measure_policy_id INT NOT NULL,
  question_option_id INT NOT NULL,
  match_value VARCHAR(40) NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
  excel_column_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mitigation_target_groups_policy FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_mitigation_target_groups_option FOREIGN KEY (question_option_id) REFERENCES question_options(id) ON DELETE CASCADE,
  CONSTRAINT uq_mitigation_target_group_xlsx_cell UNIQUE (mitigation_measure_policy_id, question_option_id),
  INDEX ix_mitigation_target_groups_policy_id (mitigation_measure_policy_id),
  INDEX ix_mitigation_target_groups_option_id (question_option_id),
  INDEX ix_mitigation_target_groups_match_value (match_value),
  INDEX ix_mitigation_target_groups_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mitigation_measure_policy_additional_hazards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mitigation_measure_policy_id INT NOT NULL,
  additional_hazard_id INT NOT NULL,
  match_value VARCHAR(40) NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
  excel_row_number INT NULL,
  excel_column_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mitigation_policy_hazards_policy FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_mitigation_policy_hazards_additional_hazard FOREIGN KEY (additional_hazard_id) REFERENCES additional_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_mitigation_policy_additional_hazard UNIQUE (mitigation_measure_policy_id, additional_hazard_id),
  INDEX ix_mitigation_policy_hazards_policy_id (mitigation_measure_policy_id),
  INDEX ix_mitigation_policy_hazards_additional_hazard_id (additional_hazard_id),
  INDEX ix_mitigation_policy_hazards_match_value (match_value),
  INDEX ix_mitigation_policy_hazards_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mitigation_measure_policy_system_hazards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mitigation_measure_policy_id INT NOT NULL,
  system_hazard_id INT NOT NULL,
  mitigation_effect VARCHAR(40) NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'xlsx',
  excel_row_number INT NULL,
  excel_column_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_mitigation_policy_system_hazards_policy FOREIGN KEY (mitigation_measure_policy_id) REFERENCES mitigation_measure_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_mitigation_policy_system_hazards_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_mitigation_policy_system_hazard UNIQUE (mitigation_measure_policy_id, system_hazard_id),
  INDEX ix_mitigation_policy_system_hazards_policy_id (mitigation_measure_policy_id),
  INDEX ix_mitigation_policy_system_hazards_hazard_id (system_hazard_id),
  INDEX ix_mitigation_policy_system_hazards_effect (mitigation_effect),
  INDEX ix_mitigation_policy_system_hazards_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_question_responses (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_session_id INT NOT NULL,
  user_hazard_id INT NULL,
  mitigation_measure_id INT NULL,
  question_id INT NULL,
  question_option_id INT NULL,
  category VARCHAR(120) NULL,
  response_text TEXT NULL,
  score INT NULL,
  reason TEXT NULL,
  evidence TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_question_responses_session FOREIGN KEY (user_session_id) REFERENCES user_sessions(id) ON DELETE CASCADE,
  CONSTRAINT fk_user_question_responses_hazard FOREIGN KEY (user_hazard_id) REFERENCES user_hazards(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_question_responses_mitigation FOREIGN KEY (mitigation_measure_id) REFERENCES user_mitigation_measures(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_question_responses_question FOREIGN KEY (question_id) REFERENCES evaluation_questions(id) ON DELETE SET NULL,
  CONSTRAINT fk_user_question_responses_option FOREIGN KEY (question_option_id) REFERENCES question_options(id) ON DELETE SET NULL,
  INDEX ix_user_question_responses_session_id (user_session_id),
  INDEX ix_user_question_responses_hazard_id (user_hazard_id),
  INDEX ix_user_question_responses_mitigation_id (mitigation_measure_id),
  INDEX ix_user_question_responses_question_id (question_id),
  INDEX ix_user_question_responses_option_id (question_option_id),
  INDEX ix_user_question_responses_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_documents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  title VARCHAR(255) NOT NULL,
  source_type VARCHAR(40) NOT NULL,
  source_uri TEXT NULL,
  scope VARCHAR(20) NOT NULL DEFAULT 'main',
  session_key VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_knowledge_documents_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  INDEX ix_knowledge_documents_user_id (user_id),
  INDEX ix_knowledge_documents_scope (scope),
  INDEX ix_knowledge_documents_session_key (session_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  document_id INT NOT NULL,
  user_id INT NULL,
  chunk_index INT NOT NULL,
  content TEXT NOT NULL,
  source_type VARCHAR(40) NOT NULL,
  source_uri TEXT NULL,
  page_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_knowledge_chunks_document FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  CONSTRAINT fk_knowledge_chunks_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  INDEX ix_knowledge_chunks_document_id (document_id),
  INDEX ix_knowledge_chunks_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS eurostat_population_cache (
  id INT AUTO_INCREMENT PRIMARY KEY,
  country VARCHAR(120) NOT NULL,
  region VARCHAR(120) NOT NULL,
  country_id INT NULL,
  region_id INT NULL,
  sector_id INT NULL,
  system_hazard_id INT NULL,
  profile VARCHAR(255) NOT NULL,
  response_json TEXT NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_eurostat_population_country FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
  CONSTRAINT fk_eurostat_population_region FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE,
  CONSTRAINT fk_eurostat_population_sector FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT fk_eurostat_population_hazard FOREIGN KEY (system_hazard_id) REFERENCES system_hazards(id) ON DELETE CASCADE,
  CONSTRAINT uq_eurostat_population_lookup UNIQUE (country_id, region_id, sector_id, system_hazard_id, profile),
  INDEX ix_eurostat_population_cache_country (country),
  INDEX ix_eurostat_population_cache_region (region),
  INDEX ix_eurostat_population_cache_country_id (country_id),
  INDEX ix_eurostat_population_cache_region_id (region_id),
  INDEX ix_eurostat_population_cache_sector_id (sector_id),
  INDEX ix_eurostat_population_cache_hazard_id (system_hazard_id),
  INDEX ix_eurostat_population_cache_profile (profile),
  INDEX ix_eurostat_population_cache_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS system_hazard_socio_demographic_population_matches (
  id INT AUTO_INCREMENT PRIMARY KEY,
  system_hazard_socio_demographic_id INT NOT NULL,
  eurostat_population_cache_id INT NULL,
  match_status INT NOT NULL DEFAULT 1,
  attempt_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_system_dg_population_match_system_dg
    FOREIGN KEY (system_hazard_socio_demographic_id)
    REFERENCES system_hazard_socio_demographics(id) ON DELETE CASCADE,
  CONSTRAINT fk_system_dg_population_match_eurostat_cache
    FOREIGN KEY (eurostat_population_cache_id)
    REFERENCES eurostat_population_cache(id) ON DELETE CASCADE,
  CONSTRAINT uq_system_dg_eurostat_cache_match
    UNIQUE (system_hazard_socio_demographic_id, eurostat_population_cache_id),
  INDEX ix_system_dg_population_match_system_dg (system_hazard_socio_demographic_id),
  INDEX ix_system_dg_population_match_eurostat_cache (eurostat_population_cache_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hazard_listing_cache (
  id INT AUTO_INCREMENT PRIMARY KEY,
  country_id INT NOT NULL,
  region_id INT NULL,
  region_scope_key INT NOT NULL DEFAULT 0,
  sector_id INT NOT NULL,
  cache_version VARCHAR(40) NOT NULL DEFAULT 'v1',
  source_fingerprint VARCHAR(64) NOT NULL,
  payload_json LONGTEXT NOT NULL,
  expires_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_hazard_listing_cache_country
    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE CASCADE,
  CONSTRAINT fk_hazard_listing_cache_region
    FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE,
  CONSTRAINT fk_hazard_listing_cache_sector
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
  CONSTRAINT uq_hazard_listing_context_version
    UNIQUE (country_id, region_scope_key, sector_id, cache_version),
  INDEX ix_hazard_listing_cache_country_id (country_id),
  INDEX ix_hazard_listing_cache_region_id (region_id),
  INDEX ix_hazard_listing_cache_region_scope_key (region_scope_key),
  INDEX ix_hazard_listing_cache_sector_id (sector_id),
  INDEX ix_hazard_listing_cache_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_activities (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_session_id INT NOT NULL,
  activity_type VARCHAR(80) NOT NULL,
  step VARCHAR(120) NULL,
  details TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_activities_session FOREIGN KEY (user_session_id) REFERENCES user_sessions(id) ON DELETE CASCADE,
  INDEX ix_user_activities_session_id (user_session_id),
  INDEX ix_user_activities_activity_type (activity_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO countries (name, map_code, map_path) VALUES
  ('Germany', 'DE', 'countries/de/de-all.geo.json'),
  ('Hungary', 'HU', 'countries/hu/hu-all.geo.json'),
  ('Ireland', 'IE', 'countries/ie/ie-all.geo.json'),
  ('Italy', 'IT', 'countries/it/it-all.geo.json'),
  ('Portugal', 'PT', 'countries/pt/pt-all.geo.json'),
  ('Spain', 'ES', 'countries/es/es-all.geo.json')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO sectors (name) VALUES
  ('Energy'),
  ('Housing'),
  ('Transport')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO regions (country_id, name)
SELECT c.id, region_rows.region_name
FROM countries c
JOIN (
  SELECT 'Germany' AS country_name, 'Baden-Württemberg' AS region_name
  UNION ALL SELECT 'Germany', 'Bavaria'
  UNION ALL SELECT 'Germany', 'Berlin'
  UNION ALL SELECT 'Germany', 'Brandenburg'
  UNION ALL SELECT 'Germany', 'Bremen'
  UNION ALL SELECT 'Germany', 'Hamburg'
  UNION ALL SELECT 'Germany', 'Hesse'
  UNION ALL SELECT 'Germany', 'Lower Saxony'
  UNION ALL SELECT 'Germany', 'Mecklenburg-Vorpommern'
  UNION ALL SELECT 'Germany', 'North Rhine-Westphalia'
  UNION ALL SELECT 'Germany', 'Rhineland-Palatinate'
  UNION ALL SELECT 'Germany', 'Saarland'
  UNION ALL SELECT 'Germany', 'Saxony'
  UNION ALL SELECT 'Germany', 'Saxony-Anhalt'
  UNION ALL SELECT 'Germany', 'Schleswig-Holstein'
  UNION ALL SELECT 'Germany', 'Thuringia'
  UNION ALL SELECT 'Hungary', 'Baranya'
  UNION ALL SELECT 'Hungary', 'Borsod-Abaúj-Zemplén'
  UNION ALL SELECT 'Hungary', 'Budapest'
  UNION ALL SELECT 'Hungary', 'Csongrád-Csanád'
  UNION ALL SELECT 'Hungary', 'Fejér'
  UNION ALL SELECT 'Hungary', 'Győr-Moson-Sopron'
  UNION ALL SELECT 'Hungary', 'Hajdú-Bihar'
  UNION ALL SELECT 'Hungary', 'Heves'
  UNION ALL SELECT 'Hungary', 'Jász-Nagykun-Szolnok'
  UNION ALL SELECT 'Hungary', 'Komárom-Esztergom'
  UNION ALL SELECT 'Hungary', 'Nógrád'
  UNION ALL SELECT 'Hungary', 'Pest'
  UNION ALL SELECT 'Hungary', 'Somogy'
  UNION ALL SELECT 'Hungary', 'Szabolcs-Szatmár-Bereg'
  UNION ALL SELECT 'Hungary', 'Tolna'
  UNION ALL SELECT 'Hungary', 'Vas'
  UNION ALL SELECT 'Hungary', 'Veszprém'
  UNION ALL SELECT 'Hungary', 'Zala'
  UNION ALL SELECT 'Ireland', 'Clare'
  UNION ALL SELECT 'Ireland', 'Connacht'
  UNION ALL SELECT 'Ireland', 'Cork'
  UNION ALL SELECT 'Ireland', 'Dublin'
  UNION ALL SELECT 'Ireland', 'Galway'
  UNION ALL SELECT 'Ireland', 'Kerry'
  UNION ALL SELECT 'Ireland', 'Kilkenny'
  UNION ALL SELECT 'Ireland', 'Leinster'
  UNION ALL SELECT 'Ireland', 'Limerick'
  UNION ALL SELECT 'Ireland', 'Mayo'
  UNION ALL SELECT 'Ireland', 'Munster'
  UNION ALL SELECT 'Ireland', 'Sligo'
  UNION ALL SELECT 'Ireland', 'Tipperary'
  UNION ALL SELECT 'Ireland', 'Ulster (ROI)'
  UNION ALL SELECT 'Ireland', 'Waterford'
  UNION ALL SELECT 'Ireland', 'Wicklow'
  UNION ALL SELECT 'Italy', 'Abruzzo'
  UNION ALL SELECT 'Italy', 'Aosta Valley'
  UNION ALL SELECT 'Italy', 'Basilicata'
  UNION ALL SELECT 'Italy', 'Calabria'
  UNION ALL SELECT 'Italy', 'Campania'
  UNION ALL SELECT 'Italy', 'Emilia-Romagna'
  UNION ALL SELECT 'Italy', 'Friuli-Venezia Giulia'
  UNION ALL SELECT 'Italy', 'Lazio'
  UNION ALL SELECT 'Italy', 'Liguria'
  UNION ALL SELECT 'Italy', 'Lombardy'
  UNION ALL SELECT 'Italy', 'Marche'
  UNION ALL SELECT 'Italy', 'Molise'
  UNION ALL SELECT 'Italy', 'Piedmont'
  UNION ALL SELECT 'Italy', 'Puglia'
  UNION ALL SELECT 'Italy', 'Sardinia'
  UNION ALL SELECT 'Italy', 'Sicily'
  UNION ALL SELECT 'Italy', 'Trentino-Alto Adige'
  UNION ALL SELECT 'Italy', 'Tuscany'
  UNION ALL SELECT 'Italy', 'Umbria'
  UNION ALL SELECT 'Italy', 'Veneto'
  UNION ALL SELECT 'Spain', 'Andalusia'
  UNION ALL SELECT 'Spain', 'Aragon'
  UNION ALL SELECT 'Spain', 'Asturias'
  UNION ALL SELECT 'Spain', 'Balearic Islands'
  UNION ALL SELECT 'Spain', 'Basque Country'
  UNION ALL SELECT 'Spain', 'Canary Islands'
  UNION ALL SELECT 'Spain', 'Cantabria'
  UNION ALL SELECT 'Spain', 'Castile and León'
  UNION ALL SELECT 'Spain', 'Castile-La Mancha'
  UNION ALL SELECT 'Spain', 'Catalonia'
  UNION ALL SELECT 'Spain', 'Extremadura'
  UNION ALL SELECT 'Spain', 'Galicia'
  UNION ALL SELECT 'Spain', 'La Rioja'
  UNION ALL SELECT 'Spain', 'Madrid'
  UNION ALL SELECT 'Spain', 'Murcia'
  UNION ALL SELECT 'Spain', 'Navarre'
  UNION ALL SELECT 'Spain', 'Valencia'
  UNION ALL SELECT 'Portugal', 'Alentejo'
  UNION ALL SELECT 'Portugal', 'Algarve'
  UNION ALL SELECT 'Portugal', 'Azores'
  UNION ALL SELECT 'Portugal', 'Centro'
  UNION ALL SELECT 'Portugal', 'Lisbon Metropolitan Area'
  UNION ALL SELECT 'Portugal', 'Madeira'
  UNION ALL SELECT 'Portugal', 'Norte'
) region_rows ON region_rows.country_name = c.name
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO country_sectors (country_id, sector_id)
SELECT c.id, s.id
FROM countries c
JOIN (
  SELECT 'Germany' AS country_name, 'Energy' AS sector_name
  UNION ALL SELECT 'Germany', 'Housing'
  UNION ALL SELECT 'Hungary', 'Energy'
  UNION ALL SELECT 'Hungary', 'Housing'
  UNION ALL SELECT 'Ireland', 'Energy'
  UNION ALL SELECT 'Ireland', 'Housing'
  UNION ALL SELECT 'Italy', 'Energy'
  UNION ALL SELECT 'Italy', 'Transport'
  UNION ALL SELECT 'Spain', 'Energy'
  UNION ALL SELECT 'Spain', 'Transport'
  UNION ALL SELECT 'Portugal', 'Energy'
) country_sector_rows ON country_sector_rows.country_name = c.name
JOIN sectors s ON s.name = country_sector_rows.sector_name
ON DUPLICATE KEY UPDATE country_id = VALUES(country_id);

INSERT IGNORE INTO evaluation_questions (category, sort_order, question, active) VALUES
  ('target_population', 1, 'Age range', TRUE),
  ('target_population', 2, 'Living in a house with low energy efficiency', TRUE),
  ('target_population', 3, 'Gender', TRUE),
  ('target_population', 4, 'Need of a car to perform daily activities', TRUE),
  ('target_population', 5, 'Level of education', TRUE),
  ('target_population', 6, 'Location of residency', TRUE),
  ('target_population', 7, 'Economic status', TRUE),
  ('target_population', 8, 'Care responsibility as the main activity', TRUE),
  ('target_population', 9, 'EU citizenship', TRUE),
  ('target_population', 10, 'Disability of long-term condition', TRUE),
  ('target_population', 11, 'Level of income', TRUE),
  ('target_population', 12, 'Tenancy status', TRUE),
  ('The transformative impact',1,'## 1. Direct Effect on Identified Negative Impacts (Weight: ~40%)\n\nTo what extent does the measure directly address the previously identified negative impacts?\n\n- **1** = No clear relevance to the identified problems\n- **5** = Partially relevant; addresses some aspects\n- **10** = Strong, direct alignment with the defined problems',TRUE),
  ('The transformative impact',2,'## 2. Systemic & Structural Impact (Weight: ~35%)\n\nTo what extent does the initiative generate broader systemic change (across sectors, institutions, or policies)?\n\n- **1** = Isolated impact; no broader systemic or institutional change\n- **5** = Moderate spillovers or incremental institutional adjustments\n- **10** = Strong cross-sector impact and/or significant changes to governance, regulation, or institutional behavior',TRUE),
  ('The transformative impact',3,'## 3. Societal Transformation & Equity (Weight: ~25%)\n\nTo what extent does the initiative influence societal attitudes and reduce inequalities?\n\n- **1** = No influence on attitudes; may worsen inequalities\n- **5** = Some influence on discourse or limited/mixed equity effects\n- **10** = Strong shift in narratives/priorities and clear reduction of inequalities (e.g., accessibility, fairness)',TRUE),
  ('Feasibility and Implementation',1,'## 4.1 Barriers in terms of Accessibility\n\nAre there technical, administrative, geographic, digital, or social barriers that may prevent certain groups from accessing the measure on equal terms?\n\n- **1** = Severe technical, administrative, geographic, digital, or social barriers prevent equitable access\n- **5** = Moderate accessibility barriers that require adaptation, support, or targeted interventions\n- **10** = Very few accessibility barriers; the measure is broadly accessible to all affected groups',TRUE),
  ('Feasibility and Implementation',2,'## 4.2 Barriers in terms of Affordability\n\nIs the measure economically viable for all affected groups, including low-income households or other disadvantaged populations? Are costs, co-payments, or hidden burdens equitably distributed?\n\n- **1** = Severe affordability barriers; costs or financial burdens exclude significant groups\n- **5** = Moderate affordability challenges; support mechanisms or adjustments may be needed\n- **10** = Very few affordability barriers; costs and financial burdens are equitably distributed and broadly manageable',TRUE),
  ('Feasibility and Implementation',3,'## 4.3 Barriers in terms of Acceptability\n\nIs the measure socially and politically acceptable to the communities and stakeholders affected? Does it align with local norms, trust levels, and stakeholder expectations?\n\n- **1** = Severe political, cultural, financial, or administrative barriers\n- **5** = Moderate obstacles that require negotiation or adaptation\n- **10** = Very few obstacles; high political, cultural, and financial compatibility',TRUE),
  ('Feasibility and Implementation',4,'## 5. Barriers in terms of Availability / Timing\n\nIs the measure legally, institutionally, and logistically in place for the target population? Are the necessary infrastructures, services, or delivery mechanisms present?\n\nHow suitable is the current moment for implementing this initiative (in terms of readiness, preconditions, and alignment with policy cycles)?\n\n- **1** = Cannot be implemented under current conditions; major prerequisites missing\n- **5** = Some prerequisites missing; partial readiness\n- **10** = Fully implementable immediately with no major preconditions',TRUE);

INSERT INTO question_options (questionId, `option`)
SELECT q.id, option_label FROM evaluation_questions q
JOIN (
  SELECT 'Age range' AS question_text, '<18' AS option_label
  UNION ALL SELECT 'Age range', '18-25'
  UNION ALL SELECT 'Age range', '25-35'
  UNION ALL SELECT 'Age range', '35-65'
  UNION ALL SELECT 'Age range', '>65'
  UNION ALL SELECT 'Living in a house with low energy efficiency', 'Yes'
  UNION ALL SELECT 'Living in a house with low energy efficiency', 'No'
  UNION ALL SELECT 'Gender', 'Woman'
  UNION ALL SELECT 'Gender', 'Male'
  UNION ALL SELECT 'Gender', 'Non-binary'
  UNION ALL SELECT 'Gender', 'Other'
  UNION ALL SELECT 'Need of a car to perform daily activities', 'Yes'
  UNION ALL SELECT 'Need of a car to perform daily activities', 'No'
  UNION ALL SELECT 'Level of education', 'No formal education'
  UNION ALL SELECT 'Level of education', 'Primary'
  UNION ALL SELECT 'Level of education', 'Secondary'
  UNION ALL SELECT 'Level of education', 'Further normal education'
  UNION ALL SELECT 'Location of residency', 'Urban area'
  UNION ALL SELECT 'Location of residency', 'Suburban area'
  UNION ALL SELECT 'Location of residency', 'Rural area'
  UNION ALL SELECT 'Economic status', 'Employed'
  UNION ALL SELECT 'Economic status', 'Unemployed'
  UNION ALL SELECT 'Economic status', 'Retired'
  UNION ALL SELECT 'Care responsibility as the main activity', 'Yes, remunerated'
  UNION ALL SELECT 'Care responsibility as the main activity', 'Yes, Non-remunerated'
  UNION ALL SELECT 'Care responsibility as the main activity', 'No'
  UNION ALL SELECT 'EU citizenship', 'Yes'
  UNION ALL SELECT 'EU citizenship', 'No'
  UNION ALL SELECT 'Disability of long-term condition', 'Yes'
  UNION ALL SELECT 'Disability of long-term condition', 'No'
  UNION ALL SELECT 'Level of income', 'Low income'
  UNION ALL SELECT 'Level of income', 'Medium income'
  UNION ALL SELECT 'Level of income', 'High income'
  UNION ALL SELECT 'Tenancy status', 'Homeowner'
  UNION ALL SELECT 'Tenancy status', 'Tenant'
) option_rows ON option_rows.question_text = q.question
WHERE q.category = 'target_population'
ON DUPLICATE KEY UPDATE `option` = VALUES(`option`);

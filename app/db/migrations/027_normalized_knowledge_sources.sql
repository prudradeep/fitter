CREATE TABLE IF NOT EXISTS knowledge_sources (
  id CHAR(36) PRIMARY KEY,
  identity_hash CHAR(64) NOT NULL,
  canonical_uri TEXT NULL,
  source_type VARCHAR(40) NOT NULL,
  content_sha256 CHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_knowledge_sources_identity_hash UNIQUE (identity_hash),
  INDEX ix_knowledge_sources_content_sha256 (content_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_source_versions (
  id CHAR(36) PRIMARY KEY,
  source_id CHAR(36) NOT NULL,
  content_sha256 CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_knowledge_source_versions_source
    FOREIGN KEY (source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE,
  CONSTRAINT uq_knowledge_source_version_hash UNIQUE (source_id, content_sha256),
  INDEX ix_knowledge_source_versions_source (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_source_chunks (
  id CHAR(36) PRIMARY KEY,
  source_version_id CHAR(36) NOT NULL,
  chunk_index INT NOT NULL,
  content LONGTEXT NOT NULL,
  page_number INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_knowledge_source_chunks_version
    FOREIGN KEY (source_version_id) REFERENCES knowledge_source_versions(id) ON DELETE CASCADE,
  CONSTRAINT uq_knowledge_source_chunk_position UNIQUE (source_version_id, chunk_index),
  INDEX ix_knowledge_source_chunks_version (source_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE knowledge_documents
  ADD COLUMN source_id CHAR(36) NULL,
  ADD INDEX ix_knowledge_documents_source_id (source_id),
  ADD CONSTRAINT fk_knowledge_documents_source
    FOREIGN KEY (source_id) REFERENCES knowledge_sources(id) ON DELETE SET NULL;

ALTER TABLE knowledge_chunks
  ADD COLUMN shared_chunk_id CHAR(36) NULL,
  ADD INDEX ix_knowledge_chunks_shared_chunk_id (shared_chunk_id),
  ADD CONSTRAINT fk_knowledge_chunks_shared_chunk
    FOREIGN KEY (shared_chunk_id) REFERENCES knowledge_source_chunks(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS knowledge_document_usages (
  id CHAR(36) PRIMARY KEY,
  knowledge_document_id CHAR(36) NOT NULL,
  scope VARCHAR(32) NOT NULL,
  user_id CHAR(36) NULL,
  session_key VARCHAR(64) NULL,
  scope_level VARCHAR(20) NOT NULL DEFAULT 'global',
  country_id CHAR(36) NULL,
  region_id CHAR(36) NULL,
  sector_id CHAR(36) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_knowledge_document_usages_document
    FOREIGN KEY (knowledge_document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  CONSTRAINT fk_knowledge_document_usages_user
    FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_knowledge_document_usages_country
    FOREIGN KEY (country_id) REFERENCES countries(id) ON DELETE SET NULL,
  CONSTRAINT fk_knowledge_document_usages_region
    FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
  CONSTRAINT fk_knowledge_document_usages_sector
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE SET NULL,
  INDEX ix_knowledge_document_usages_document (knowledge_document_id),
  INDEX ix_knowledge_document_usages_scope (scope),
  INDEX ix_knowledge_document_usages_user (user_id),
  INDEX ix_knowledge_document_usages_session (session_key),
  INDEX ix_knowledge_document_usages_country (country_id),
  INDEX ix_knowledge_document_usages_region (region_id),
  INDEX ix_knowledge_document_usages_sector (sector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO knowledge_document_usages
  (id, knowledge_document_id, scope, user_id, session_key, scope_level,
   country_id, region_id, sector_id, created_at)
SELECT UUID(), id, scope, user_id, session_key, scope_level,
       country_id, region_id, sector_id, created_at
FROM knowledge_documents;

SET @add_document_faiss_flag_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE knowledge_documents ADD COLUMN faiss_indexed TINYINT NOT NULL DEFAULT 0 AFTER scope',
    'SELECT 1')
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_documents'
    AND column_name = 'faiss_indexed'
);
PREPARE add_document_faiss_flag_stmt FROM @add_document_faiss_flag_sql;
EXECUTE add_document_faiss_flag_stmt;
DEALLOCATE PREPARE add_document_faiss_flag_stmt;

SET @add_chunk_faiss_flag_sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE knowledge_chunks ADD COLUMN faiss_indexed TINYINT NOT NULL DEFAULT 0 AFTER chunk_index',
    'SELECT 1')
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'knowledge_chunks'
    AND column_name = 'faiss_indexed'
);
PREPARE add_chunk_faiss_flag_stmt FROM @add_chunk_faiss_flag_sql;
EXECUTE add_chunk_faiss_flag_stmt;
DEALLOCATE PREPARE add_chunk_faiss_flag_stmt;

CREATE INDEX ix_knowledge_chunks_document_chunk
  ON knowledge_chunks (document_id, chunk_index);

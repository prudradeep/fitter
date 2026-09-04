# Direct Answers

## Which hazard dimensions are contradiction-blocking, and which are recoverable?

Platform co-created hazard dimension scoring has six dimensions: hazard definition, twin-transition policy fit, policy-objective fit, selected sector fit, country/region fit, and affected population groups. Critical low or clarification-needed states on hazard definition, twin-transition policy fit, policy-objective fit, selected sector fit, and country/region fit block progress until clarified or rejected after flatline; affected groups can trigger clarification/review and are handled separately from the critical set. Policy-objective fit uses the objective defined for the selected sector. [app/services/custom_hazard_validation.py :: CRITICAL_DIMENSIONS] [app/services/custom_hazard_validation.py :: SECTOR_POLICY_OBJECTIVES] [app/services/custom_hazard_validation.py :: _recommended_action()]

Contradiction as `INVALID` is handled by the evidence contradiction service rather than per hazard dimension; contradiction or contraindication forces invalid. [app/services/evidence_contradiction_service.py :: _normalize_verdict()]

## Which mitigation dimensions are contradiction-blocking?

[UNVERIFIED — could not determine fully from code in this pass.] Evidence contradiction for mitigation can force `INVALID` when evidence concepts contradict or do not align with selected sector, hazard, or mitigation measure; the complete six-dimension mitigation matrix requires a deeper trace through the mitigation validation mixins. Looked at: [app/services/evidence_contradiction_service.py :: _claim_concept_alignment()] [app/services/validation_service.py :: _validate_user_evidence_against_core_kb()].

## Does strict check alter contradiction handling, or only insufficient handling?

Strict/easy mode alters the configurable platform co-created hazard ready score and dimension floor, and mitigation storage/crowdsourcing gates. The inspected contradiction service does not branch on strict/easy mode; contradiction or contraindication forces `INVALID` regardless of mode. [app/config.py :: Settings.custom_hazard_validation_thresholds] [app/services/custom_hazard_validation.py :: _recommended_action()] [app/services/evidence_contradiction_service.py :: _normalize_verdict()]

## Does provenance capture exist anywhere in the pipeline?

Partial provenance exists during ingestion and retrieval: documents/chunks store document IDs, source URIs, page numbers, scope, and content, and search returns document ID and page number. That provenance is discarded or flattened before contradiction verdicts and PDF output; no structured AI-output-to-source-chunk provenance reaches the PDF. [app/models.py :: KnowledgeDocument] [app/models.py :: KnowledgeChunk] [app/services/knowledge_base.py :: KnowledgeBaseService._search_results()] [app/services/evidence_contradiction_service.py :: _format_l1_matches()] [app/services/report_export.py :: _report_lines()]

## Accepted evidence file formats and size limits?

Evidence upload accepts `.pdf`, `.docx`, `.md`, and `.txt`; the default upload size limit is `10 * 1024 * 1024` bytes, reported as 10 MB in errors. [app/routes/api.py :: _allowed_evidence_file()] [app/config.py :: Settings] [app/services/knowledge_base.py :: KnowledgeBaseService.ingest_file()]

## Exact Windows paths of session log files?

Packaged service stdout/stderr logs are under `%LOCALAPPDATA%\DrTransition\logs`: `backend.out.log`, `backend.err.log`, `reranker.out.log`, `reranker.err.log`, `nli.out.log`, and `nli.err.log`. LLM exchange JSONL logs default to `data/service-runtime/logs/llm_requests-YYYY-MM-DD.jsonl`; when frozen and relative, that path is resolved under `%ProgramData%\DrTransition`. [desktop/tauri/src-tauri/src/main.rs :: log_dir()] [desktop/tauri/src-tauri/src/main.rs :: start_service()] [app/config.py :: _frozen_program_data_path()] [app/services/llm_logging.py :: _dated_log_path()]

## Do the log files contain personal data or credentials?

Service stdout/stderr logs may contain exception traces and application log messages; exact contents depend on runtime failures. LLM exchange logs can contain user prompts, evidence, retrieved excerpts, and model responses when payload logging is enabled; keys named authorization, api_key, apikey, access_token, refresh_token, password, secret, and token are redacted. [desktop/tauri/src-tauri/src/main.rs :: start_service()] [app/services/llm_logging.py :: log_llm_exchange()] [app/services/llm_logging.py :: SENSITIVE_KEYS] [app/services/llm_logging.py :: _sanitize_payload()]

# Dr Transition Software Requirements Specification v2

Version: 2.0. Verification date: 2026-08-14.

## Purpose

This SRS specifies the behaviour implemented in the Dr Transition codebase as of this verification pass. Every requirement below is based on implementation reads and carries a code citation. Behaviour not determined from code is excluded from the requirement set and recorded in [GAPS.md](GAPS.md).

## Scope

Dr Transition is a FastAPI web application packaged with a Tauri Windows desktop launcher; the launcher starts the backend, reranker, and Natural Language Inference (NLI) services as local processes and writes their stdout/stderr to log files. [desktop/tauri/src-tauri/src/main.rs :: start_service()] [desktop/tauri/src-tauri/src/main.rs :: log_dir()] [packaging/windows/config/default.config.json :: backend/grounding/paths]

The backend supports authentication, guided chat sessions, hazard selection and creation, mitigation measure creation, self-evaluation, STSI probing, knowledge ingestion and retrieval, PDF report generation, JSON session export/import, and optional database Cloud Sync. [app/routes/auth.py :: login()] [app/routes/api.py :: chat()] [app/routes/api.py :: export_mitigation_report()] [app/routes/api.py :: export_session()] [app/routes/sync.py :: sync_exchange()]

Out of scope for new requirements in this document: accessibility, GDPR/data-protection compliance, multi-language support, and performance tuning. Existing code that stores personal data, logs content, or sets security controls is still described factually. [app/models.py :: AppUser] [app/services/llm_logging.py :: log_llm_exchange()] [app/config.py :: Settings]

## Deployment & Architecture

The Windows package configuration defines a local backend on `127.0.0.1:8000`, a reranker service on `127.0.0.1:8081`, an NLI service on `127.0.0.1:8082`, Ollama at `http://127.0.0.1:11434`, `%ProgramData%\DrTransition` as data storage, and `%LOCALAPPDATA%\DrTransition\logs` as log storage. [packaging/windows/config/default.config.json :: backend/grounding/ollama/paths]

The Python settings default to MySQL at `mysql+pymysql://dr_transition:dr_transition_password@localhost:3306/dr_transition`, Ollama model `qwen3.5:4b`, embedding model `nomic-embed-text`, Ollama timeout `1200` seconds, upload/import/URL limits of `10 * 1024 * 1024` bytes, JSON body limit `1 * 1024 * 1024` bytes, reranker timeout `60` seconds, NLI timeout `60` seconds, Eurostat timeout `20` seconds, Eurostat cache expiry `3` months, and sync interval `3600` seconds. [app/config.py :: Settings]

```mermaid
flowchart TD
  A[Tauri launcher] --> B{health checks}
  B -->|backend unhealthy| C[start backend exe]
  B -->|reranker unhealthy| D[start reranker exe]
  B -->|nli unhealthy| E[start nli exe]
  C --> F[backend.out.log/backend.err.log]
  D --> G[reranker.out.log/reranker.err.log]
  E --> H[nli.out.log/nli.err.log]
  F --> I[FastAPI app]
  G --> I
  H --> I
  I --> J[Ollama /api/chat and /api/embeddings]
```

Diagram citation: [desktop/tauri/src-tauri/src/main.rs :: start_services()] [desktop/tauri/src-tauri/src/main.rs :: start_service()] [app/llm.py :: ask_llm_chat()] [app/services/knowledge_base.py :: KnowledgeBaseService._embed()]

## Users & Roles

End users authenticate with email and password; signup creates an `AppUser` row with email, name, password hash, designation, organisation type, organisation name, and default role `user`. [app/routes/auth.py :: signup()] [app/models.py :: AppUser]

Admin-only routes require `require_admin_user`; implemented admin operations include metrics, session import, knowledge management subject to permission checks, prompt management subject to permission checks, and `/api/auto-user-message`. [app/auth.py :: require_admin_user()] [app/main.py :: metrics()] [app/routes/api.py :: import_session()] [app/routes/api.py :: auto_user_message()]

Signup is not invite-only in the local implementation; the signup route accepts completed user details and a valid password without an invite token. [app/routes/auth.py :: signup()]

## Knowledge Sources

Use these vocabulary mappings:

| Term | Verified internal representation | Status | Citation |
|---|---|---:|---|
| Core knowledge bank | `scope="main"` | IMPLEMENTED | [app/services/knowledge_base.py :: MAIN_KB_SCOPE] |
| Validated knowledge bank (secondary) | `scope="validated_evidence"` | IMPLEMENTED | [app/services/knowledge_base.py :: VALIDATED_EVIDENCE_SCOPE] |
| Survey findings | `scope="sector_prompt"` and survey CSV-derived tables | IMPLEMENTED | [app/services/knowledge_base.py :: SECTOR_PROMPT_SCOPE] [app/services/hazard_salience.py :: _hazard_salience()] |
| Survey hazards | `SystemHazard` plus ranking over survey CSV outputs | IMPLEMENTED | [app/models.py :: SystemHazard] [app/services/hazard_ranking_service.py :: HazardRankingService.rank_hazards()] |
| Expert open-lab hazards | `AdditionalHazard` and `AdditionalHazardProfile` | IMPLEMENTED | [app/models.py :: AdditionalHazard] [app/models.py :: AdditionalHazardProfile] |
| Platform co-created hazards | `CustomHazard` and `UserHazard` | IMPLEMENTED | [app/models.py :: CustomHazard] [app/models.py :: UserHazard] |
| STSI probing | `system_inquiry_*` fields and probe library | IMPLEMENTED | [app/models.py :: UserMitigationMeasure] [app/services/system_inquiry_probe_library.py :: system_inquiry_probe_library()] |
| End users | `AppUser` rows with role `user` unless admin | IMPLEMENTED | [app/models.py :: AppUser] |

## End-to-End Flow

An end user logs in, selects context, selects or creates one hazard, creates one or more mitigation measures for that hazard, completes 1-10 self-evaluation questions, is offered STSI probing after evaluation, and can generate a mitigation report once a persisted mitigation measure exists. [app/routes/auth.py :: login()] [app/services/chat_persistence.py :: _ensure_user_session()] [app/services/chat_mitigation_creation_evaluation.py :: _evaluation_complete_step_with_llm()] [app/services/chat_mitigation_creation_system_flow.py :: _system_inquiry_intro_step()] [app/routes/api.py :: export_mitigation_report()]

```mermaid
flowchart TD
  A[POST /api/chat] --> B[hydrate session]
  B --> C[process current phase]
  C -->|invalid input| D[ChatResponse error true]
  C -->|needs clarification| E[return clarification prompt]
  C -->|mitigation saved| F[start evaluation questions]
  F -->|score missing| G[repeat evaluation question]
  F -->|all questions answered| H[start STSI intro]
  H -->|skip| I[STSI complete skipped]
  H -->|start| J[STSI question loop]
  J -->|complete| K[persist session/message]
  D --> K
  E --> K
  I --> K
```

Diagram citation: [app/routes/api.py :: chat()] [app/services/chat_persistence.py :: _hydrate_session_from_db()] [app/services/chat_persistence.py :: _finalize_chat_response()] [app/services/chat_mitigation_creation_evaluation.py :: _handle_evaluation_answer()] [app/services/chat_mitigation_creation_system_flow.py :: _handle_system_inquiry_intro()]

## Detailed Requirements By Area

| ID | Requirement | Status | Configuration | Citation |
|---|---|---:|---|---|
| FR-AUTH-001 | The system shall authenticate users by email and password and set the auth cookie after successful verification. | IMPLEMENTED | `AUTH_COOKIE_NAME`, `auth_cookie_max_age_seconds` | [app/routes/auth.py :: login()] [app/auth.py :: set_auth_cookie()] |
| FR-AUTH-002 | The system shall rate-limit login attempts with defaults of 5 attempts, 15-minute window, and 15-minute lockout. | IMPLEMENTED | `login_rate_limit_attempts=5`, `login_rate_limit_window_seconds=15*60`, `login_rate_limit_lockout_seconds=15*60` | [app/config.py :: Settings] [app/routes/auth.py :: login()] |
| FR-AUTH-003 | The system shall allow local signup without an invite token when required fields and password rules pass. | IMPLEMENTED | `signup_rate_limit_*` | [app/routes/auth.py :: signup()] |
| FR-AUTH-004 | The system shall require admin role for admin-only dependencies. | IMPLEMENTED | User role in `AppUser.role` | [app/auth.py :: is_admin_user()] [app/auth.py :: require_admin_user()] |
| FR-CTX-001 | The system shall persist each session snapshot as JSON in `UserSession.session_data` and link it to country, region, sector, and current user. | IMPLEMENTED | Database URL | [app/services/chat_persistence.py :: _ensure_user_session()] |
| FR-CTX-002 | The system shall restore a prior session from `UserSession.session_data` and ordered `UserChatMessage` rows. | IMPLEMENTED | Database URL | [app/routes/api.py :: restore_session()] [app/services/chat_persistence.py :: _hydrate_session_from_db()] |
| FR-CTX-003 | The browser shall keep the current session key in local storage under `dr_transition_session_id`. | IMPLEMENTED | Browser local storage | [app/static/js/app.js :: sessionKey] |
| FR-HAZ-001 | The system shall rank survey hazards by salience, effect size, and reach, summing those values into `relevance_score`. | IMPLEMENTED | Eurostat cache and CSV outputs | [app/services/hazard_ranking_service.py :: HazardRankingService.rank_hazards()] |
| FR-HAZ-002 | The system shall calculate survey-hazard salience from survey CSV hazard columns as mean concern multiplied by percent of values greater than `12.0`, divided by `100`. | IMPLEMENTED | `HIGH_CONCERN_THRESHOLD=12.0` | [app/services/hazard_salience.py :: _hazard_salience()] |
| FR-HAZ-003 | The system shall calculate survey-hazard effect size from confirmed predictor odds ratios by averaging absolute natural log odds ratios. | IMPLEMENTED | `min_or` default `1.0` | [app/services/hazard_effect_size.py :: _hazard_effect_sizes()] |
| FR-HAZ-004 | The system shall use deterministic mock Eurostat population data, cache it, and label prevalence source as `Mock Eurostat cache`. | DUMMY DATA | `eurostat_cache_expiry_months=3` | [app/services/eurostat_service.py :: EurostatService._mock_profile_population()] [app/services/eurostat_service.py :: EurostatService.get_prevalence()] |
| FR-CHZ-001 | The system shall validate platform co-created hazards across hazard definition, twin-transition policy fit, sector fit, country/region fit, and affected population groups. | IMPLEMENTED | validation mode | [app/services/custom_hazard_validation.py :: validate_custom_hazard_dimensions()] |
| FR-CHZ-002 | The system shall score platform co-created hazard dimensions on a 0-10 scale and convert the weighted sum to a 0-100 overall score. | IMPLEMENTED | dimension weights | [app/services/custom_hazard_validation.py :: DIMENSION_WEIGHTS] [app/services/custom_hazard_validation.py :: _overall_score()] |
| FR-CHZ-003 | The system shall require strict hazard validation to meet ready score `75` and dimension floor `5`. | IMPLEMENTED | `validation_mode=strict` | [app/services/custom_hazard_validation.py :: VALIDATION_THRESHOLDS] |
| FR-CHZ-004 | The system shall allow easy hazard validation at ready score `45`, dimension floor `3`, or because mode is easy after critical dimensions resolve. | IMPLEMENTED | `validation_mode=easy` | [app/services/custom_hazard_validation.py :: VALIDATION_THRESHOLDS] [app/services/custom_hazard_validation.py :: _recommended_action()] |
| FR-CHZ-005 | The system shall detect hazard duplicate candidates using sequence threshold `0.86`, token threshold `0.72`, semantic threshold `0.70`, and return at most 3 candidates. | IMPLEMENTED | hard-coded thresholds | [app/services/custom_hazard_matching.py :: duplicate_candidates()] |
| FR-CHZ-006 | The system shall not save a platform co-created hazard as crowdsourced unless validation mode is strict and the crowdsourcing flag is true. | IMPLEMENTED | user toggle `crowd_sourcing_enabled`; validation mode | [app/models.py :: CustomHazard] [app/services/chat_mitigation_creation_evaluation.py :: _crowd_sourcing_visibility_notice()] [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] |
| FR-APG-001 | The system shall extract affected population groups from platform co-created hazard text using aliases and regex patterns, then deduplicate normalized groups. | IMPLEMENTED | hard-coded alias map | [app/services/custom_hazard_matching.py :: extract_affected_groups()] [app/services/custom_hazard_matching.py :: dedupe_groups()] |
| FR-MIT-001 | The system shall store mitigation measures with hazard references, measure text, reason, target population JSON, validation mode, and crowdsourcing flag. | IMPLEMENTED | validation mode; crowdsourcing flag | [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] [app/models.py :: UserMitigationMeasure] |
| FR-MIT-002 | The system shall force mitigation crowdsourcing false unless validation mode is strict. | IMPLEMENTED | `validation_mode` | [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] |
| FR-EVL-001 | The system shall ask active evaluation questions loaded from the database and require a numeric score from 1 to 10 for each answer. | IMPLEMENTED | active evaluation questions | [app/services/chat_mitigation_creation_evaluation.py :: _start_evaluation_questions()] [app/services/chat_mitigation_creation_evaluation.py :: _handle_evaluation_answer()] |
| FR-EVL-002 | The system shall store evaluation answers in `UserQuestionResponse` with score, reason, evidence, hazard reference, and mitigation measure reference. | IMPLEMENTED | Database URL | [app/services/chat_mitigation_creation_storage.py :: _store_question_response()] |
| FR-STSI-001 | The system shall use a JSON STSI probe library and fail library loading when fewer than 30 probes are present. | IMPLEMENTED | `app/system_inquiry_probe_library.json` | [app/services/system_inquiry_probe_library.py :: system_inquiry_probe_library()] |
| FR-STSI-002 | The system shall offer `Start system inquiry` and `Skip system inquiry`; skipping records `system_inquiry_skipped=True` and completes the STSI step. | IMPLEMENTED | user choice | [app/services/chat_mitigation_creation_system_flow.py :: _handle_system_inquiry_intro()] |
| FR-STSI-003 | The system shall ask selected STSI observations sequentially, allow `Skip this question`, and record skipped-question annotations with `resolution_state="open"`. | IMPLEMENTED | user choice | [app/services/chat_mitigation_creation_system_flow.py :: _handle_system_inquiry_observation()] |
| FR-STSI-004 | The system shall store STSI summary, annotations, profile, and telemetry in `UserMitigationMeasure.system_inquiry_json`. | IMPLEMENTED | Database URL | [app/services/chat_mitigation_creation_system_flow.py :: _system_inquiry_complete_step()] [app/models.py :: UserMitigationMeasure] |
| FR-RPT-001 | The system shall generate PDF mitigation reports from `/api/sessions/{session_key}/report`. | IMPLEMENTED | `scope` query parameter | [app/routes/api.py :: export_mitigation_report()] [app/services/report_export.py :: mitigation_report_pdf()] |
| FR-RPT-002 | The system shall support three PDF report scopes: `current`, `user_hazard`, and `all_hazard`. | IMPLEMENTED | `REPORT_SCOPES` | [app/services/report_export.py :: REPORT_SCOPES] [app/services/report_export.py :: _measures_for_scope()] |
| FR-RPT-003 | The report export shall require only that a current or latest persisted mitigation measure can be found; it shall not enforce completion of evaluation or STSI. | IMPLEMENTED | none | [app/services/report_export.py :: _current_measure()] [app/services/report_export.py :: mitigation_report_pdf()] |
| FR-RPT-004 | The PDF report shall include mitigation measure cards, evaluation rows, radar data, comparison cards, and STSI summary lines when present. | IMPLEMENTED | report scope | [app/services/report_export.py :: _report_lines()] |
| FR-RPT-005 | The system shall expose a separate JSON session export endpoint at `/api/sessions/{session_key}/export`. | IMPLEMENTED | authenticated current user | [app/routes/api.py :: export_session()] |
| FR-SYN-001 | Cloud Sync shall be disabled unless `sync_enabled` is true. | IMPLEMENTED | `sync_enabled=False` default | [app/config.py :: Settings] [app/routes/sync.py :: require_sync_token()] |
| FR-SYN-002 | Sync API access shall require a configured sync client token supplied by bearer auth or `X-Sync-Token`. | IMPLEMENTED | `SYNC_API_TOKEN`; sync client table | [app/routes/sync.py :: require_sync_token()] [app/services/sync_service.py :: ensure_auth_schema()] |
| FR-SYN-003 | Client sync shall push an exported bundle to `/api/sync/exchange` and apply the returned bundle. | IMPLEMENTED | `SYNC_SERVER_URL`, `SYNC_API_TOKEN` | [app/services/sync_service.py :: exchange_with_server()] |
| FR-SYN-004 | Normal client knowledge export shall include only `validated_evidence`; admin client export may include `main`, `validated_evidence`, and `sector_prompt`. | IMPLEMENTED | sync client permissions | [app/services/sync_service.py :: CLIENT_EXPORT_KNOWLEDGE_SCOPES] [app/services/sync_service.py :: ADMIN_CLIENT_EXPORT_KNOWLEDGE_SCOPES] |
| FR-PROV-001 | Knowledge search results shall retain document ID, title, source type, source URI, page number, scores, scope fields, and chunk content in returned result dictionaries. | IMPLEMENTED | retrieval scope and limit | [app/services/knowledge_base.py :: KnowledgeBaseService._search_results()] |
| FR-PROV-002 | Mitigation review prompts may include source page labels from the D2.3 conceptual review excerpts, but these are text snippets rather than stored provenance records. | PARTIAL | D2.3 constants | [app/services/chat_mitigation_creation_evaluation.py :: _d23_conceptual_review_context()] |
| FR-PROV-003 | The PDF report shall not include knowledge document IDs or chunk IDs for AI-generated validation output. | NOT PRESENT | none | [app/services/report_export.py :: _report_lines()] [app/services/report_export.py :: _evaluation_answers()] |
| NFR-SEC-001 | Production settings shall reject default/unsafe secret keys, debug mode, auto migration, sample database passwords, and payload logging unless explicitly allowed. | IMPLEMENTED | `app_env`, `llm_log_allow_production_payloads` | [app/config.py :: Settings.validate_safe_runtime_defaults()] |
| NFR-SEC-002 | Evidence and knowledge URL ingestion shall reject non-HTTP(S), unresolved, private, loopback, link-local, multicast, reserved, and unspecified hosts. | IMPLEMENTED | URL ingestion limits | [app/services/knowledge_base.py :: _validate_public_http_url()] |
| NFR-REL-001 | Ollama chat calls shall return user-facing error strings for timeout, HTTP error, connection error, invalid JSON, and empty response. | IMPLEMENTED | `ollama_timeout_seconds=1200` | [app/llm.py :: ask_llm_chat()] |
| NFR-REL-002 | Knowledge ingestion shall return an error when no readable chunks are found. | IMPLEMENTED | file parser support | [app/services/knowledge_base.py :: KnowledgeBaseService.ingest_chunks()] |
| NFR-USE-001 | The UI shall show progress rows during knowledge file and URL ingestion. | IMPLEMENTED | browser UI | [app/static/js/app.js :: knowledgeUploadForm submit handler] [app/static/js/app.js :: knowledgeUrlForm submit handler] |
| NFR-MNT-001 | LLM exchange logging shall write dated JSONL logs and/or DB rows only when enabled by settings. | IMPLEMENTED | `llm_log_*` settings | [app/services/llm_logging.py :: log_llm_exchange()] |

## Validation & Scoring

Platform co-created hazard confidence scoring uses five weighted dimensions: hazard definition `0.30`, twin-transition policy fit `0.25`, selected sector fit `0.20`, country/region fit `0.10`, and affected population groups `0.15`. [app/services/custom_hazard_validation.py :: DIMENSION_WEIGHTS]

The hazard validation score scale is 0-10 per dimension and 0-100 overall; confidence is high at percent score `>=75`, medium at `>=50`, and low below `50`. [app/services/custom_hazard_validation.py :: _clamp_score()] [app/services/custom_hazard_validation.py :: _overall_score()] [app/services/custom_hazard_validation.py :: _confidence_for_percent()]

Flatline detection for platform co-created hazards occurs when there is a prior score, validation round is at least `2`, and absolute score improvement is below `3` points. [app/services/custom_hazard_validation.py :: CLARIFICATION_IMPROVEMENT_THRESHOLD] [app/services/custom_hazard_validation.py :: _recommended_action()] [app/services/custom_hazard_validation.py :: _clarification_progress_card()]

The strict/easy toggle changes ready score and dimension floor for platform co-created hazards; the code read did not show a strict/easy branch that changes evidence contradiction invalidation. [app/services/custom_hazard_validation.py :: VALIDATION_THRESHOLDS] [app/services/evidence_contradiction_service.py :: _normalize_verdict()]

Evidence contradiction outcomes are `VALID`, `INVALID`, and `NEEDS_CLARIFICATION`; `contradiction_found` or `contraindication_found` forces `INVALID`, while missing concepts or missing core matches produce `NEEDS_CLARIFICATION`. [app/services/evidence_contradiction_service.py :: VALID_VERDICTS] [app/services/evidence_contradiction_service.py :: _normalize_verdict()] [app/services/evidence_contradiction_service.py :: _needs_clarification()]

## Provenance

Knowledge ingestion stores `KnowledgeDocument.id`, source title, source type, source URI, scope, session key for temporary/quarantined evidence, location IDs for validated evidence, and per-chunk document ID, chunk index, content, source type, source URI, page number, and location scope fields. [app/models.py :: KnowledgeDocument] [app/models.py :: KnowledgeChunk] [app/services/knowledge_base.py :: KnowledgeBaseService.ingest_chunks()]

Knowledge retrieval returns document ID, title, source type, source URI, page number, scores, scope, location IDs, and chunk content to the caller. [app/services/knowledge_base.py :: KnowledgeBaseService._search_results()]

The evidence contradiction service formats retrieved core matches into LLM prompt text with title, score, and content only; it does not preserve document IDs or chunk IDs in the contradiction verdict object. [app/services/evidence_contradiction_service.py :: _format_l1_matches()] [app/services/evidence_contradiction_service.py :: _match_items()] [app/services/evidence_contradiction_service.py :: _normalize_verdict()]

Validated user evidence can be promoted into the validated knowledge bank, but the promotion records provenance in document title/source type and scope fields, not as a structured chain from AI output to source chunks. [app/services/knowledge_base.py :: KnowledgeBaseService.promote_temporary_documents()] [app/services/validation_service.py :: _admit_inline_evidence_to_quarantine()]

The PDF report consumes stored mitigation, evaluation, affected profile, and STSI JSON fields; it does not output source document IDs, chunk IDs, or retrieval result IDs for AI-produced validations. [app/services/report_export.py :: _report_lines()] [app/services/report_export.py :: _measure_card_payload()] [app/services/report_export.py :: _evaluation_answers()]

## Data Handled

The system stores account identity and organisation profile fields in `AppUser`, password hashes in `password_hash`, chat messages in `UserChatMessage.content`, session snapshots in `UserSession.session_data`, and LLM logs in files and/or `LlmExchangeLog` depending on settings. [app/models.py :: AppUser] [app/models.py :: UserChatMessage] [app/models.py :: UserSession] [app/models.py :: LlmExchangeLog] [app/services/llm_logging.py :: log_llm_exchange()]

Evidence file formats accepted by server-side evidence upload are `.pdf`, `.docx`, `.md`, and `.txt`; the HTML file inputs use the same extensions and MIME types. [app/routes/api.py :: _allowed_evidence_file()] [app/templates/index.html :: evidence file inputs]

PDF extraction uses `pypdf.PdfReader`, DOCX extraction reads `word/document.xml` from the ZIP package, markdown/text extraction decodes UTF-8 with ignored errors, and URL extraction supports PDF, DOCX, HTML, and generic text after public URL validation. [app/services/document_text.py :: extract_pdf_page_texts()] [app/services/document_text.py :: extract_docx_text()] [app/services/knowledge_base.py :: extract_file_chunks()] [app/services/knowledge_base.py :: extract_url_chunks()]

## Interfaces

Primary HTTP interfaces include `/login`, `/signup`, `/logout`, `/api/chat`, `/api/sessions`, `/api/sessions/{session_key}`, `/api/sessions/{session_key}/export`, `/api/sessions/{session_key}/report`, `/api/knowledge/*`, `/api/prompts/*`, `/api/sync/*`, `/health`, and `/metrics`. [app/routes/auth.py :: router] [app/routes/api.py :: router] [app/routes/sync.py :: router] [app/main.py :: health()] [app/main.py :: metrics()]

## Non-Functional

Long-running operations include Ollama chat and embeddings with timeout `1200` seconds, URL fetch with stream timeout `20.0` seconds and redirect cap `5`, sync exchange with HTTP timeout `120.0` seconds, reranker timeout `60` seconds, NLI timeout `60` seconds, and desktop health wait loops using 750 ms polling. [app/llm.py :: ask_llm_chat()] [app/services/knowledge_base.py :: KnowledgeBaseService._embed()] [app/services/knowledge_base.py :: _fetch_public_url()] [app/services/sync_service.py :: exchange_with_server()] [app/config.py :: Settings] [desktop/tauri/src-tauri/src/main.rs :: wait_for_health()]

Session logs on Windows are service logs at `%LOCALAPPDATA%\DrTransition\logs\backend.out.log`, `backend.err.log`, `reranker.out.log`, `reranker.err.log`, `nli.out.log`, and `nli.err.log`; LLM exchange logs default to `data/service-runtime/logs/llm_requests-YYYY-MM-DD.jsonl` and are moved under `%ProgramData%\DrTransition` only for frozen relative paths. [desktop/tauri/src-tauri/src/main.rs :: log_dir()] [desktop/tauri/src-tauri/src/main.rs :: start_service()] [app/config.py :: _frozen_program_data_path()] [app/services/llm_logging.py :: _dated_log_path()]

LLM logs redact keys named authorization, api_key, apikey, access_token, refresh_token, password, secret, and token, but when payload logging is enabled they may include user prompt text, evidence text, retrieved excerpts, and model responses up to configured truncation limits. [app/services/llm_logging.py :: SENSITIVE_KEYS] [app/services/llm_logging.py :: _sanitize_payload()] [app/services/llm_logging.py :: log_llm_exchange()]

## Claims Verification

| Claim | Result | Code-based finding |
|---|---:|---|
| The platform is an installable Windows desktop application. | CONFIRMED | Tauri and Windows packaging define desktop config and bundled service executables. [desktop/tauri/src-tauri/src/main.rs :: load_config()] [packaging/windows/config/default.config.json] |
| A separate central server provides authentication and Cloud Sync. | REFUTED | Cloud Sync server mode exists, but local auth/signup is implemented in the app database; no central-auth-only enforcement was found. [app/routes/auth.py :: signup()] [app/routes/sync.py :: sync_exchange()] |
| LLM inference and knowledge retrieval run locally; network access is required only for initial login and Cloud Sync. | REFUTED | LLM defaults to local Ollama and retrieval is local DB/FAISS, but URL evidence ingestion and Eurostat settings can use network; signup/login are local routes. [app/llm.py :: ask_llm_chat()] [app/services/knowledge_base.py :: extract_url_chunks()] [app/config.py :: Settings] |
| Signup is invite-only, enforced at the central server. | REFUTED | Signup route does not require invite token. [app/routes/auth.py :: signup()] |
| Cloud Sync is the only protection against local data loss; there is no local backup. | UNVERIFIED | No local backup mechanism was found in inspected code; absence across whole repo requires broader audit. [app/services/sync_service.py :: exchange_with_server()] |
| A session covers exactly one hazard. | CONFIRMED | Session snapshot has one `selected_hazard`, and mitigation records link to that selected hazard reference. [app/services/chat_session.py :: ChatSession] [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] |
| A session may contain multiple mitigation measures for that hazard. | CONFIRMED | Multiple mitigation rows can be counted and report scopes can collect multiple measures for the same hazard. [app/services/chat_persistence.py :: _persisted_mitigation_measure_count()] [app/services/report_export.py :: _measures_for_scope()] |
| User input is scored for confidence; clarification questions aim to raise that score. | CONFIRMED for platform co-created hazards. | Hazard dimensions score and clarification question fields drive next action. [app/services/custom_hazard_validation.py :: validate_custom_hazard_dimensions()] |
| Flatline detection and clarification-still-needed error are the same mechanism. | REFUTED | Flatline changes recommended action to reject/validate; error responses are generated elsewhere for invalid user text or failed validation. [app/services/custom_hazard_validation.py :: _recommended_action()] [app/services/chat_mitigation_creation_system_flow.py :: _system_inquiry_observation_step()] |
| Strict ON requires every dimension supported, no contradictions; OFF allows insufficient if threshold met. | UNVERIFIED | Strict/easy threshold logic exists for platform co-created hazards, but the complete claim across every hazard and mitigation dimension was not fully determined; contradiction handling is separate and not toggled by strict/easy in inspected evidence service. [app/services/custom_hazard_validation.py :: VALIDATION_THRESHOLDS] [app/services/evidence_contradiction_service.py :: _normalize_verdict()] |
| Validated content enters the validated knowledge bank and is available later. | CONFIRMED for promoted evidence. | Temporary evidence can be promoted to `validated_evidence` with scope filters used in later search. [app/services/knowledge_base.py :: promote_temporary_documents()] [app/services/knowledge_base.py :: KnowledgeBaseService._knowledge_rows()] |
| Crowdsourcing requires both crowdsourcing setting and strict check. | CONFIRMED for stored hazards/mitigations. | Mitigation storage gates crowdsourcing by strict mode, and UI sends crowdsourcing flag. [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] [app/static/js/app.js :: sendMessage()] |
| STSI runs once per mitigation measure. | UNVERIFIED | STSI is launched after evaluation for the current mitigation and stored on that mitigation record; no global one-time invariant was proven. [app/services/chat_mitigation_creation_evaluation.py :: _evaluation_complete_step_with_llm()] [app/services/chat_mitigation_creation_system_flow.py :: _system_inquiry_complete_step()] |
| STSI is skippable and skipping does not block report export. | CONFIRMED | Skip completes STSI, and report export only requires a current persisted mitigation measure. [app/services/chat_mitigation_creation_system_flow.py :: _handle_system_inquiry_intro()] [app/services/report_export.py :: mitigation_report_pdf()] |
| STSI currently produces user-facing report content. | CONFIRMED | Report summary lines include STSI annotations and measure card includes STSI summary. [app/services/report_export.py :: _system_inquiry_summary_lines()] [app/services/report_export.py :: _measure_card_payload()] |
| Self-evaluation scores modulate STSI strictness. | UNVERIFIED | STSI selection/adjudication functions inspected did not show evaluation-score modulation. [app/services/chat_mitigation_creation_system_observations.py :: _system_inquiry_observations()] [app/services/chat_mitigation_creation_system_flow.py :: _adjudicate_system_inquiry_response()] |
| Exactly one export exists: final PDF. | REFUTED | JSON session export and import exist; PDF report has three scopes. [app/routes/api.py :: export_session()] [app/routes/api.py :: import_session()] [app/services/report_export.py :: REPORT_SCOPES] |
| Report readiness requires one mitigation, clarified, self-evaluated, and STSI executed. | REFUTED | Export requires a current measure; no evaluation/STSI readiness gate is enforced. [app/services/report_export.py :: _current_measure()] [app/routes/api.py :: export_mitigation_report()] |
| Report includes all mitigation measures in the session and probing output for each. | REFUTED | `current` scope includes one measure; other scopes can include multiple hazard-linked measures and STSI content when stored. [app/services/report_export.py :: _measures_for_scope()] [app/services/report_export.py :: _system_inquiry_summary_lines()] |
| Report includes incremental pairwise portfolio comparison. | REFUTED | Report includes comparison cards and average-score summary, not pairwise incremental comparison. [app/services/report_export.py :: _comparison_cards()] [app/services/report_export.py :: _comparison_summary()] |
| Eurostat API middleware is not ready; system currently uses dummy data. | CONFIRMED | Eurostat service returns deterministic `_mock_profile_population` data. [app/services/eurostat_service.py :: EurostatService._mock_profile_population()] |

## Acceptance Criteria

1. A reviewer can trace every requirement in this SRS to one or more cited functions or constants. [this file]
2. Running the app with default settings uses local Ollama model `qwen3.5:4b`, local backend port `8000`, upload limit `10 MB`, and sync disabled. [app/config.py :: Settings] [packaging/windows/config/default.config.json]
3. Creating a mitigation measure and answering evaluation questions produces persisted `UserMitigationMeasure` and `UserQuestionResponse` rows. [app/services/chat_mitigation_creation_storage.py :: _store_mitigation_measure()] [app/services/chat_mitigation_creation_storage.py :: _store_question_response()]
4. Requesting `/api/sessions/{session_key}/report?scope=current` returns a PDF if a current or latest mitigation measure exists for that session. [app/routes/api.py :: export_mitigation_report()] [app/services/report_export.py :: mitigation_report_pdf()]

# Dr Transition SRS v2 Gaps

## 1. Code Exists But Behaviour Could Not Be Determined

- Full mitigation-measure validation outcome matrix: code exists in `app/services/validation_service.py`, `app/services/chat_mitigation_creation_policy.py`, and related mixins, but the implementation is large and distributed; this pass verified evidence contradiction and storage behaviour but did not fully certify all six mitigation dimensions and their exact blocking rules. Resolution: trace `_validate_mitigation_*` calls end to end and add a dimension-by-dimension table. Looked at: [app/services/validation_service.py :: _validate_user_evidence_against_core_kb()] [app/services/evidence_contradiction_service.py :: validate_evidence_against_kb()].

- Exact four validation outcomes `supported`, `contradicted`, `insufficient`, and `unavailable` across every UI branch: outcome terms exist partly as statuses/verdicts and templates, but not as one central state machine. Resolution: enumerate each template and handler that renders `*_validation_failed`, `*_validation_rejected`, `*_validation_unavailable`, and grounding cards. Looked at: [app/templates/chat/hazard_validation_failed.md] [app/templates/chat/mitigation_validation_rejected.md] [app/services/evidence_contradiction_service.py :: _normalize_verdict()].

- STSI probe selection cap and exact number of probes per mitigation measure: `_system_inquiry_observations()` constructs many candidates and later code appears to screen/cap them, but the complete cap path was not fully reduced into a numeric invariant in this pass. Resolution: inspect the untruncated middle of `app/services/chat_mitigation_creation_system_observations.py` around candidate screening and sorting. Looked at: [app/services/chat_mitigation_creation_system_observations.py :: _system_inquiry_observations()] [app/services/system_inquiry_probe_library.py :: system_inquiry_probe_library()].

- Whether self-evaluation scores modulate STSI strictness: no direct evidence was found in inspected STSI functions, but a complete negative requires searching all STSI helper calls. Resolution: search references from evaluation answer storage into STSI observation/adjudication code. Looked at: [app/services/chat_mitigation_creation_evaluation.py :: _evaluation_complete_step_with_llm()] [app/services/chat_mitigation_creation_system_flow.py :: _adjudicate_system_inquiry_response()].

- Exact realistic Cloud Sync delay: code defines startup sync and interval sync defaults, plus manual sync, but real delay depends on the running client process, network, server availability, and whether the user enabled user-data sync. Resolution: runtime observation under packaged client. Looked at: [app/config.py :: Settings] [app/main.py :: _client_sync_loop_after_startup()] [app/services/sync_service.py :: exchange_with_server()].

- Complete admin capability inventory: verified `metrics`, import, prompt sync/admin permissions, and `/api/auto-user-message`, but a full route-by-route permission table was not completed. Resolution: audit all `require_admin_user`, `_can_manage_*`, and sync-client permission dependencies. Looked at: [app/auth.py :: require_admin_user()] [app/routes/api.py :: auto_user_message()] [app/routes/sync.py :: sync_prompt_create()].

## 2. Documented Or Intended But NOT PRESENT In Code

- Invite-only signup was not present in the local signup route; no invite token, invitation code, or central-server invite check was found in the implementation path. Looked at: [app/routes/auth.py :: signup()].

- A single final-PDF-only export model is not present; the code has JSON session export/import and three PDF report scopes. Looked at: [app/routes/api.py :: export_session()] [app/routes/api.py :: import_session()] [app/services/report_export.py :: REPORT_SCOPES].

- Report readiness requiring mitigation clarification, self-evaluation, and executed STSI is not present in the PDF export route; the route delegates to `mitigation_report_pdf()`, which only raises when no mitigation measure is available. Looked at: [app/routes/api.py :: export_mitigation_report()] [app/services/report_export.py :: mitigation_report_pdf()].

- Structured provenance from AI-produced output to specific source document IDs or chunk IDs is not present in report output or contradiction verdicts; retrieval has document IDs, but they are reduced to title/score/content for contradiction checks. Looked at: [app/services/knowledge_base.py :: KnowledgeBaseService._search_results()] [app/services/evidence_contradiction_service.py :: _format_l1_matches()] [app/services/report_export.py :: _report_lines()].

- Live Eurostat API middleware is not present in the executed population path; the service uses deterministic mock population responses and labels prevalence source `Mock Eurostat cache`. Looked at: [app/services/eurostat_service.py :: EurostatService._mock_profile_population()] [app/services/eurostat_service.py :: EurostatService.get_prevalence()].

## 3. Code Contradicts The Claims Listed In The Task

- Claim: signup is invite-only, enforced at central server. Contradiction: signup is local and creates `AppUser` directly after field/password/rate-limit checks. Looked at: [app/routes/auth.py :: signup()].

- Claim: exactly one export exists, the final PDF. Contradiction: `/api/sessions/{session_key}/export` returns JSON session exports, `/api/sessions/import` imports them, and `/api/sessions/{session_key}/report` returns PDF. Looked at: [app/routes/api.py :: export_session()] [app/routes/api.py :: import_session()] [app/routes/api.py :: export_mitigation_report()].

- Claim: report readiness requires mitigation created, clarified, self-evaluated, and STSI probing executed. Contradiction: PDF export only requires a current/latest mitigation measure; STSI skipped/missing still allows report generation. Looked at: [app/services/report_export.py :: _current_measure()] [app/services/report_export.py :: mitigation_report_pdf()] [app/services/chat_mitigation_creation_system_flow.py :: _handle_system_inquiry_intro()].

- Claim: Eurostat data is fetched by a batch process on first run and refreshed every 3 months. Contradiction: cache expiry is 3 months, but the population path calls `_mock_profile_population()` on cache miss rather than a live Eurostat fetch or verified batch process. Looked at: [app/services/eurostat_service.py :: EurostatService.get_profile_population()] [app/services/eurostat_service.py :: EurostatService._store_profile_population()].

- Claim: final report includes incremental pairwise portfolio comparison. Contradiction: report code emits comparison cards and average-score summary; no pairwise matrix or incremental pairwise algorithm was found in report export. Looked at: [app/services/report_export.py :: _comparison_cards()] [app/services/report_export.py :: _comparison_summary()].

- Claim: network access is required only for initial login and Cloud Sync. Contradiction: URL evidence/knowledge ingestion performs outbound HTTP(S), and sync/login are not the only network-capable paths. Looked at: [app/services/knowledge_base.py :: extract_url_chunks()] [app/services/knowledge_base.py :: _fetch_public_url()].

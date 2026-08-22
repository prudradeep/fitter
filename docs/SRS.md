# Software Requirements Specification: Dr Transition

Version: 1.0  
Date: 2026-08-11  
Status: Draft  
Prepared for: Dr Transition product and engineering team

## Index

- [1. Purpose](#1-purpose)
- [2. Product Scope](#2-product-scope)
- [3. Users](#3-users)
- [4. Reviewer Rationale](#4-reviewer-rationale)
- [5. Current End-to-End Flow](#5-current-end-to-end-flow)
  - [5.1 Authentication](#51-authentication)
  - [5.2 Country, Region, and Sector Selection](#52-country-region-and-sector-selection)
  - [5.3 Hazard Review](#53-hazard-review)
  - [5.4 Custom Hazard Creation](#54-custom-hazard-creation)
  - [5.5 Affected Population Group Review](#55-affected-population-group-review)
  - [5.6 Mitigation Measure Creation](#56-mitigation-measure-creation)
  - [5.7 Mitigation Evaluation](#57-mitigation-evaluation)
  - [5.8 System Inquiry](#58-system-inquiry)
  - [5.9 PDF Report Export](#59-pdf-report-export)
  - [5.10 Cloud Sync](#510-cloud-sync)
- [6. Data Handled by the Tool](#6-data-handled-by-the-tool)
- [7. Main Interfaces](#7-main-interfaces)
- [8. Current Validation Rules](#8-current-validation-rules)
- [9. Non-Functional Requirements](#9-non-functional-requirements)
- [10. Acceptance Criteria](#10-acceptance-criteria)
- [11. Traceability](#11-traceability)

## 1. Purpose

Dr Transition is a guided policy-analysis tool for exploring regional twin-transition risks and mitigation options. It helps authenticated users select a geography and sector, review hazards, create custom hazards, identify affected population groups, create mitigation measures, complete evaluation and system inquiry, and export the completed analysis as a PDF report.

This SRS describes the current intended flow of the tool.

## 2. Product Scope

The tool supports a conversational workflow for policy analysts, researchers, and institutional users working with transition-risk evidence. The current workflow is:

```text
Login or sign up
-> Select country
-> Select region or national scope
-> Select sector
-> Review hazards
-> Add a custom hazard or start mitigation planning
-> Review affected population groups
-> Create or adopt mitigation measure
-> Evaluate mitigation measure
-> Complete system inquiry
-> Download PDF report
```

The tool uses seeded reference data, session data, user inputs, evidence URLs/files, local knowledge-base retrieval, and LLM-assisted validation to keep the conversation grounded in the selected country, region, and sector.

## 3. Users

| User | Description | Current capabilities |
| --- | --- | --- |
| Registered user | Authenticated analyst, practitioner, researcher, or stakeholder. | Run guided sessions, create hazards, create mitigation measures, complete system inquiry, export PDF reports. |
| Admin user | Authenticated user with admin role. | Access admin-only operations such as metrics and selected prompt/sync management actions. |
| Cloud Sync client | Configured installation using a sync token. | Pull, push, or exchange permitted data bundles according to sync permissions. |

## 4. Reviewer Rationale

Dr Transition exists to make transition-risk analysis easier to review, challenge, and reuse. The workflow is structured because non-technical policy reviewers need to understand why a hazard or mitigation measure is being considered, which evidence supports it, which groups may be affected, and where uncertainty remains.

The tool must therefore do more than collect answers. It must:

- show the reason a workflow step is being asked;
- distinguish user judgement from system-suggested content;
- preserve evidence, validation outcomes, and unresolved caveats;
- let reviewers see why hazards and mitigation measures are prominent;
- make report content traceable to user inputs, seeded reference data, or validation results;
- avoid fabricating conclusions when evidence or optional data is missing.

Content quality is treated as a product requirement. A saved hazard, affected group, mitigation measure, system inquiry response, or report section must be clear enough for a reviewer to understand the policy issue, its context, its affected population, and its evidential limits without reading source code or internal logs.

## 5. Current End-to-End Flow

### 5.1 Authentication

1. The user opens Dr Transition.
2. If the user is not authenticated, the tool shows login or signup.
3. Signup collects email, name, designation, organisation type, organisation name, password, and password confirmation.
4. Login validates the user's credentials.
5. After successful authentication, the user enters the main chat workflow.
6. The tool protects authenticated routes, applies rate limits to login/signup, and stores the active user session through secure cookies.

### 5.2 Country, Region, and Sector Selection

1. The chat asks the user to select a country.
2. After country selection, the chat asks for a region or national scope.
3. After region or national scope selection, the chat asks for a sector.
4. The selected country, region, and sector become the context for all later hazard, mitigation, evidence, and report steps.
5. If the user asks an in-scope question about the workflow, the tool answers it and keeps the user on the same step.
6. If the user gives an invalid selection, the tool returns a clear error and stays on the current step.

### 5.3 Hazard Review

1. After sector selection, the tool displays hazards relevant to the selected context.
2. The hazard list can include system hazards, additional seeded hazards, and permitted user-created hazards.
3. The user can:
   - start mitigation planning for an existing hazard;
   - add a new custom hazard;
   - refresh hazards and affected population groups;
   - ask a statistical deep-dive question;
   - navigate back through available workflow options.
4. Hazard ranking uses the formula `salience_score + effect_size_score + reach_score`.
5. `salience_score` is derived from country and sector concern data where available.
6. `effect_size_score` is derived from sector hazard predictor effects where available.
7. `reach_score` is derived from matched population profiles and regional or national population share where available.
8. Missing salience, effect-size, or reach inputs contribute `0` to the relevant score rather than hiding the hazard.
9. Ranked hazard responses expose the component scores, relevance score, predictor counts, matched profiles, and regional or national population percentages so users and reviewers can see why a hazard ranks highly.
10. Hazards are sorted by descending relevance score, then descending salience score, then descending effect-size score, then hazard name.

### 5.4 Custom Hazard Creation

1. The user chooses to add a new hazard.
2. The tool asks for a custom hazard title or description.
3. The tool screens the hazard text for meaningful hazard content.
4. The tool rejects blank, meaningless, unrelated, benefit-only, or non-hazard inputs.
5. If the hazard is understandable but unclear, the tool asks for clarification before continuing.
6. When the hazard title is clear enough, the tool asks for the reason or justification.
7. After the reason is provided, the tool asks whether the user wants to add evidence.
8. If the user selects or types "No", the tool continues without evidence.
9. If the user selects or types "Yes", the tool asks for an evidence URL and/or supported evidence file.
10. If the user pastes a URL during the evidence decision, the tool treats it as evidence.
11. The tool validates the custom hazard across:
    - hazard definition;
    - twin transition policy fit;
    - sector fit;
    - country/region fit;
    - duplicate status;
    - affected population groups.
12. The tool does not move to affected population group review while a core validation dimension still needs clarification.
13. Core validation dimensions are hazard definition, twin transition policy fit, sector fit, and country/region fit.
14. If all core dimensions are supported, the tool does not ask the same core clarification again.
15. If only affected population groups need more detail, the tool asks only for affected-group clarification.
16. If the user answer does not resolve a pending clarification and the same question would be asked again, the tool returns a clarification-still-needed error instead of looping.
17. Duplicate detection compares against existing system hazards and accepted scoped custom hazards.
18. The current draft hazard is not treated as a duplicate of itself.
19. If a real duplicate is detected, the tool shows the suggested existing hazard and asks the user whether to continue, explore the existing hazard, or revise.
20. If the user chooses to continue after a duplicate warning, the tool respects that choice for the same draft.

### 5.5 Affected Population Group Review

1. After the hazard is valid enough to proceed, the tool identifies affected population groups.
2. For system hazards, affected groups come from seeded hazard-profile mappings where available.
3. For custom hazards, affected groups are extracted from the hazard text, reason, and validation context.
4. The tool shows the hazard to be co-created and the identified affected groups.
5. The user can confirm the groups or type changes.
6. The user can add, remove, or edit affected groups.
7. Generic groups such as "people" or "general population" require clarification when a more specific targetable group is needed.
8. After confirmation, the tool saves the custom hazard and affected groups.

### 5.6 Mitigation Measure Creation

1. The user starts mitigation planning from the hazard review flow.
2. The user selects a hazard for mitigation planning.
3. The tool reviews the hazard and affected population context.
4. The tool may generate practical policy recommendations or a suggested mitigation proposal.
5. The user can adopt the suggested mitigation proposal or enter a mitigation measure manually.
6. If a suggested proposal is adopted, the tool preserves the proposal, reason, and target-group mechanisms.
7. If the user enters a measure manually, the tool collects the mitigation measure and reason.
8. The tool validates the mitigation measure for:
   - clarity;
   - relevance to the selected hazard;
   - selected sector and geography fit;
   - target population fit;
   - duplicate status;
   - grounded support.
9. The tool asks for clarification when the mitigation measure or reason lacks enough detail.
10. The tool rejects clarification that only repeats the mitigation measure, original input, or existing reason.
11. The tool asks whether the user wants to add mitigation evidence.
12. If the user selects or types "No", the tool continues without evidence.
13. If the user selects or types "Yes", the tool asks for an evidence URL and/or supported evidence file.
14. If grounded validation lacks support or abstains, the tool asks for clarification through the mitigation clarification input.
15. If the same mitigation clarification question would be repeated after a user answer, the tool returns a clarification-still-needed error instead of looping.
16. The tool identifies target populations from the mitigation measure, reason, and target-group mechanisms.
17. The user reviews, confirms, adds, removes, or edits target populations.
18. The tool saves the mitigation measure, reason, target population, conclusion, evidence, validation mode, and crowd-sourcing state.
19. After mitigation measure creation is complete, the tool offers PDF report export for the current analysis.

### 5.7 Mitigation Evaluation

1. After mitigation review, the tool presents evaluation questions.
2. The user answers evaluation questions through the chat interface.
3. The tool stores evaluation answers in the active session.
4. Evaluation can include dimensions such as:
   - Direct Effect;
   - Systemic Impact;
   - Societal Transformation and Equity;
   - Accessibility;
   - Affordability;
   - Acceptability;
   - Availability/Timeliness.
5. After evaluation is complete, the tool moves to the next available workflow step.

### 5.8 System Inquiry

1. After mitigation evaluation, the user enters the system inquiry flow.
2. The tool asks system-level inquiry questions based on the selected context, hazard, affected groups, mitigation measure, and evaluation results.
3. The user responds through the chat interface.
4. The tool records system inquiry responses and telemetry according to configured retention and sync rules.
5. After system inquiry is complete, the tool offers final PDF report export.

### 5.9 PDF Report Export

1. The tool provides a PDF report export after mitigation-measure creation.
2. The tool provides a final PDF report export after system inquiry completion.
3. The PDF is generated from the latest saved session state and related persisted records.
4. The report does not require the user to re-enter information already collected in the workflow.
5. The mitigation-completion report includes:
   - selected country;
   - selected region or national scope;
   - selected sector;
   - selected hazard;
   - affected population groups;
   - mitigation measure;
   - mitigation reason;
   - target populations;
   - evidence URLs or uploaded-evidence metadata;
   - validation and grounding summaries;
   - completed evaluation answers when available.
6. The final system-inquiry report includes:
   - Policy Objectives;
   - Stakeholder and Hazard Analysis;
   - Identified Gaps and Areas Requiring Improvement;
   - Mitigation Measure Creation;
   - Mitigation Measure Evaluation;
   - Comparison of Mitigation Measures where available;
   - System Inquiry findings;
   - Conclusions and Recommendations.
7. If optional report fields are missing, the tool omits them or marks them as not provided.
8. The tool does not fabricate report content.
9. The PDF download is available only to the session owner or an authorized admin.
10. If report generation fails, the tool shows a clear error and does not change workflow state.
11. Generated PDF filenames include a safe session or project identifier and generation date.
12. A mitigation-completion report is ready when the session has a saved mitigation measure linked to the current session.
13. A final system-inquiry report is ready when the mitigation-completion threshold is met and system inquiry has either recorded at least one response or been explicitly skipped or ended by the user.
14. If a report request does not meet the readiness threshold, the API returns a clear not-ready error rather than generating a partial report.

### 5.10 Cloud Sync

1. The tool supports Cloud Sync in disabled, client, and server modes.
2. Server mode uses MySQL as the authoritative central database; client mode uses SQLite for local/offline data and must not connect directly to central MySQL.
3. Sync endpoints require a valid sync token when Cloud Sync is enabled.
4. Cloud Sync status reports enabled state, mode, device ID, client permissions, knowledge scopes, dirty indexes, and sync table names.
5. Cloud Sync pull exports only the data allowed by the sync client's permissions.
6. Cloud Sync push applies an allowed data bundle and reports inserted, updated, skipped, dirty knowledge scopes, and prompt dirty state.
7. Cloud Sync exchange applies client data and returns a server bundle in one operation.
8. Cloud Sync supports server-to-client synchronization for main, validated evidence, and sector-prompt knowledge scopes.
9. Cloud Sync supports client-to-server synchronization for validated evidence where permitted.
10. Admin Cloud Sync clients can synchronize main, validated evidence, and sector-prompt knowledge scopes where permitted.
11. Temporary knowledge is excluded from normal sync bundles.
12. User-data synchronization occurs only when the sync client has user-data permission.
13. Prompt synchronization and prompt management occur only when the sync client has prompt-management permission.
14. System inquiry telemetry can be queued and synchronized through Cloud Sync.
15. In sync-only server mode, ordinary app APIs are blocked unless the deployment explicitly enables them.

## 6. Data Handled by the Tool

The tool stores and uses:

- authenticated user account details;
- user sessions and chat messages;
- selected country, region, national scope, and sector;
- system hazards;
- additional seeded hazards;
- custom hazards;
- affected population groups;
- mitigation measures;
- mitigation target populations;
- evidence URLs and uploaded-evidence metadata;
- validation and grounding results;
- evaluation answers;
- system inquiry responses and telemetry;
- prompt records;
- Cloud Sync metadata;
- generated PDF report metadata when report metadata is persisted.

## 7. Main Interfaces

### 7.1 User Interface

The main interface is a chat workflow with:

- assistant messages;
- user messages;
- clickable workflow options;
- other-options navigation;
- text and textarea inputs;
- mitigation measure input;
- evidence URL and evidence file controls;
- validation and grounding status cards;
- PDF report download actions.

### 7.2 API Interface

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/chat` | Main chat workflow endpoint. |
| POST | `/api/stats-deep-dive` | Contextual statistical follow-up dialogue. |
| POST | `/api/auto-user-message` | Admin-only auto-user message generation. |
| GET | `/api/sessions` | List user sessions. |
| GET | `/api/sessions/{session_key}` | Restore one user session. |
| GET | `/api/hazard-salience` | Return hazard salience data. |
| GET | `/api/hazard-effect-size` | Return hazard effect-size data. |
| GET | `/api/hazards/ranked` | Return ranked hazards for a selected country, region, and sector. |
| GET | `/api/sessions/{session_key}/report.pdf` | Download the current session report as PDF. |
| GET | `/api/sessions/{session_key}/report/status` | Return whether the current session has enough data for report export. |
| GET | `/api/sync/status` | Cloud Sync status for authorized sync clients. |
| POST | `/api/sync/pull` | Export an allowed Cloud Sync bundle. |
| POST | `/api/sync/push` | Apply an allowed Cloud Sync bundle. |
| POST | `/api/sync/exchange` | Push and pull in one Cloud Sync transaction. |

## 8. Current Validation Rules

1. The selected country, region or national scope, and sector anchor all later validation.
2. The tool clarifies unclear user intent before accepting or saving user-created content.
3. Evidence is optional for custom hazard and mitigation creation.
4. A "No evidence" response continues the workflow without evidence.
5. Evidence URLs and uploaded files are used only when readable and relevant.
6. The tool distinguishes supported, contradicted, insufficient, and unavailable validation outcomes.
7. A supported outcome allows the workflow to continue and is shown in validation summaries and reports as supporting evidence or sufficient grounding.
8. A contradicted outcome blocks saving for core custom hazard dimensions and mitigation dimensions unless the user revises the content or provides new evidence that changes the outcome.
9. A contradicted non-core or evidential caveat is preserved as a warning and must appear in the validation summary and report if the related content is saved.
10. An insufficient outcome means the system cannot yet confirm the claim. For core custom hazard and mitigation requirements, the user must clarify or revise before saving. For optional evidence or non-core dimensions, the tool may allow continuation but must preserve the caveat.
11. An unavailable outcome means the relevant validation service, model, retrieval, document extraction, or URL ingestion could not complete. The user sees a validation-unavailable message and can retry, revise, continue without optional evidence where allowed, or stop the workflow.
12. The tool does not save a custom hazard while hazard definition, twin transition policy fit, sector fit, or country/region fit is contradicted or still insufficient.
13. The tool does not save a mitigation measure while required clarity, hazard relevance, sector/geography fit, or target-population fit is contradicted or still insufficient.
14. The tool does not invent missing evidence or fill missing report fields with fabricated content.
15. The tool does not move past unresolved core custom hazard clarification.
16. The tool does not repeat the same unresolved clarification question indefinitely.
17. The tool does not treat the current draft custom hazard as a duplicate of itself.
18. The tool respects a user's explicit decision to continue after a duplicate warning for the same draft.
19. Generic affected population groups require clarification when a more specific group is needed.

## 9. Non-Functional Requirements

### 9.1 Security, Privacy, and Data Protection

1. The tool requires authentication for user workflows.
2. Users can access only their own sessions unless they are authorized admins.
3. PDF report downloads are access-controlled by session ownership or admin authorization.
4. Sync endpoints require valid sync tokens.
5. Production deployments require secure secrets, secure cookies, CSRF protection, and security headers.
6. LLM payload logging is disabled outside development unless explicitly allowed.
7. The tool must collect only the account, workflow, evidence, telemetry, and report data needed to operate the service.
8. User-uploaded evidence and evidence URLs must be associated with the owning user session and not exposed to other ordinary users.
9. Reports must distinguish user-provided content, seeded reference data, and validation or system-generated summaries when those content types appear in the same report section.
10. System inquiry telemetry used for aggregate learning must be anonymised or pseudonymised and governed by the configured retention period.
11. Cloud Sync must transfer only permitted data scopes for the authenticated sync client.
12. Deployments intended for EU review or pilots must provide a privacy notice that explains controller/contact details, purpose, lawful basis, categories of data, retention, access controls, and user rights.

### 9.2 Reliability

1. If LLM, retrieval, reranker, NLI, document extraction, or URL ingestion is unavailable, the tool reports a clear validation-unavailable outcome.
2. Validation failures preserve relevant user input.
3. PDF generation failure does not corrupt session state.
4. Cloud Sync reports applied, skipped, and dirty states clearly.

### 9.3 Usability

1. Each chat step makes the next user action clear.
2. The tool keeps users on the correct step after invalid input.
3. In-scope help questions do not move the workflow forward.
4. PDF export actions appear only when the session has enough saved data for the report type.
5. Validation summaries must use plain language suitable for non-technical reviewers.
6. Where a recommendation, ranking, or validation outcome is generated by the system, the UI must expose the main reason or component scores that produced it.

### 9.4 Accessibility

1. The web interface must support keyboard navigation for primary chat, option, evidence, evaluation, and report-export controls.
2. Interactive controls must have accessible names and visible focus states.
3. Text, controls, and validation states must meet WCAG 2.1 AA contrast expectations in production themes.
4. Error, validation, and status messages must not rely on colour alone.
5. Uploaded evidence controls and generated PDF actions must remain usable with assistive technology.

### 9.5 Language Coverage

1. The alpha workflow language is English.
2. User-entered country, region, sector, hazard, mitigation, evidence, and free-text responses may contain local-language terms where they are meaningful in the selected policy context.
3. System prompts, validation summaries, workflow options, and PDF section headings are delivered in English unless a deployment explicitly enables localisation.
4. Future localisation must preserve validation meaning and report traceability rather than translating only labels.

### 9.6 Performance and Waiting Time

1. Cached or deterministic UI steps must respond within 3 seconds at p50 and 8 seconds at p95.
2. LLM-assisted validation, retrieval, and system inquiry steps must show progress or a waiting state when they exceed 3 seconds.
3. LLM-assisted validation, retrieval, and system inquiry steps must complete within 30 seconds at p95 in the alpha deployment, excluding external network outages and first-time model startup.
4. PDF generation for an eligible report must complete within 10 seconds at p95 for ordinary alpha-test sessions.
5. When a step exceeds its expected waiting time, the tool must preserve user input and show a retryable or actionable message.

### 9.7 Maintainability

1. Workflow messages are maintained through Markdown templates where appropriate.
2. LLM prompts are maintained through prompt files or prompt database rows.
3. Current workflow behavior is covered by tests for hazard creation, mitigation creation, duplicate detection, evidence decisions, clarification loops, Cloud Sync, and PDF export.

## 10. Acceptance Criteria

### 10.1 Custom Hazard Flow

1. Given a user enters a clear custom hazard, the tool collects reason, asks for optional evidence, validates core dimensions, reviews affected groups, and saves the hazard.
2. Given the user selects "No" for evidence, the tool continues without evidence.
3. Given a core custom hazard dimension still needs clarification, the tool does not move to affected population group review.
4. Given all core custom hazard dimensions are supported, the tool does not ask the same core clarification again.
5. Given the only duplicate match is the current draft hazard, the tool does not display a duplicate warning.

### 10.2 Mitigation Flow

1. Given a user selects a hazard for mitigation planning, the tool supports adopting a suggested mitigation or entering one manually.
2. Given the mitigation measure is saved, the tool offers PDF report export.
3. Given a mitigation clarification answer does not resolve the pending question, the tool returns a clarification-still-needed error instead of looping.
4. Given target populations are identified, the user can confirm, add, remove, or edit them before saving.

### 10.3 System Inquiry and PDF Export

1. Given mitigation evaluation is complete, the tool starts system inquiry.
2. Given system inquiry is complete, the tool offers final PDF report export.
3. Given a user downloads the mitigation-completion PDF, the report includes the selected context, hazard, affected groups, mitigation measure, target populations, evidence references, and validation summary.
4. Given a user downloads the final PDF after system inquiry, the report includes Policy Objectives, Stakeholder and Hazard Analysis, Identified Gaps, Mitigation Measure Creation, Mitigation Measure Evaluation, Comparison where available, System Inquiry findings, Conclusions, and Recommendations.
5. Given optional report data is missing, the report omits it or marks it as not provided.
6. Given a user requests another user's report, the tool rejects the request.
7. Given a report request lacks a saved mitigation measure, the tool returns a not-ready error and does not generate a PDF.
8. Given a final report request occurs before system inquiry is answered, skipped, or ended, the tool returns a not-ready error and does not generate a final PDF.

### 10.4 Hazard Ranking

1. Given ranked hazards are requested for a valid country, region or national scope, and sector, the tool returns hazards ordered by `salience_score + effect_size_score + reach_score`.
2. Given one or more ranking component scores are unavailable for a hazard, the missing component contributes `0` and the returned row still shows the available components.
3. Given reviewers inspect a ranked hazard row, the response includes component scores and matched population information where available.

### 10.5 Validation Outcomes

1. Given a core custom hazard validation dimension is contradicted or insufficient, the tool asks for revision or clarification and does not save the custom hazard.
2. Given a required mitigation validation dimension is contradicted or insufficient, the tool asks for revision or clarification and does not save the mitigation measure.
3. Given optional evidence validation is unavailable, the tool shows the unavailable state and allows the user to retry or continue without optional evidence where the workflow permits.
4. Given saved content has non-blocking caveats, the report includes those caveats in the validation summary.

### 10.6 Cloud Sync

1. Given Cloud Sync is disabled, sync endpoints return unavailable responses.
2. Given Cloud Sync is enabled and the token is invalid, sync endpoints reject the request.
3. Given a permitted Cloud Sync client pulls data, the response contains only permitted scopes and user data.
4. Given a permitted Cloud Sync client pushes data, the response reports inserted, updated, skipped, dirty knowledge scopes, and prompt dirty state.
5. Given temporary knowledge exists locally, Cloud Sync excludes it from normal sync bundles.

## 11. Traceability

| Area | Key files |
| --- | --- |
| Main app and middleware | `app/main.py` |
| API routes | `app/routes/api.py` |
| Auth routes | `app/routes/auth.py` |
| Sync routes | `app/routes/sync.py` |
| Request/response schemas | `app/schemas.py` |
| Database models | `app/models.py` |
| Runtime settings | `app/config.py` |
| Chat orchestration | `app/services/chat_service.py` |
| Custom hazard workflow | `app/services/chat_hazard_creation.py` |
| Custom hazard validation | `app/services/custom_hazard_validation.py` |
| Custom hazard duplicate matching | `app/services/custom_hazard_matching.py` |
| Hazard validation routing | `app/services/validation_service.py` |
| Mitigation workflow | `app/services/chat_mitigation_creation_workflow.py` |
| Knowledge base | `app/services/knowledge_base.py` |
| Cloud Sync service | `app/services/sync_service.py` |
| Prompt storage | `app/services/prompt_store.py` |
| PDF report export | Report route, service, and template modules |

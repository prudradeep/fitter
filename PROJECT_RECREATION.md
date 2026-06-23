# Dr Transition — Project Recreation Specification

This document is the source-of-truth brief for recreating the current **Dr Transition** project. Rebuild the application as a local, browser-based policy-analysis coach with the same architecture, workflows, visual language, persistence model, and integrations described below. Do not reduce it to a generic chatbot or a static mock-up.

## 1. Product definition

Dr Transition is an authenticated digital coach for **Twin-Transition policy analysis**. It guides a policy practitioner through a structured conversation:

1. Choose a country.
2. Choose a region.
3. Choose an available policy sector.
4. Review statistically ranked hazards and negative impacts.
5. Inspect affected socio-demographic profiles.
6. Add and validate regional hazards or demographic groups when needed.
7. Design a mitigation measure, justify it, attach evidence, and select its target population.
8. Review grounding quality and supported dimensions.
9. Score the measure for transformative impact and feasibility.
10. Continue into a free-form statistical or policy deep dive.

The experience is a stateful, option-assisted chat rather than a conventional multi-page form. A large visual context panel changes with the active analysis stage.

## 2. Required technology

- Python 3.12+
- FastAPI and Uvicorn
- Jinja2 server-rendered HTML
- Plain browser JavaScript and CSS; no frontend framework or build step
- SQLAlchemy 2 with PyMySQL and MySQL 8+
- Pydantic 2 and `pydantic-settings`
- Signed cookie authentication using `itsdangerous`
- Local Ollama chat model, default `mistral-nemo`
- Ollama embeddings, default `nomic-embed-text`
- FAISS CPU for vector retrieval
- HTTPX for external and local service calls
- Markdown plus Bleach for safe assistant-message rendering
- pypdf and DOCX XML extraction for evidence ingestion
- Highcharts Maps 11.4.8, Highcharts More, and the Venn module loaded from CDN
- Optional Sentence Transformers grounding services: MS MARCO MiniLM reranker and DeBERTa-v3-small NLI
- UV for dependency and lockfile management

Use the dependency declarations in `pyproject.toml` and keep a reproducible `uv.lock`.

## 3. Repository layout

```text
app/
  main.py                       # FastAPI app, startup, templates, static files
  auth.py                       # password hashing, cookie token helpers, auth guards
  config.py                     # environment settings
  database.py                   # engine, sessions, schema startup/migrations
  llm.py                        # Ollama chat client and graceful fallback
  models.py                     # SQLAlchemy models
  schemas.py                    # chat request/response DTOs
  routes/
    auth.py                     # login, signup, logout
    api.py                      # chat, sessions, analytics, knowledge APIs
  services/
    chat_service.py             # conversation state machine and persistence
    chat_session.py             # in-memory state object and store
    chat_options.py             # fixed actions and fuzzy option matching
    chat_parsers.py
    chat_formatters.py
    message_renderer.py
    prompt_loader.py
    profile_metadata.py
    coverage.py
    knowledge_base.py            # MySQL metadata/chunks + FAISS vectors
    sector_prompt_rag.py
    document_text.py
    eurostat_service.py
    normalization_service.py
    hazard_salience.py
    hazard_effect_size.py
    hazard_ranking_service.py
    reach_service.py
    grounding_models.py
    mitigation_examples.py
  grounding_servers/
    reranker.py
    nli.py
    model_runtime.py
  templates/
    auth_landing.html
    login.html
    signup.html
    index.html
    chat/*.md                    # user-facing response templates
  static/
    css/style.css
    js/app.js
    js/auth.js
    js/auth-map.js
    img/logo.png
    img/teacher.png
  prompts/
    Default_system_prompt.txt
    Energy_truth.txt
    Housing_truth.txt
    Transport_truth.txt
  outputs/
    dfs/*.csv
    step6/*_effects.csv
scripts/
  start_grounding_services.ps1
  stop_all_services.ps1
schema.sql
pyproject.toml
uv.lock
README.md
```

Keep domain truth prompts, statistical CSVs, response templates, and images as first-class source assets. They are not replaceable by placeholder text.

## 4. Configuration

Read configuration from `.env`, ignoring unknown keys. Provide these names and defaults:

```env
APP_NAME="Dr Transition"
APP_ENV="development"
APP_DEBUG=false
SECRET_KEY="development-only-secret"
DATABASE_URL="mysql+pymysql://dr_transition:dr_transition_password@localhost:3306/dr_transition"

OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_MODEL="mistral-nemo"
OLLAMA_EMBEDDING_MODEL="nomic-embed-text"
OLLAMA_TIMEOUT_SECONDS=1200

FAISS_INDEX_PATH="data/knowledge.faiss"
RERANKER_URL=""
RERANKER_TIMEOUT_SECONDS=60
RERANKER_MODEL="cross-encoder/ms-marco-MiniLM-L-6-v2"
NLI_URL=""
NLI_TIMEOUT_SECONDS=60
NLI_MODEL="cross-encoder/nli-deberta-v3-small"

MITIGATION_VERDICT_SAMPLES=3
MITIGATION_CONTRADICTION_RESAMPLES=2
MITIGATION_CONTRADICTION_CONFIRMATION_FRACTION=0.4
MITIGATION_VERDICT_TEMPERATURE=0.25
MITIGATION_SUPPORT_SCORE_FLOOR=0.15

EUROSTAT_BASE_URL="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
EUROSTAT_TIMEOUT_SECONDS=20
EUROSTAT_CACHE_EXPIRY_MONTHS=3

CORS_ORIGINS="http://localhost:8000,http://127.0.0.1:8000"
LOG_LEVEL="INFO"
```

The guided flow must degrade gracefully if Ollama or optional grounding services are unavailable. Database availability is required at application startup.

## 5. Authentication and landing page

Unauthenticated visits to `/` redirect to `/login`. `/login` and `/signup` render the same landing page with a modal opened to the relevant tab.

### Landing composition

- Full-window, pale-gray background.
- Left side: interactive Highcharts map of Europe showing coverage.
- Right side: an `AI-Powered Analysis` pill, the kicker `Your Region, Your Data + FITTER Research`, and the headline `A roadmap to JUST Transition`.
- Follow with three numbered benefits: identifying policy hazards, affected profiles, and mitigation plans.
- A prominent black `Start Analysis` button opens the auth dialog.

### Auth dialog

- Centered white modal with logo, Dr Transition title, Login and Sign up tabs.
- Login fields: email and password.
- Signup is a two-step form: **Account** then **Organisation**.
- Account fields: email, name, password, confirmation.
- Password requires at least 8 characters, uppercase, lowercase, number, and symbol. Show live rule indicators and a match indicator.
- Organisation fields: designation, organisation type, organisation name.
- Organisation types: Public sector, Private sector, Research, Civil society, Other.
- Store a secure password hash; never store plaintext passwords.
- On successful login/signup, issue a signed, HTTP-only auth cookie and redirect to `/`.
- Logout is POST-only and clears the cookie.

## 6. Authenticated workspace

The page fills the viewport and deliberately avoids document-level scrolling. Internal panels scroll as necessary.

### Top bar

A 92px black header contains:

- Logo, `Dr Transition`, and subtitle `A digital coach for Twin-Transition policy`.
- An animated white voice waveform when text-to-speech is active.
- `Manage Sessions` and `New Session` dark buttons.
- Circular settings button.
- Circular profile button showing the user's initial.

The settings popover contains toggles for Voice, Typing effect, and Auto conversation; a voice preference select (Auto/Female/Male); and a Knowledge Base button. Persist these preferences in `localStorage`.

The profile popover shows name, email, designation, organisation type, and organisation name, with Change password and red Logout actions.

### Main two-panel shell

The desktop workspace consists of a left analysis/visual panel and a right chat panel. The divider is draggable and its percentage width is saved in `localStorage`.

The layout becomes stacked and touch-friendly at narrow widths. Keep controls keyboard accessible, use semantic buttons/dialogs, visible focus states, `aria-live` for chat, and labels for every input.

### Left analysis panel

Show all of the following:

- A definition list for current Country, Region, and Sector, initially `Not selected`.
- `Session not started yet.` before the first selection.
- Selected hazard card and expandable affected socio-demographic profile list.
- During mitigation review: benefited profiles, confidence score, supported grounding dimensions with explanations, verdict stability, support corpus, and last note.
- A dynamic visualization area. Use a Europe/country map during geography stages and stage-specific icon/chart compositions later.
- Stage title and explanatory copy.
- A conditional `Deep dive into statistical findings` floating button.
- Six-step progress rail: Country, Region, Sector, Hazard, Mitigation, Evaluation. Active and completed states update from the chat response step.
- Summary metrics and charts derived from the session response: hazard count, affected-profile count, mitigation count, top-hazard regional versus national population percentages, hazard salience/effect/reach data, and evaluation trends where available.

### Right chat panel

- Header naming the policy-coach conversation.
- Scrollable message log with distinct assistant, user, and error bubbles.
- Render assistant Markdown to sanitized HTML. Preserve tables, lists, headings, links, and emphasis.
- Show a typing indicator while awaiting the API.
- Assistant messages can animate character-by-character when Typing effect is enabled.
- Option chips/buttons appear beneath prompts. Also show contextual navigation actions in a separate `Other actions` affordance.
- Fuzzy matches require a Yes/No confirmation instead of silently accepting ambiguous input.
- Include a microphone action using the browser Speech Recognition API where supported.
- Voice mode uses Speech Synthesis, configurable voice gender preference, stop/cancel behavior, and a waveform animation synchronized approximately to speech boundaries.
- Auto conversation asks the server to synthesize the next user reply and is visibly a testing feature.

### Adaptive composer modes

The composer changes based on `input_mode` from the API:

- `text`: ordinary message input.
- option/multi-select mode: selectable option chips, including batch submission where appropriate.
- reason/evidence mode: reason text, optional second justification text, optional evidence URL, and PDF/DOCX evidence upload.
- evaluation mode: score slider from 1–10, optional reason, optional evidence URL, optional PDF/DOCX.

Do not allow empty required fields. Flash invalid fields red and preserve typed values while validation messages are shown.

## 7. Dialogs and overlays

Implement native `<dialog>` overlays for:

- Rename session: required title, maximum 220 characters.
- Statistical findings: separate chat history and follow-up composer; closing returns to the unchanged main step.
- Target population: render every question with multi-select options; include `Target all general population`, Cancel, Submit.
- Knowledge base: document sidebar, multi-file upload, multi-URL ingestion, progress, semantic search, delete action, sector-prompt reindex and search.
- Change password: current password, new password, confirmation, same strength rules as signup.

## 8. Conversation state machine

Use a `ChatSession` object keyed by UUID. Keep a live in-memory cache for speed, but serialize session state into MySQL so users can restore sessions after process restart. Verify ownership before every restore or mutation.

The dispatcher must support these phases:

```text
wizard (country -> region -> sector)
hazards
stats_deep_dive
add_hazard
add_hazard_evidence
target_population_question
hazard_profile_selection
socio_demographic_review
reason_confirmation
other_actions
add_dgs
dg_reason_evidence
mitigation_measure
mitigation_duplicate_suggestion
mitigation_duplicate_report
mitigation_reason
mitigation_clarity
mitigation_target_population
mitigation_review
evaluation_question
evaluation_complete
mitigation / free-form deep dive
```

### Core transitions

1. Empty new session: greet the user and return countries.
2. Country: accept ID, exact name, or fuzzy name; then return its regions.
3. Region: accept selection, including the national-scope path if offered; then return sectors available for that country.
4. Sector: load truth/prompt data, establish system hazards and profiles, enrich with regional/national population context, rank hazards, then enter `hazards`.
5. Hazards: show ranked hazards and allow mitigation planning, custom hazard entry, refresh, and statistical deep dive.
6. Hazard selection: show affected socio-demographic profiles and let the user add more demographic groups or create a mitigation.
7. Custom hazard: collect hazard, reason, and evidence; validate input quality, semantic duplicates, and statistical support. If accepted, collect target-population answers and persist it.
8. Additional demographic groups: collect names, optional reason/evidence, reject duplicates, validate against statistics, persist accepted groups.
9. Mitigation: collect a specific measure and justification/evidence. Detect local and semantic duplicates. Clarify unresolved measure, justification, and evidence dimensions over bounded turns.
10. Freeze the clarified mitigation inputs before grounding so validation is repeatable.
11. Select benefited target populations using the same seeded demographic questionnaire vocabulary.
12. Review: show grounded synthesis and validation metadata. `Move to next step` begins evaluation.
13. Evaluation: ask active questions in order, capture 1–10 score plus optional reason/evidence, validate when statistical claims are made, and persist every response.
14. Completion: present final output and permit ongoing deep-dive questions.

`/reset` clears temporary evidence for the old session, creates a new UUID, and returns to Country. Global navigation actions can rewrite the mitigation/hazard, analyze another hazard, change sector/region/country, or start over while clearing only the dependent state.

### Fixed action labels

Preserve these labels because client behavior and fuzzy matching depend on them:

- Start Mitigation Planning
- Add a new Hazard
- Refresh hazards and DGs
- Create Mitigation Measure
- Add more DGs
- Go back to list of hazards
- Move to next step
- Yes / No / Skip
- Write mitigation measure again
- Analyse another hazard in the same sector
- Write hazard again
- Choose a different sector
- Select another region
- Start over with a different country

## 9. Chat API contract

All `/api/*` routes require an authenticated user.

### `POST /api/chat`

Accept JSON:

```json
{"message":"Spain","session_id":"optional-uuid"}
```

Also accept multipart form data with `message`, `session_id`, `evidence_url`, and `evidence_file`. Inline evidence supports PDF and DOCX. Limit extracted inline text to 5,000 characters. Temporarily index session PDF evidence and either promote or delete it after validation.

Return:

```json
{
  "session_id": "uuid",
  "step": "region",
  "bot_message": "Markdown response",
  "options": [{"id": 1, "label": "Andalusia"}],
  "other_options": [],
  "session": {
    "country": "Spain",
    "region": null,
    "sector": null,
    "selected_hazard": null,
    "mitigation_measure": null,
    "benefited_profiles": [],
    "mitigation_review": null,
    "target_population_questions": [],
    "target_population_answers": [],
    "hazard_count": 0,
    "top_hazards": [],
    "affected_profile_count": 0,
    "affected_profiles": [],
    "mitigation_measure_count": 0
  },
  "input_mode": "text",
  "input_values": {},
  "error": false,
  "validation_details": null
}
```

### Other routes

- `POST /api/stats-deep-dive` — separate statistical chat response.
- `POST /api/auto-user-message` — generate a plausible next user turn for testing.
- `GET /api/sessions` — current user's sessions, newest first.
- `GET /api/sessions/{session_key}` — restore state and full ordered chat history.
- `PATCH /api/sessions/{session_key}` — rename owned session.
- `PATCH /api/profile/password` — verify current and save strong new password.
- `GET /api/hazard-salience` — optional country/sector filter; formula `mean_concern * pct_high_concern / 100`, display threshold `> 12`.
- `GET /api/hazard-effect-size` — optional sector/hazard and positive `min_or`; formula `mean(abs(log(OR_k))) for OR_k > min_or`.
- `GET /api/hazards/ranked` — requires country and sector IDs, optional region ID; combine `salience_score + effect_size_score + reach_score`.
- `GET /api/knowledge` — list owned knowledge documents.
- `POST /api/knowledge/upload` — ingest multiple PDF/DOCX/MD/TXT files and report partial failures.
- `POST /api/knowledge/url` — ingest one or multiple newline/comma-separated URLs.
- `POST /api/knowledge/search` — return the top 10 semantic matches.
- `DELETE /api/knowledge/{document_id}` — delete only an owned document and its vectors/chunks.
- `POST /api/sector-prompts/reindex` — rebuild sector truth-prompt vectors.
- `POST /api/sector-prompts/search` — search Energy, Housing, or Transport prompt chunks.
- `GET /health` — `{"status":"ok","service":"Dr Transition"}`.

## 10. Data model

Use the schema and indexes from `schema.sql`. The required tables and responsibilities are:

- `app_users`: email, name, password hash, designation, organisation metadata, timestamps.
- `countries`, `regions`, `sectors`, `country_sectors`: reference coverage and allowed country-sector combinations.
- `user_sessions`: owner, UUID key, title, selected geography/sector, phase, serialized session state, timestamps.
- `user_chat_messages`: ordered user/assistant messages and error flag.
- `system_hazards`: reusable hazards by country/region/sector.
- `user_hazards`: per-session system or custom hazard selection, reason, evidence.
- `system_hazard_socio_demographics`: generated/system profile, reason, evidence, predictor variable.
- `user_hazard_socio_demographics`: per-user profile records and system-profile links.
- `system_hazard_socio_demographic_target_populations`: profile-to-question-option matches.
- `evaluation_questions`, `question_options`: target-population and evaluation questionnaire definitions.
- `user_mitigation_measures`: measure, reason, target population, conclusion, target groups JSON.
- `mitigation_measure_examples`: sector-specific examples used for context.
- `user_question_responses`: target-population/evaluation answers, score, reason, evidence.
- `knowledge_documents`, `knowledge_chunks`: ownership, source, scope, session, content, page metadata.
- `eurostat_population_cache`: scoped Eurostat responses with three-month expiry.
- `system_hazard_socio_demographic_population_matches`: cached profile-to-population link attempts.
- `user_activities`: auditable activity type, step, details, timestamp.

Apply cascading deletes to dependent session/user/domain content and `SET NULL` where historical answers should survive deletion of an optional association. Enforce unique email, session key, reference names, link pairs, and cache lookup tuples.

## 11. Seed coverage and questionnaire

Seed these countries: Germany, Hungary, Ireland, Italy, Portugal, Spain. Seed all regions currently listed for those countries in `schema.sql`.

Seed sectors: Energy, Housing, Transport. Seed country-sector coverage exactly as follows:

- Germany: Energy, Housing
- Hungary: Energy, Housing
- Ireland: Energy, Housing
- Italy: Energy, Transport
- Spain: Energy, Transport
- Portugal: Energy

Seed target-population questions and their exact option vocabularies from `schema.sql`: age range, low energy efficiency housing, gender, car dependency, education, residency location, economic status, care responsibility, EU citizenship, disability/long-term condition, income, and tenancy status.

Seed evaluation questions in two categories:

- **The transformative impact**: direct negative-impact effect (~40%), systemic/structural impact (~35%), societal transformation/equity (~25%).
- **Feasibility and Implementation**: accessibility, affordability, acceptability, and availability/timing barriers.

Each evaluation question explains anchors for scores 1, 5, and 10.

## 12. Statistical, retrieval, and grounding behavior

### Hazard ranking

- Read precomputed survey/statistical CSVs from `app/outputs`.
- Compute concern salience and odds-ratio effect size using the exposed formulas.
- Enrich profiles with regional and national population percentages from Eurostat.
- Cache Eurostat responses for three months.
- Combine normalized salience, effect size, and reach to rank hazards.
- Keep the top hazard summaries in the chat response for charting.

### Knowledge base

- Extract text page-by-page from PDF and structurally from DOCX; also accept Markdown, plain text, and readable web pages/PDF/DOCX URLs.
- Chunk compacted text and persist chunk/source/page metadata in MySQL.
- Generate embeddings through Ollama and store vectors in a local FAISS index.
- Scope normal documents per user; scope temporary evidence by user and session.
- Search returns content, source information, page where known, and relevance score.

### Mitigation validation

- Validate a mitigation against selected geography, sector, hazard, affected groups, justification, user evidence, main knowledge base, and sector truth prompt.
- Detect duplicate measures before accepting a new one.
- Use multiple temperature-controlled verdict samples and majority aggregation.
- Rerank retrieved passages when the reranker service is configured.
- Check synthesized claims with NLI when configured.
- Track support by dimension, confidence score, final outcome, verdict stability, support corpus label, and explanation.
- Apply the configured support-score floor.
- Handle unavailable, failed, abstained, rejected, and accepted outcomes with dedicated response templates.
- Never invent citations. Only expose claims entailed by retrieved evidence.

## 13. Visual design system

Match the existing restrained monochrome UI:

```css
--ink: #020617;
--muted: #718096;
--line: #d9dee7;
--panel: #ffffff;
--page: #f1f3f6;
--accent: #050505;
--accent-dark: #222222;
--success: #059669;
--danger: #dc2626;
--shadow: 0 14px 32px rgba(15, 23, 42, 0.12);
```

- Typography: Inter with system UI fallbacks.
- Page background: cool light gray; panels: white; primary header: true black.
- Primary actions: black/dark charcoal with white text.
- Success/toggle state: emerald green. Validation danger: red.
- Borders: soft gray; shadows subtle but spatially clear.
- Corners: approximately 10–16px on controls/cards and pill-shaped chips/toggles.
- Logo in header is 62×62 and preserves aspect ratio.
- Header controls use compact 0.78rem bold text.
- Avoid gradients except tiny interaction sheen; the overall product should feel analytical, sober, and high-contrast.
- Use motion sparingly: typing, waveform, hover lift, toggle slide, validation pulse, map/chart transitions.

Reuse `logo.png` and `teacher.png`. Do not substitute emoji for established image assets or stage SVG/icon treatments.

## 14. Browser-side state

Persist only UX preferences and the active session identifier in `localStorage`, including:

- active session UUID
- saved left/right panel width
- voice enabled
- voice preference
- typing effect enabled
- auto-conversation enabled
- transient per-session UI state where needed

The database remains authoritative for user, conversation, and analysis data. When restoring a session, rebuild all visual summaries, options, composer mode, and chat messages from the server response.

## 15. Startup and operations

On FastAPI startup:

1. Validate the MySQL connection.
2. Apply `schema.sql` idempotently.
3. Run SQLAlchemy `create_all` as a safety net.
4. Apply runtime schema compatibility checks.

Run the main application with:

```powershell
uv sync
uv run uvicorn app.main:app --reload --reload-dir app
```

For full grounding:

```powershell
uv sync --extra grounding
.\scripts\start_grounding_services.ps1
```

The launcher starts the FastAPI app on port 8000, reranker on 8081, and NLI on 8082, writes PID/log files under `data/service-runtime`, and has a matching safe stop script.

## 16. Recreation acceptance checklist

The recreation is complete only when all of these are true:

- A new user can sign up through both steps, log out, and log back in.
- Unauthenticated API and workspace access is rejected or redirected appropriately.
- The Europe landing map and stage maps render from Highcharts map data.
- A user can complete Country → Region → Sector using buttons or typed/fuzzy selections.
- Available sectors honor the seeded country-sector matrix.
- Hazards and affected profiles are loaded, ranked, persisted, and visualized.
- The user can add a custom hazard and demographics with reason/evidence validation.
- The user can create, clarify, ground, target, and review a mitigation measure.
- The user can answer every evaluation question and reach completion.
- Evidence URL and PDF/DOCX submission work in relevant composer modes.
- Knowledge files/URLs can be ingested, searched, listed, and deleted.
- Multiple sessions can be created, renamed, switched, restored after restart, and kept isolated by user.
- Voice, typing, auto-conversation, resizable panels, profile, and settings controls work.
- Optional Ollama/reranker/NLI failures produce honest fallback states rather than crashes.
- Markdown is sanitized before insertion into the DOM.
- All database ownership checks prevent cross-user session/document access.
- The desktop UI visually matches the black-header, white-panel, pale-gray workspace described here and remains usable on mobile.
- `GET /health` returns HTTP 200 and the expected JSON.

## 17. Canonical implementation sources

When this specification and a source asset differ, preserve the behavior and content in these files, in order of authority:

1. `schema.sql` for relational schema and seed data.
2. `app/services/chat_service.py` and `app/services/chat_session.py` for workflow behavior.
3. `app/routes/api.py` and `app/routes/auth.py` for HTTP contracts.
4. `app/templates/index.html`, `auth_landing.html`, `app/static/js/*.js`, and `app/static/css/style.css` for UI behavior and appearance.
5. `app/templates/chat/*.md` for exact conversation copy.
6. `app/prompts/*` and `app/outputs/*` for domain and statistical truth.
7. `app/models.py`, `app/config.py`, and `pyproject.toml` for runtime implementation details.

The result should feel like the same product, not merely a product inspired by it.

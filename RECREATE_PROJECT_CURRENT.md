# Dr Transition — Current Project Recreation Guide

This guide explains how to recreate the current **Dr Transition** project as it exists in this repository. It covers the runtime stack, source assets, database setup, seed process, app commands, and behavior that must be preserved.

## 1. Product summary

Dr Transition is a local browser-based FastAPI application for guided **Twin-Transition policy analysis**. It uses an authenticated chat workflow to help a policy practitioner:

1. Select a country, region, and sector.
2. Review ranked hazards and additional hazards.
3. Inspect affected socio-demographic profiles and Eurostat population context.
4. Add user-created hazards and validate them against twin-transition policy context.
5. Create mitigation measures.
6. Review practical considerations, current policy implementations, and new policy suggestions.
7. Select target populations.
8. Provide mitigation reasoning and evidence.
9. Review grounding/validation results.
10. Continue into evaluation and statistical/policy deep dives.

The product is a stateful policy coach, not a generic chatbot or static mock-up.

## 2. Technology stack

- Python 3.12+
- FastAPI + Uvicorn
- Jinja2 templates
- Plain JavaScript and CSS; no frontend build step
- SQLAlchemy 2 + PyMySQL
- MySQL 8+
- Pydantic 2 and `pydantic-settings`
- Signed cookie auth with `itsdangerous`
- Ollama chat model, default `mistral-nemo`
- Ollama embedding model, default `nomic-embed-text`
- FAISS CPU for vector search
- Highcharts Maps / Highcharts More / Venn module from CDN
- Optional grounding services using Sentence Transformers
- UV for dependency management

Use [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock) as the dependency source of truth.

## 3. Important repository structure

```text
app/
  main.py
  auth.py
  config.py
  database.py
  llm.py
  models.py
  schemas.py
  seed_data.py
  routes/
    api.py
    auth.py
  services/
    chat_service.py
    chat_session.py
    chat_options.py
    chat_parsers.py
    chat_formatters.py
    message_renderer.py
    knowledge_base.py
    eurostat_service.py
    normalization_service.py
    hazard_salience.py
    hazard_effect_size.py
    hazard_ranking_service.py
    reach_service.py
    grounding_models.py
    sector_prompt_rag.py
    document_text.py
    profile_metadata.py
  grounding_servers/
    reranker.py
    nli.py
    model_runtime.py
  templates/
    auth_landing.html
    login.html
    signup.html
    index.html
    chat/*.md
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
data/
  fitter_d42_text.txt
  fitter_d42_ch4_pages_43_56.txt
scripts/
  seed_database.ps1
  start_grounding_services.ps1
  stop_all_services.ps1
schema.sql
mm.csv
additionalHazards.csv
additionalHazardProfiles.csv
MM Target group.xlsx
sectoral_challenges.xlsx
hazards.xlsx
FITTER D4.2 FINAL.pdf
README.md
pyproject.toml
uv.lock
```

Do not replace the CSV/XLSX/PDF/prompt/template/static assets with placeholders. The current behavior depends on them.

## 4. Environment setup

Create `.env` from `.env.example`.

```powershell
Copy-Item .env.example .env
```

Important settings:

```env
APP_NAME="Dr Transition"
APP_ENV=development
APP_DEBUG=true
SECRET_KEY="change-this-secret-key-before-production"

DATABASE_URL="mysql+pymysql://dr_transition:dr_transition_password@localhost:3306/dr_transition"

OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_MODEL="mistral-nemo"
OLLAMA_EMBEDDING_MODEL="nomic-embed-text"
OLLAMA_TIMEOUT_SECONDS=120

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

CORS_ORIGINS="http://localhost:8000,http://127.0.0.1:8000"
LOG_LEVEL=INFO
```

`app/config.py` also supports:

```env
EUROSTAT_BASE_URL="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
EUROSTAT_TIMEOUT_SECONDS=20
EUROSTAT_CACHE_EXPIRY_MONTHS=3
```

## 5. Install dependencies

Install UV if it is not already installed:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Install application dependencies:

```powershell
uv sync
```

Install optional grounding dependencies:

```powershell
uv sync --extra grounding
```

## 6. MySQL setup

Create the database and user:

```sql
CREATE DATABASE dr_transition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'dr_transition'@'localhost' IDENTIFIED BY 'dr_transition_password';
GRANT ALL PRIVILEGES ON dr_transition.* TO 'dr_transition'@'localhost';
FLUSH PRIVILEGES;
```

The app requires MySQL at startup.

## 7. Seed schema and reference data

The current app does **not** reload `schema.sql` or CSV/XLSX data on every startup. Use the seed command when creating a fresh database or after changing source data files.

Fresh setup:

```powershell
.\scripts\seed_database.ps1
```

Equivalent:

```powershell
uv run python -m app.seed_data
```

Reload only CSV/XLSX reference data when schema is already up to date:

```powershell
.\scripts\seed_database.ps1 -SkipSchema
```

Equivalent:

```powershell
uv run python -m app.seed_data --skip-schema
```

The seeder loads:

- [schema.sql](schema.sql), unless schema is skipped.
- Core country, region, sector, country-sector, question-option, and evaluation data.
- Mitigation examples from [mm.csv](mm.csv).
- Additional hazards from [additionalHazards.csv](additionalHazards.csv).
- Additional hazard profiles from [additionalHazardProfiles.csv](additionalHazardProfiles.csv).
- Mitigation target groups from [MM Target group.xlsx](<MM Target group.xlsx>).
- Sectoral challenges from [sectoral_challenges.xlsx](sectoral_challenges.xlsx).
- Policy/system-hazard mitigation effects from [hazards.xlsx](hazards.xlsx).

Current data rules:

- Ignore additional-hazard value `Not addressed`.
- Ignore mitigation target-group value `No`.
- Ignore blank and `Not applicable` hazard mitigation effects.
- Normalize mitigation target groups through question-option references.
- Keep `match_value`.
- Use `sector_id`, not duplicated sector name, in normalized mappings.
- Store `country_id` on mitigation measure policies using `policy_code` and `countries.map_code`.
- Create/handle policy records per sector when a policy maps to multiple sectors.

## 8. App startup behavior

On startup, [app/main.py](app/main.py) currently:

1. Validates the database connection.
2. Runs SQLAlchemy `Base.metadata.create_all()` as a safety net.
3. Runs `ensure_runtime_schema()` for compatibility checks.

It does not automatically apply the full schema or reload seed files.

## 9. Run the app

```powershell
uv run uvicorn app.main:app --reload --reload-dir app
```

Open:

```text
http://localhost:8000
```

Health check:

```text
http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","service":"Dr Transition"}
```

## 10. Ollama setup

Install Ollama and pull the default models:

```powershell
ollama pull mistral-nemo
ollama pull nomic-embed-text
```

The app uses:

```text
http://localhost:11434
```

The guided flow should degrade gracefully if Ollama is unavailable, but LLM synthesis, embeddings, validation, and knowledge search will be limited.

## 11. Optional grounding services

Start grounding services:

```powershell
uv sync --extra grounding
.\scripts\start_grounding_services.ps1
```

Default ports:

- Main app: `8000`
- Reranker: `8081`
- NLI: `8082`

Verify:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8081/health
Invoke-RestMethod http://localhost:8082/health
```

Stop services:

```powershell
.\scripts\stop_all_services.ps1
```

Runtime logs/PIDs are under:

```text
data/service-runtime/
```

## 12. Authentication requirements

Unauthenticated visits to `/` redirect to `/login`.

Preserve:

- Login and signup modal.
- Two-step signup: account then organisation.
- Password strength rules: 8+ characters, uppercase, lowercase, number, symbol.
- Password confirmation match indicator.
- Secure password hashing.
- Signed HTTP-only auth cookie.
- POST-only logout.
- Auth checks on `/api/*`.

## 13. Main UI requirements

The authenticated workspace is implemented mainly by:

- [app/templates/index.html](app/templates/index.html)
- [app/static/js/app.js](app/static/js/app.js)
- [app/static/css/style.css](app/static/css/style.css)

Preserve:

- Black top bar with logo, title, session buttons, settings, and profile menu.
- Two-panel layout with draggable divider and saved width.
- Left analysis panel for country/region/sector, selected hazard, affected profiles, stage visuals, and mitigation review context.
- Right chat panel with sanitized Markdown assistant messages, user messages, options, composer modes, typing state, and validation details.
- Settings for voice, typing effect, and auto conversation.
- Session management, rename, restore, and new-session behavior.
- Knowledge base dialog.
- Statistical deep-dive dialog.
- Target-population dialog.
- Evidence URL and PDF/DOCX upload where supported.

Current mitigation UI rule:

- During mitigation measure/reason/duplicate/clarity continuation, the left panel should show the selected hazard and affected socio-demographic profiles at the top.
- The population comparison table should not be shown in that mitigation continuation state.

If [app/static/js/app.js](app/static/js/app.js) changes, bump the cache query string in [app/templates/index.html](app/templates/index.html).

## 14. Conversation flow

The current flow includes these steps:

```text
country
national_scope
region
sector
hazards
stats_deep_dive
add_hazard
hazard_reason_evidence
target_population_question
hazard_profile_selection
socio_demographic_review
reason_confirmation
add_dgs
dg_reason_evidence
mitigation_measure
mitigation_duplicate_suggestion
mitigation_duplicate_report
mitigation_reason
mitigation_clarity
mitigation_target_population
mitigation_target_population_review
mitigation_review
evaluation_question
evaluation_complete
complete
```

The server response drives frontend state through:

- `session_id`
- `step`
- `bot_message`
- `options`
- `other_options`
- `session`
- `input_mode`
- `input_values`
- `validation_details`
- `error`

## 15. Fixed action labels to preserve

Preserve these labels because matching and UI behavior depend on them:

- Start Mitigation Planning
- Add a new Hazard
- Show additional hazards
- Refresh hazards and DGs
- Create Mitigation Measure
- Continue with current mitigation measure
- Add more DGs
- Go back to list of hazards
- Move to next step
- Yes
- No
- Skip
- Write mitigation measure again
- Analyse another hazard in the same sector
- Write hazard again
- Choose a different sector
- Select another region
- Start over with a different country

## 16. Hazard behavior

Hazards are driven by system data, statistical ranking, additional hazard mappings, and user-added hazards.

Preserve:

- System hazard listing filtered by selected country/sector/region where applicable.
- Additional hazards filtered by selected country and sector.
- Additional hazards shown through a dedicated `Show additional hazards` option.
- User-added hazard validation within twin-transition policy context only.
- Target-population mapping for additional hazard profiles.
- Eurostat population lookup/cache for system, additional, and user-added profiles where possible.
- Tooltip explanations for Salience, Reach, Effect size, and Relevance.
- Label `Effect size`, not `Effect Relevance`.

## 17. Mitigation behavior

Mitigation planning includes three explanatory sections:

1. **Practical Considerations**
   - Shows available practical responses.
   - Includes `Yes`, `No`, and `Continue with current mitigation measure`.
   - Continuing uses the current policy measure as the proposed mitigation measure and proceeds to reasoning.
2. **Current Policy Implementations**
   - Uses `mitigation_measure_examples.measure` data.
   - Generalizes headings.
   - Includes implementation country, summary, evidence, and reference links.
3. **New policy suggestions**
   - Uses `mitigation_measure_policies` for the same country and sector.
   - Uses mapped target groups and system hazard mitigation effects.
   - Scores target-population match against policy target population.
   - Uses target-group values `Partially` and hazard mitigation effect values `High`, `Medium`, `Low`.
   - Does not display raw `PP` or `New` in target-group match text.
   - Shows top/best-scoring policy suggestions only.

Avoid duplicated `New policy suggestions` headings.

## 18. API overview

All `/api/*` routes require authentication.

Main endpoint:

```http
POST /api/chat
```

JSON example:

```json
{
  "message": "Spain",
  "session_id": "optional-session-id"
}
```

Multipart evidence fields are supported where relevant:

- `message`
- `session_id`
- `evidence_url`
- `evidence_file`

Important routes:

- `POST /api/stats-deep-dive`
- `POST /api/auto-user-message`
- `GET /api/sessions`
- `GET /api/sessions/{session_key}`
- `PATCH /api/sessions/{session_key}`
- `PATCH /api/profile/password`
- `GET /api/hazard-salience`
- `GET /api/hazard-effect-size`
- `GET /api/hazards/ranked`
- `GET /api/knowledge`
- `POST /api/knowledge/upload`
- `POST /api/knowledge/url`
- `POST /api/knowledge/search`
- `DELETE /api/knowledge/{document_id}`
- `POST /api/sector-prompts/reindex`
- `POST /api/sector-prompts/search`
- `GET /health`

## 19. Data model summary

Use [schema.sql](schema.sql), [app/models.py](app/models.py), and [app/database.py](app/database.py) as the authority.

Important table groups:

- Users/auth: `app_users`
- Sessions/messages: `user_sessions`, `user_chat_messages`
- Geography/sectors: `countries`, `regions`, `sectors`, `country_sectors`
- System/user hazards: `system_hazards`, `user_hazards`
- Socio-demographics: `system_hazard_socio_demographics`, `user_hazard_socio_demographics`
- Target populations: `system_hazard_socio_demographic_target_populations`, `question_options`
- Mitigation examples/policies: `mitigation_measure_examples`, `mitigation_measure_policies`
- Mitigation mappings: `mitigation_measure_target_groups`, `mitigation_measure_policy_additional_hazards`, `mitigation_measure_policy_system_hazards`
- User mitigation/evaluation: `user_mitigation_measures`, `user_question_responses`
- Knowledge: `knowledge_documents`, `knowledge_chunks`
- Eurostat/cache: `eurostat_population_cache` and profile population match tables
- Audit/activity: `user_activities`

## 20. Seeded country-sector coverage

Countries:

- Germany
- Hungary
- Ireland
- Italy
- Portugal
- Spain

Sectors:

- Energy
- Housing
- Transport

Coverage:

- Germany: Energy, Housing
- Hungary: Energy, Housing
- Ireland: Energy, Housing
- Italy: Energy, Transport
- Spain: Energy, Transport
- Portugal: Energy

## 21. Knowledge and evidence behavior

Preserve:

- PDF, DOCX, Markdown, plain text, and URL ingestion.
- PDF page-level extraction.
- DOCX XML extraction.
- MySQL storage for document/chunk metadata.
- FAISS storage for vectors at `FAISS_INDEX_PATH`.
- Ollama embeddings.
- User-scoped normal knowledge documents.
- Session-scoped temporary evidence.
- No fabricated citations.

## 22. Visual design notes

Keep the current analytical UI:

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

Design expectations:

- Black header.
- White panels/cards.
- Pale-gray page background.
- Rounded controls/cards.
- Accessible focus states.
- Sanitized Markdown rendering.
- Responsive stacked layout on narrow screens.
- Existing logo and teacher image assets.

## 23. Smoke-test checklist

After recreating the project:

1. `uv sync` completes.
2. MySQL is reachable from `DATABASE_URL`.
3. `.\scripts\seed_database.ps1` completes.
4. `uv run uvicorn app.main:app --reload --reload-dir app` starts.
5. `/health` returns `{"status":"ok","service":"Dr Transition"}`.
6. Signup works.
7. Login/logout works.
8. Country → Region → Sector flow works.
9. Sector options match the country-sector matrix.
10. Hazard listing appears.
11. Additional hazards appear only through `Show additional hazards`.
12. Selected hazard shows affected socio-demographic profiles.
13. User-created hazards validate against twin-transition policies.
14. Eurostat profile population context appears where data is available.
15. Mitigation measure entry works.
16. Practical Considerations includes `Continue with current mitigation measure`.
17. Current Policy Implementations appears.
18. New policy suggestions appears once, with ranked suggestions.
19. Mitigation reason step shows selected hazard/profiles on the left and no population comparison table.
20. Target population dialog saves selected groups.
21. Mitigation review shows grounding confidence/status/checks.
22. Evaluation can be completed.
23. Session restore works after server restart.
24. Knowledge upload/search works when Ollama embeddings are available.
25. Optional reranker/NLI services fail gracefully if not configured.

## 24. Source-of-truth priority

If this guide conflicts with implementation files, trust the current source in this order:

1. [schema.sql](schema.sql)
2. [app/database.py](app/database.py)
3. [app/models.py](app/models.py)
4. [app/services/chat_service.py](app/services/chat_service.py)
5. [app/services/chat_session.py](app/services/chat_session.py)
6. [app/routes/api.py](app/routes/api.py)
7. [app/routes/auth.py](app/routes/auth.py)
8. [app/templates/index.html](app/templates/index.html)
9. [app/static/js/app.js](app/static/js/app.js)
10. [app/static/css/style.css](app/static/css/style.css)
11. [app/templates/chat](app/templates/chat)
12. [app/prompts](app/prompts)
13. [app/outputs](app/outputs)
14. Root CSV/XLSX/PDF data files.

The recreated app should behave like this product, with the same data-driven workflow and UI, not merely a similar prototype.

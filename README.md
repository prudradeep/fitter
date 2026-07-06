# Dr Transition

Dr Transition is a local browser-based FastAPI application for guided Twin-Transition policy analysis. It uses a single chat endpoint to walk users through country, region, and sector selection, with MySQL-backed reference data and optional local Ollama support.

## Prerequisites

- Python 3.12+
- UV
- MySQL 8+
- FAISS for local knowledge-base vector search
- Ollama, with the chat and embedding models available locally

## Install UV

On macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Configure Environment

Copy the example file and adjust credentials for your local MySQL instance:

```bash
cp .env.example .env
```

Example database URL:

```env
DATABASE_URL="mysql+pymysql://drtransition:drtransition_password@localhost:3306/drtransition"
```

## MySQL Setup

Create the database using the SQL file:

```bash
mysql -u root -p < schema.sql
```

Alternatively, create a database and user manually, then apply the SQL file:

```sql
CREATE DATABASE drtransition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'drtransition'@'localhost' IDENTIFIED BY 'drtransition_password';
GRANT ALL PRIVILEGES ON drtransition.* TO 'drtransition'@'localhost';
FLUSH PRIVILEGES;
```

```bash
mysql -u drtransition -p drtransition < schema.sql
```

## Seed Reference Data

The application no longer applies `schema.sql` or reloads CSV/XLSX reference data
on every startup. Run the seed command only when setting up the database or after
changing the source files such as `mm.csv`, `additionalHazards.csv`,
`additionalHazardProfiles.csv`, `MM Target group.xlsx`, `sectoral_challenges.xlsx`,
or `hazards.xlsx`.

PowerShell:

```powershell
.\scripts\seed_database.ps1
```

Cross-platform:

```bash
uv run python -m app.seed_data
```

If the schema is already up to date and you only want to reload CSV/XLSX data:

```powershell
.\scripts\seed_database.ps1 -SkipSchema
```

```bash
uv run python -m app.seed_data --skip-schema
```

## Ollama Setup

Install Ollama from https://ollama.com, start the Ollama service, then pull the chat and embedding models:

```bash
ollama pull mistral-nemo
ollama pull nomic-embed-text
```

The app calls Ollama at:

```text
http://localhost:11434
```

The guided wizard works even if Ollama is not running; `app/llm.py` returns a graceful fallback message when the local model is unavailable.

## FAISS Knowledge Base

The app stores knowledge-base document metadata and chunk text/source/page records in MySQL. It stores vector embeddings in a local FAISS index file. Configure the index path and embedding model in `.env` when needed:

```env
FAISS_INDEX_PATH="data/knowledge.faiss"
OLLAMA_EMBEDDING_MODEL="nomic-embed-text"
```

## Grounding Model Services

Mitigation validation includes two dedicated local model services:

```env
RERANKER_URL="http://localhost:8081/rerank"
NLI_URL="http://localhost:8082/entail"
MITIGATION_VERDICT_SAMPLES=3
MITIGATION_VERDICT_TEMPERATURE=0.25
MITIGATION_SUPPORT_SCORE_FLOOR=0.15
```

Install the model dependencies and start the main app, reranker, and NLI services:

```powershell
uv sync --extra grounding
.\scripts\start_grounding_services.ps1
```

The first inference downloads the configured Hugging Face models. Defaults:

```env
RERANKER_MODEL="cross-encoder/ms-marco-MiniLM-L-6-v2"
NLI_MODEL="cross-encoder/nli-deberta-v3-small"
```

Verify the services:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8081/health
Invoke-RestMethod http://localhost:8082/health
```

Stop all processes started by the launcher:

```powershell
.\scripts\stop_all_services.ps1
```

Logs and PID files are stored under `data/service-runtime/`.

The reranker receives `{"query": "...", "documents": ["...", "..."]}` and returns
`{"scores": [0.91, 0.42]}`. The NLI service receives
`{"pairs": [{"premise": "...", "hypothesis": "..."}]}` and returns
`{"results": [{"label": "entailment", "score": 0.94}]}`.

If a service is unavailable, mitigation validation falls back to the existing combined retrieval
score or strict LLM entailment verification.

## Install Dependencies

```bash
uv sync
```

## Run Locally

To start only the main application:

```bash
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

## Windows Desktop Installer

The repository includes an initial Windows desktop packaging layer under
`desktop/tauri/` and `packaging/windows/`.

The packaged desktop target is:

- `DrTransition.exe`, a native Tauri/WebView2 launcher
- `drtransition-backend.exe`, the main FastAPI backend
- `drtransition-reranker.exe`, the grounding reranker service
- `drtransition-nli.exe`, the grounding NLI service

On launch, `DrTransition.exe` starts the backend, reranker, and NLI services as
hidden local processes, waits for their health checks, then opens the app in its
own desktop window instead of the user's browser.

Build documentation is in:

```text
docs/WINDOWS_DESKTOP_INSTALLER.md
```

## Open Conversation Selection Regression

Generate the Country -> Region -> Sector test-case workbook and the Pass/Fail
results workbook with one command:

```bash
uv run python tests/run_open_conversation_selection_regression.py
```

This creates these files in the current working directory:

```text
open_conversation_selection_test_cases.xlsx
open_conversation_selection_test_results.xlsx
```

To run only the workbook/unit tests:

```bash
uv run python -m unittest tests.test_open_conversation_selection_test_cases tests.test_chat_selection_engine tests.test_conversational_selection tests.test_question_intent
```

The lower-level scripts are also available when needed:

```bash
uv run python tests/generate_open_conversation_selection_test_cases.py
uv run python tests/run_open_conversation_selection_cases.py
```

## API

The full chat flow uses one endpoint:

```http
POST /api/chat
Content-Type: application/json
```

Request:

```json
{
  "message": "Spain",
  "session_id": "optional-session-id"
}
```

Response:

```json
{
  "session_id": "uuid",
  "step": "region",
  "bot_message": "Great. Select your region in Spain.",
  "options": [{ "id": 1, "label": "Andalusia" }],
  "session": {
    "country": "Spain",
    "region": null,
    "sector": null
  },
  "error": false
}
```

Reset a session:

```json
{
  "message": "/reset",
  "session_id": "existing-session-id"
}
```

## Project Structure

```text
app/
  main.py
  database.py
  models.py
  llm.py
  config.py
  schemas.py
  routes/
    api.py
  services/
    chat_service.py
  templates/
    index.html
  static/
    css/style.css
    js/app.js
    img/
schema.sql
.env.example
pyproject.toml
README.md
```

## Notes

- Session state is stored in memory using UUID keys, while reference data is stored in MySQL.
- Restarting the server clears in-memory sessions.
- The first three progress stages are active now: Country, Region, and Sector. Hazards, Mitigation, and Evaluation are displayed as future workflow stages.

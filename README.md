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

Create the database and seed the reference data using the SQL file:

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

## Install Dependencies

```bash
uv sync
```

## Run Locally

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

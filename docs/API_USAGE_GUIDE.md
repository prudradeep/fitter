# Swagger and API usage guide

This guide covers the HTTP APIs exposed by the main Dr Transition app and its two optional grounding services. The checked-in OpenAPI 3.1 files are [`openapi/main.openapi.json`](openapi/main.openapi.json), [`openapi/reranker.openapi.json`](openapi/reranker.openapi.json), and [`openapi/nli.openapi.json`](openapi/nli.openapi.json). Regenerate them after route changes with `uv run python scripts/export_openapi.py` from the repository root.

## Open Swagger UI

Start the configured main app as described in [README.md](../README.md), for example `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`. Open `http://127.0.0.1:8000/docs` for Swagger UI or `http://127.0.0.1:8000/openapi.json` for the same enhanced schema as JSON. `/redoc` redirects to Swagger UI. The UI's JavaScript and CSS are served locally from `/static`, so they work with the app's Content Security Policy and do not require CDN access. The schema includes body fields and security information that FastAPI cannot infer from handlers that call `request.form()` or parse JSON themselves.

The optional reranker and NLI services have their own Swagger UIs at `http://127.0.0.1:8081/docs` and `http://127.0.0.1:8082/docs` when running on the default local ports. They also expose `/openapi.json`. These services are intended for local/internal use. Avoid uploading specs or tokens to a public Swagger editor.

On a sync-only server (`SYNC_MODE=server` with app APIs hidden), the main middleware permits health, metrics, and `/api/sync/*` requests but returns 404 for `/docs`, `/redoc`, `/openapi.json`, and other app routes. Use the checked-in schema on a trusted local documentation host in that deployment.

## Authentication and request setup

| API group | Authentication | Additional requirement |
| --- | --- | --- |
| Public login, signup, health | None | Login and signup accept URL-encoded form data and return JSON when `Accept: application/json` is sent. |
| App `/api/*`, `/metrics`, `/` | `dr_transition_auth` session cookie from `POST /login` | Admin routes require an admin account; some also check sync permissions. |
| Server sync `/api/sync/*` except `/client/*` | `Authorization: Bearer <token>` **or** `X-Sync-Token: <token>` | Sync enabled and a registered client token with relevant permissions. |
| Local client `/api/sync/client/*` | Session cookie | Client sync configuration is needed for operations that contact the server. |
| Reranker and NLI services | No application-level auth in these services | Run on a trusted local/internal interface. |

Open `/login` in the same browser, sign in, then open `/docs`. The browser sends the HttpOnly session cookie automatically to the same origin; entering a cookie in Swagger's **Authorize** dialog cannot create that browser cookie. For a protected operation, expand it, click **Try it out**, fill the fields, click **Execute**, and inspect the response body, status, and headers.

Swagger UI sends `Accept: application/json` for `POST /login`, `POST /signup`, and `POST /logout`. Successful login and signup calls return JSON with user details and a `csrf_token`, plus the session and CSRF cookies. Browser form submissions still redirect to the app. The `GET /`, `GET /login`, and `GET /signup` pages return HTML; the report download endpoint returns PDF.

When CSRF protection is enabled, a cookie-authenticated `POST`, `PATCH`, or `DELETE` request needs the `dr_transition_csrf` cookie and a matching `X-CSRF-Token` header (or `csrf_token` form field), plus an allowed `Origin` or `Referer`. Swagger UI copies the readable CSRF cookie into that header automatically for same-origin unsafe requests. Scripts can use the `csrf_token` from the JSON login response. `/login` and `/signup` are exempt from this CSRF check. Bearer-token sync calls do not use a session cookie and therefore do not need the CSRF header. Responses include the configured request ID header for troubleshooting.

For a script, keep a cookie jar and send the same CSRF value in both places. Example PowerShell session on a local development app:

```powershell
$base = 'http://127.0.0.1:8000'
$web = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$login = Invoke-RestMethod "$base/login" -Method Post -Body @{email='you@example.org';password='YOUR_PASSWORD'} -Headers @{Accept='application/json'} -WebSession $web
$csrf = $login.csrf_token
$headers = @{ 'X-CSRF-Token' = $csrf; 'Origin' = $base }
Invoke-RestMethod "$base/api/sessions" -WebSession $web
Invoke-RestMethod "$base/api/chat" -Method Post -WebSession $web -Headers $headers -ContentType 'application/json' -Body '{"message":"Hello"}'
```

Use HTTPS and a host that matches the configured cookie settings outside local development. A JSON login failure returns `{"error":true,"detail":"..."}` with HTTP 400 or 429; later protected calls return 401 without a valid session. Password changes replace the session cookie and invalidate older sessions.

## Common API flows

### Chat and sessions

`POST /api/chat` accepts a JSON `ChatRequest` with `message`, optional `session_id`, `validation_mode` (default `strict`), and `crowd_sourcing_enabled` (default `false`). The response includes `session_id`, `step`, `bot_message`, `options`, `other_options`, and the current session summary. Send the returned `session_id` on subsequent messages. `POST /api/stats-deep-dive` uses the same JSON shape for follow-up analysis. `GET /api/sessions` lists your sessions, `GET /api/sessions/{session_key}` restores one, and `GET /api/sessions/{session_key}/report?scope=current` downloads a PDF. Valid report scopes are `current`, `user_hazard`, and `all_hazard`.

For evidence attachments, send `multipart/form-data` to `/api/chat` with `message`, `session_id`, and optionally `evidence_file`, `evidence_url`, `policy_reference_file`, or `policy_reference_url`. Reference file types are PDF, DOCX, MD, or TXT. A session ID is required for policy reference ingestion. `X-Ingestion-Progress-Id` can be supplied for ingestion operations and polled with `GET /api/knowledge/progress/{progress_id}`; progress is scoped to the signed-in user.

### Knowledge and prompt administration

An admin can call `POST /api/knowledge/upload` with multipart `files` (repeat the field for multiple files) or `file`; `POST /api/knowledge/url` with `{"url":"https://..."}` or `{"urls":["https://..."]}`; and `POST /api/knowledge/policy-document` with `policy_id` and exactly one of multipart `file` or `url`. Some installations also require the admin's sync client to have knowledge management permission. Search with `POST /api/knowledge/search` and `{"query":"..."}`. Many application errors return HTTP 200 with `{"error":true,"detail":"..."}`, so check the `error` field as well as the HTTP status.

Prompt administration uses `GET /api/prompts`, `POST /api/prompts` with `prompt_key` and `content`, `GET /api/prompts/{prompt_id}`, and `PATCH /api/prompts/{prompt_id}` with `content`. Prompt source can be read or set with `/api/settings/prompt-source`; values are `auto`, `db`, or `file`.

### Sync clients

Create a registered sync client token using the deployment procedure in [BACKEND_SYNC_DEPLOYMENT.md](BACKEND_SYNC_DEPLOYMENT.md). The token is sent only to the central server's `/api/sync/*` routes. A typical sequence is `GET /api/sync/status`, `POST /api/sync/pull`, then `POST /api/sync/push` with a bundle, or one `POST /api/sync/exchange` with a bundle to apply and pull in one call. Bundle tables and permissions vary by deployment; prefer the app's `SyncService` to construct bundles. `POST /api/sync/run` triggers the configured server exchange. The local client routes `/api/sync/client/status`, `/client/user-data`, `/client/run`, and `/client/system-inquiry-telemetry` instead use the signed-in browser session.

Example server token check:

```powershell
$token = 'SERVER_ISSUED_CLIENT_TOKEN'
Invoke-RestMethod 'https://your-sync-host.example/api/sync/status' -Headers @{Authorization="Bearer $token"}
```

### Grounding services

`POST http://127.0.0.1:8081/rerank` accepts `{"query":"...","documents":["text one","text two"]}` and returns scores in input order. `POST http://127.0.0.1:8082/entail` accepts `{"pairs":[{"premise":"...","hypothesis":"..."}]}` and returns a label and score for each pair. Both have `/health`. Model unavailability returns 503; malformed inputs return 422.

## Endpoint reference

The paths below are relative to the relevant service base URL. Open the matching OpenAPI document for field-level schemas and query parameter constraints. `Session` means the browser cookie; `Admin` means an admin session; `Token` means a registered sync token; `Public` means no app authentication.

### Main app: pages and operational endpoints

| Method and path | Access | Purpose / input / output |
| --- | --- | --- |
| `GET /` | Session | Application HTML; redirects to login if signed out and sets CSRF cookie after login. |
| `GET /login` | Public | Login HTML form. |
| `POST /login` | Public | Form `email`, `password`; JSON with `Accept: application/json`, otherwise browser redirect or HTML error. |
| `GET /signup` | Public | Signup HTML form. |
| `POST /signup` | Public | Form identity, organisation, password, confirmation; JSON with `Accept: application/json`, otherwise browser redirect or HTML error. |
| `POST /logout` | Session | Clears auth/CSRF cookies; JSON with `Accept: application/json`, otherwise redirects to login. |
| `GET /health` | Public | Basic service status. |
| `GET /health/live` | Public | Liveness status. |
| `GET /health/ready` | Public | Database readiness; 503 if unavailable. |
| `GET /metrics` | Admin | In-process metrics snapshot. |

### Main app: conversation, analytics, and sessions

| Method and path | Access | Purpose / input / output |
| --- | --- | --- |
| `POST /api/chat` | Session | Chat JSON or multipart evidence; returns `ChatResponse`. |
| `POST /api/stats-deep-dive` | Session | Stats follow-up using `ChatRequest`; returns `ChatResponse`. |
| `POST /api/auto-user-message` | Admin | Generate a suggested user message for a session; `ChatRequest`. |
| `GET /api/sessions` | Session | List the current user's saved sessions. |
| `GET /api/hazard-salience` | Session | Survey salience; optional `country`, `sector`, `region`, `hazard` query fields. |
| `GET /api/hazard-effect-size` | Session | Odds-ratio effect size; optional `sector`, `hazard`, `min_or`. |
| `GET /api/hazards/ranked` | Session | Ranked hazards; required `country_id`, `sector_id`, optional `region_id`. |
| `GET /api/sessions/{session_key}` | Session | Restore a session and its messages. |
| `GET /api/sessions/{session_key}/export` | Session | Export session and messages as JSON. |
| `GET /api/sessions/{session_key}/report` | Session | Download PDF; optional `scope` query field. |
| `POST /api/sessions/import` | Admin | Import exported JSON via multipart `file`; creates a new session. |
| `PATCH /api/sessions/{session_key}` | Session | Rename session with JSON `title`. |
| `PATCH /api/profile/password` | Session | Change password with `current_password`, `new_password`, `confirm_password`. |

### Main app: knowledge and prompts

| Method and path | Access | Purpose / input / output |
| --- | --- | --- |
| `GET /api/knowledge/progress/{progress_id}` | Session | Get an ingestion job's progress. |
| `GET /api/knowledge` | Session | List main and policy knowledge documents. |
| `POST /api/knowledge/upload` | Admin | Upload one or more PDF, DOCX, MD, TXT files. |
| `GET /api/knowledge/policies` | Admin | List policies available for document linking. |
| `POST /api/knowledge/policy-document` | Admin | Attach file or URL to a policy with `policy_id`. |
| `POST /api/knowledge/url` | Admin | Import one or more URLs. |
| `POST /api/knowledge/search` | Session | Search main knowledge with JSON `query`. |
| `DELETE /api/knowledge/{document_id}` | Admin | Delete a knowledge document. |
| `POST /api/sector-prompts/reindex` | Admin | Rebuild sector prompt search index. |
| `POST /api/sector-prompts/search` | Admin | Search sector prompts with `sector` and `query`. |
| `GET /api/settings/prompt-source` | Admin | Read active prompt source. |
| `PATCH /api/settings/prompt-source` | Admin | Set `prompt_source` to `auto`, `db`, or `file`. |
| `GET /api/prompts` | Admin | List prompt records. |
| `POST /api/prompts` | Admin | Create prompt with `prompt_key`, `content`, optional `display_name`. |
| `GET /api/prompts/{prompt_id}` | Admin | Read a prompt. |
| `PATCH /api/prompts/{prompt_id}` | Admin | Update prompt `content`. |

### Main app: sync and remote model APIs

| Method and path | Access | Purpose / input / output |
| --- | --- | --- |
| `GET /api/sync/status` | Token | Sync configuration, scopes, tables, and client permissions. |
| `POST /api/sync/pull` | Token | Export bundle; optional `request_user_data_sync` query boolean. |
| `POST /api/sync/push` | Token | Apply a sync bundle; returns inserted/updated/skipped counts. |
| `POST /api/sync/exchange` | Token | Apply bundle and return a fresh bundle. |
| `POST /api/sync/run` | Token | Trigger configured server exchange. |
| `POST /api/sync/llm/chat` | Token | Proxy Bedrock chat; JSON `context`, `messages`, optional format/temperature/token limit. |
| `POST /api/sync/llm/embedding` | Token | Proxy Bedrock embedding; JSON `text`. |
| `POST /api/sync/system-inquiry-telemetry` | Token | Accept telemetry batch in JSON `events`. |
| `POST /api/sync/prompts` | Token | Create prompt; token needs `can_manage_prompts`. |
| `PATCH /api/sync/prompts/{prompt_id}` | Token | Update prompt; token needs `can_manage_prompts`. |
| `GET /api/sync/client/status` | Session | Local client sync configuration and state. |
| `POST /api/sync/client/user-data` | Session | Toggle local user-data sync with JSON `enabled`. |
| `POST /api/sync/client/run` | Session | Trigger a client exchange with the server. |
| `POST /api/sync/client/system-inquiry-telemetry` | Session | Push queued telemetry from the client. |

### Optional grounding services

| Service | Method and path | Purpose |
| --- | --- | --- |
| Reranker | `GET /health` | Service and model status. |
| Reranker | `POST /rerank` | Rank 1–100 documents against a query; returns `scores`. |
| NLI | `GET /health` | Service and model status. |
| NLI | `POST /entail` | Classify 1–100 premise/hypothesis pairs; returns `results`. |

## Errors and limits

FastAPI validation errors use HTTP 422 with `detail`. Authentication failures use 401; admin or feature-permission failures use 403. CSRF failures use 403 with `{"error":true,"detail":"Invalid CSRF token or request origin."}`. Missing resources may be 404 or an HTTP 200 body with `error: true`, depending on the handler. Login/signup rate limiting can return 429. Oversized manually parsed JSON or upload requests can return 413. Read the specific operation's response and the `error` field before treating HTTP 200 as success.

Swagger shows the structural request schema; deployment settings such as maximum upload size, sync permissions, model availability, and allowed origins still control whether a request succeeds.

# Dr Transition Architecture Boundaries

This document freezes the target ownership boundaries for the hosted backend and
Windows desktop client. It is the implementation reference for the server/client
split migration.

Deployment and bundle-release steps are documented in
[`DEPLOYMENT.md`](DEPLOYMENT.md).

## Hosted Server Owns

The hosted server is the authoritative persistence and access-control layer.

- Authentication and authorization.
- User accounts, profile data, and password/session security.
- MySQL-backed persistent sessions and saved workflow state.
- Admin-managed main knowledge base records.
- Validated evidence records after client-side validation succeeds.
- Knowledge-base sync APIs for client caches.
- Audit logging for administrative and evidence-promotion actions.
- Health, readiness, metrics, and server operations endpoints.

The hosted server must not own temporary draft evidence. It must not require a
local Windows machine dependency such as Ollama, client embeddings, or client
vector indexes to serve persistence APIs.

## Windows App Owns

The Windows desktop app is the local intelligence and user-experience layer.

- Frontend application shell and interactive workflow UI.
- Local LLM calls on the user's machine.
- Local embedding calls on the user's machine.
- Local RAG retrieval and prompt construction.
- Local vector/search indexes for synced knowledge bases.
- Client-only temporary evidence for the active session.
- Client-side validation workflows before evidence promotion.
- Sync cursor/cache state for downloaded knowledge scopes.
- Local runtime diagnostics for hosted backend reachability and local model
  readiness.

Temporary evidence remains local to the client until a validation workflow
explicitly promotes it.

## Knowledge-Base Scope Ownership

| Scope | Source of Truth | Client Behavior | Server Behavior |
| --- | --- | --- | --- |
| `main` | Hosted server / MySQL | Sync, cache, embed, index, search locally | Store admin-managed documents and chunks |
| `sector_prompt` | Hosted server or bundled static client assets | Sync or load locally, embed, index, search locally | Provide authoritative prompt/reference content when hosted |
| `validated_evidence` | Hosted server / MySQL | Sync authorized records, embed, index, search locally | Store only evidence promoted after validation |
| `temporary` | Windows app only | Store per session, embed, index, search, clear locally | Never store or sync before validation |
| `quarantined` | Optional server/admin workflow only | Use only if an explicit admin/debug workflow needs it | Avoid by default |

## Temporary Evidence Rule

Temporary evidence never reaches the hosted server before validation.

Client flow:

1. User provides an evidence file or URL.
2. Client extracts text locally where possible.
3. Client chunks and embeds the evidence locally.
4. Client stores it in a session-local temporary knowledge store.
5. Client uses it for local RAG and validation.
6. Client deletes it on reset, discard, or session cleanup.
7. If validation succeeds, client sends a promoted evidence package to the
   hosted server as `validated_evidence`.

Server flow:

1. Reject or ignore attempts to sync `temporary` scope.
2. Store only promoted `validated_evidence`.
3. Record audit events for promotion.

## Client Knowledge Store

The Windows app stores client knowledge under:

```text
%LOCALAPPDATA%\DrTransition\knowledge
```

Persistent synced scopes use one directory per scope:

```text
main/
sector_prompt/
validated_evidence/
```

Each scope keeps:

- `manifest.json`: sync cursor, scope checksum, document checksums, tombstones.
- `documents/*.json`: document metadata, chunks, and optional local embeddings.

Temporary evidence is isolated by session:

```text
temporary/{session_id}/
```

Temporary manifests are local lifecycle state only. They are never server sync
state and must be cleared on reset, discard, or local session cleanup.

The frontend uses `window.DrTransitionClientKB`. In the Windows app it delegates
to Tauri commands backed by local files. In a regular browser it falls back to
IndexedDB with the same scope rules.

## Client Knowledge Sync

The frontend sync manager is exposed as `window.DrTransitionKBSync`.

Sync behavior:

- Fetch hosted manifests from `/api/knowledge/sync/manifest`.
- Remove local documents that are no longer present in the hosted manifest.
- Compare hosted document checksums with local manifest checksums.
- Fetch changed/new documents and chunks from `/api/knowledge/sync`.
- Generate chunk embeddings locally through Ollama.
- Store embedded chunks in the client knowledge store.
- Rebuild per-scope local indexes for `main`, `sector_prompt`, and
  `validated_evidence`.
- Run once when the authenticated app starts.
- Run incremental sync periodically while the app remains open.

The hosted manifest and sync APIs never expose or accept the `temporary` scope.

## Client Local LLM/RAG Layer

The frontend local inference layer is exposed as `window.DrTransitionLocalRAG`.

It owns:

- Local Ollama chat calls.
- Local Ollama embedding calls.
- Model availability checks for chat and embedding models.
- Retrieval across selected scopes, including session-local `temporary`
  evidence when a session id is supplied.
- Prompt construction with scoped source snippets.
- Citation metadata and bracketed citation ids such as `[S1]`.
- Best-effort JSON/structured response parsing.
- User-facing errors when Ollama is offline or a required model is missing.

This layer is the integration point for later workflow migration. Hosted server
chat endpoints must remain persistence/state APIs and must not perform local LLM
or RAG work.

## Client Workflow Controller

The frontend workflow migration layer is exposed as
`window.DrTransitionWorkflows`.

It routes these workflows through the local LLM/RAG layer after the hosted server
persists state:

- Intro/help and voice-summary text.
- Open conversation selection assistance.
- Grounded questions.
- Hazard validation.
- Socio-demographic validation.
- Mitigation validation.
- Evaluation validation.
- Stats deep-dive.
- Auto-user test message generation.

The client controller returns the same response shape consumed by the existing
UI: `bot_message`, `voice_summary`, `step`, `input_mode`, `options`, `session`,
and optional `validation_details`. The hosted server remains responsible only
for authentication and persistence/state updates during these turns.

Frontend API behavior:

- User workflow input is persisted through `/api/sessions/state`.
- The frontend does not call `/api/chat`, `/api/stats-deep-dive`, or
  `/api/auto-user-message` for LLM work.
- Local workflow results are persisted back through `/api/sessions/state` as
  compact final state, message, and `workflow_result` metadata only.
- Hosted API calls use the shared frontend API wrapper so desktop and browser
  sessions send cookies with `credentials: include` and may also send a local
  bearer token when configured.
- Knowledge sync and validated-evidence promotion use the same hosted API
  wrapper.

## Client Temporary Evidence Handling

The frontend evidence module is exposed as `window.DrTransitionEvidence`.

Temporary evidence behavior:

- Evidence files and URLs are processed before the chat persistence call.
- Chat requests remain JSON-only and do not upload temporary evidence files or
  URLs to the hosted server.
- Uploaded files are parsed locally when the browser can read them directly.
  Supported local formats are PDF, DOCX, TXT, Markdown, CSV, JSON, and HTML.
- Evidence URLs are fetched locally when browser CORS rules allow it.
- Extracted text is chunked locally.
- Chunk embeddings are generated locally through Ollama.
- Embedded chunks are stored in the session-local `temporary` scope.
- A session-local temporary index is rebuilt after evidence changes.
- Temporary evidence is cleared on reset and logout.

PDF and DOCX are parsed by the client evidence module. If a specific PDF uses
image-only scans or unsupported encoding, extraction may fail locally, but the
file must still not be sent to the server as temporary evidence.

## Validated Evidence Promotion

Validated evidence promotion is the only path from client-local temporary
evidence to the hosted server.

Promotion flow:

1. Client validates temporary evidence through local RAG/LLM workflows.
2. Client promotes only when the local structured validation result is accepted.
3. Client sends a compact evidence package to
   `/api/validated-evidence/promote` with title, source metadata, validated
   chunks, session key, scope metadata, and validation summary.
4. Hosted server stores the package as `validated_evidence`.
5. Hosted server records a `validated_evidence.promote` audit event.
6. Hosted server returns the stored document id, chunk ids, and document
   version.
7. Client clears the promoted temporary documents locally.
8. Client syncs the `validated_evidence` scope so future retrieval uses the
   authoritative server copy.

Rejected or incomplete validations do not upload temporary evidence.

## Windows Installer Boundary

The Windows installer bundles only the desktop client and local runtime assets.

Bundle:

- Tauri desktop app.
- Frontend HTML, CSS, JavaScript, images, and static UI assets.
- Client-side LLM/RAG/evidence/sync modules.
- Static prompt/reference assets required locally.
- Runtime config template for hosted backend URL and local Ollama settings.
- Diagnostics for hosted backend reachability, local Ollama reachability, and
  required local models.

Do not bundle:

- FastAPI backend executable.
- Backend API service.
- MySQL server.
- `schema.sql`.
- Database migrations.
- Seed/import scripts.
- Production `.env` or secrets.
- MySQL credentials.
- User temporary evidence.
- User-generated local embeddings or indexes.

## Migration Constraint

All following phases must preserve this boundary:

- Server APIs persist and sync authoritative data.
- Client workflows perform local inference and local retrieval.
- Temporary evidence is client-only.
- Windows packaging remains a desktop-client package, not a bundled server
  package.

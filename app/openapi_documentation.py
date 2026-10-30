"""OpenAPI augmentation for manually parsed app routes."""

from __future__ import annotations

METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def obj(properties: dict, required: list[str] | None = None, description: str = "") -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema


def string(description: str = "", **kwargs) -> dict:
    return {"type": "string", **({"description": description} if description else {}), **kwargs}


def body(schema: dict, media_type: str = "application/json", description: str = "") -> dict:
    return {"content": {media_type: {"schema": schema}}, **({"description": description} if description else {})}


FILE = {"type": "string", "format": "binary"}
CHAT = {"$ref": "#/components/schemas/ChatRequest"}
SYNC_BUNDLE = obj(
    {"format": string(enum=["dr-transition-sync-v1"]),
     "sync_protocol_version": {"type": "integer"},
     "client_schema_version": {"type": "integer"},
     "device_id": string(), "exported_at": string(format="date-time"),
     "admin_knowledge_sync": {"type": "boolean"}, "admin_user_email": string(),
     "request_user_data_sync": {"type": "boolean"},
     "tables": {"type": "array", "items": obj({"name": string(), "rows": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}, ["name", "rows"])}},
    ["format", "tables"],
    "Sync bundle produced by /api/sync/pull or the client SyncService. Table row fields and metadata are version dependent.",
)

MANUAL_BODIES = {
    ("post", "/login"): body(obj({"email": string(format="email"), "password": string(format="password")}, ["email", "password"]), "application/x-www-form-urlencoded"),
    ("post", "/signup"): body(obj({key: string() for key in ("email", "name", "designation", "organisation_type", "organisation_name", "password", "confirm_password")}, ["email", "name", "designation", "organisation_type", "organisation_name", "password", "confirm_password"]), "application/x-www-form-urlencoded"),
    ("post", "/api/chat"): body(CHAT, description="Send JSON for ordinary chat, or multipart/form-data to attach evidence and policy references. The multipart alternative is described in the API usage guide."),
    ("post", "/api/stats-deep-dive"): body(CHAT),
    ("post", "/api/auto-user-message"): body(CHAT),
    ("post", "/api/sessions/import"): body(obj({"file": FILE}, ["file"]), "multipart/form-data"),
    ("patch", "/api/sessions/{session_key}"): body(obj({"title": string(maxLength=220)}, ["title"])),
    ("patch", "/api/profile/password"): body(obj({key: string(format="password") for key in ("current_password", "new_password", "confirm_password")}, ["current_password", "new_password", "confirm_password"])),
    ("post", "/api/knowledge/upload"): body(obj({"files": {"type": "array", "items": FILE}, "file": FILE}, description="Use files for multiple uploads or file for one upload; PDF, DOCX, MD, TXT."), "multipart/form-data"),
    ("post", "/api/knowledge/policy-document"): body(obj({"policy_id": string(), "file": FILE, "url": string(format="uri")}, ["policy_id"], "Supply exactly one of file or url."), "multipart/form-data"),
    ("post", "/api/knowledge/url"): body(obj({"url": string(format="uri"), "urls": {"type": "array", "items": string(format="uri")}, "title": string()}, description="Supply url or urls.")),
    ("post", "/api/knowledge/search"): body(obj({"query": string()}, ["query"])),
    ("post", "/api/sector-prompts/search"): body(obj({"sector": string(), "query": string()}, ["query"])),
    ("patch", "/api/settings/prompt-source"): body(obj({"prompt_source": string(enum=["auto", "db", "file"])}, ["prompt_source"])),
    ("post", "/api/prompts"): body(obj({"prompt_key": string(), "content": string(), "display_name": string()}, ["prompt_key", "content"])),
    ("patch", "/api/prompts/{prompt_id}"): body(obj({"content": string()}, ["content"])),
    ("post", "/api/sync/push"): body(SYNC_BUNDLE),
    ("post", "/api/sync/exchange"): body(SYNC_BUNDLE),
    ("post", "/api/sync/llm/chat"): body(obj({"context": string(), "messages": {"type": "array", "minItems": 1, "items": obj({"role": string(enum=["user", "assistant"]), "content": string()}, ["role", "content"])}, "response_format": {"oneOf": [string(), {"type": "object"}, {"type": "null"}]}, "temperature": {"type": "number", "minimum": 0, "maximum": 1}, "max_tokens": {"type": "integer", "minimum": 1, "maximum": 8192}}, ["context", "messages"])),
    ("post", "/api/sync/llm/embedding"): body(obj({"text": string(minLength=1, maxLength=50000)}, ["text"])),
    ("post", "/api/sync/system-inquiry-telemetry"): body(obj({"events": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}, ["events"])),
    ("post", "/api/sync/client/user-data"): body(obj({"enabled": {"type": "boolean"}}, ["enabled"])),
    ("post", "/api/sync/prompts"): body(obj({"prompt_key": string(), "content": string(), "display_name": string()}, ["prompt_key", "content"])),
    ("patch", "/api/sync/prompts/{prompt_id}"): body(obj({"content": string()}, ["content"])),
}

PUBLIC = {("get", p) for p in ("/login", "/signup", "/health", "/health/live", "/health/ready")}
PUBLIC.update({("post", "/login"), ("post", "/signup")})
PUBLIC.add(("post", "/logout"))
SYNC_COOKIE = {"/api/sync/client/status", "/api/sync/client/user-data", "/api/sync/client/run", "/api/sync/client/system-inquiry-telemetry"}
ADMIN = {"/metrics", "/api/auto-user-message", "/api/sessions/import", "/api/knowledge/upload", "/api/knowledge/policies", "/api/knowledge/policy-document", "/api/knowledge/url", "/api/sector-prompts/reindex", "/api/sector-prompts/search", "/api/settings/prompt-source", "/api/prompts", "/api/prompts/{prompt_id}", "/api/knowledge/{document_id}"}


def enhance_main(spec: dict) -> dict:
    spec["info"]["description"] = "Dr Transition application and sync API. See docs/API_USAGE_GUIDE.md for authentication, CSRF, deployment modes, examples, and endpoint behavior."
    schemes = spec.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["sessionCookie"] = {"type": "apiKey", "in": "cookie", "name": "dr_transition_auth", "description": "Session cookie set by POST /login."}
    schemes["syncBearer"] = {"type": "http", "scheme": "bearer", "description": "Server-issued sync client token."}
    schemes["syncHeader"] = {"type": "apiKey", "in": "header", "name": "X-Sync-Token", "description": "Alternative to Authorization: Bearer for sync clients."}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in METHODS:
                continue
            key = method, path
            if key in MANUAL_BODIES:
                operation["requestBody"] = MANUAL_BODIES[key]
            if key in PUBLIC:
                operation["security"] = []
            elif path.startswith("/api/sync/") and path not in SYNC_COOKIE:
                operation["security"] = [{"syncBearer": []}, {"syncHeader": []}]
            else:
                operation["security"] = [{"sessionCookie": []}]
            if path in ADMIN:
                operation["description"] = (operation.get("description", "") + "\n\nRequires an administrator session; some operations also require sync client permissions.").strip()
            if path.startswith("/api/sync/") and path not in SYNC_COOKIE:
                operation["description"] = (operation.get("description", "") + "\n\nRequires sync to be enabled and a registered client token.").strip()
            if method in {"post", "patch", "put", "delete"} and key not in {( "post", "/login"), ("post", "/signup")}:
                operation.setdefault("description", "")
                operation["description"] += "\n\nCookie-authenticated requests need the dr_transition_csrf cookie, matching X-CSRF-Token header, and an allowed Origin or Referer when CSRF protection is enabled."
            if key not in PUBLIC:
                operation.setdefault("responses", {}).setdefault("401", {"description": "Authentication or sync token required/invalid."})
            if path in ADMIN:
                operation["responses"].setdefault("403", {"description": "Administrator or feature permission required."})
            if key in MANUAL_BODIES:
                operation["responses"].setdefault("413", {"description": "Configured request or upload size limit exceeded."})
    # FastAPI has no way to describe these manually parsed alternative media types automatically.
    chat = spec["paths"]["/api/chat"]["post"]["requestBody"]
    chat["content"]["multipart/form-data"] = {"schema": obj({
        "message": string(), "session_id": string(), "validation_mode": string(),
        "crowd_sourcing_enabled": {"type": "boolean"}, "policy_reference_url": string(format="uri"),
        "policy_reference_file": FILE, "evidence_url": string(format="uri"), "evidence_file": FILE,
    })}
    spec["paths"]["/api/sessions/{session_key}/report"]["get"]["responses"]["200"]["content"] = {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}
    auth_success = obj({
        "error": {"type": "boolean", "const": False},
        "detail": string(),
        "user": obj({"id": string(), "email": string(format="email"), "name": string(), "role": string()}, ["id", "email", "name", "role"]),
        "csrf_token": {"oneOf": [string(), {"type": "null"}]},
    }, ["error", "detail", "user", "csrf_token"])
    auth_error = obj({"error": {"type": "boolean", "const": True}, "detail": string()}, ["error", "detail"])
    for path in ("/login", "/signup"):
        operation = spec["paths"][path]["post"]
        operation["description"] = "Send form data with Accept: application/json for a JSON response and session/CSRF cookies. Browser form submissions receive a 303 redirect."
        operation["responses"]["200"] = {"description": "Authenticated JSON response.", "content": {"application/json": {"schema": auth_success}}}
        operation["responses"]["303"] = {"description": "Browser form submission redirects to the app."}
        for code, description in (("400", "Invalid or incomplete form."), ("429", "Too many attempts.")):
            operation["responses"][code] = {"description": description, "content": {"application/json": {"schema": auth_error}}}
    logout = spec["paths"]["/logout"]["post"]
    logout["description"] = "Send Accept: application/json for JSON confirmation. Browser form submissions redirect to /login. Cookie-authenticated requests still need CSRF protection."
    logout["responses"]["200"] = {"description": "Logged out.", "content": {"application/json": {"schema": obj({"error": {"type": "boolean", "const": False}, "detail": string()}, ["error", "detail"])}}}
    logout["responses"]["303"] = {"description": "Browser form submission redirects to /login."}
    return spec




const assert = require("node:assert/strict");
const { randomUUID, webcrypto } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");

function storage() {
  const data = new Map();
  return {
    getItem: (key) => data.get(key) || null,
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

function scriptContext(extra = {}) {
  const context = {
    console,
    crypto: {
      subtle: webcrypto.subtle,
      randomUUID,
    },
    TextDecoder,
    TextEncoder,
    URLSearchParams,
    File: class File {},
    localStorage: storage(),
    setInterval,
    clearInterval,
    setTimeout,
    clearTimeout,
    ...extra,
  };
  context.window = context;
  return vm.createContext(context);
}

function loadClientScript(context, relativePath) {
  const code = fs.readFileSync(path.join(root, relativePath), "utf8");
  vm.runInContext(code, context, { filename: relativePath });
}

test("client KB store enforces scope/session boundaries and delegates to Tauri store", async () => {
  const invoked = [];
  const context = scriptContext({
    __TAURI__: {
      core: {
        invoke: async (command, args) => {
          invoked.push({ command, args });
          return { command, args };
        },
      },
    },
  });
  loadClientScript(context, "app/static/js/client-kb-store.js");

  await assert.rejects(
    () => context.DrTransitionClientKB.listDocuments("temporary"),
    /Temporary knowledge requires a session id/
  );
  await assert.rejects(
    () => context.DrTransitionClientKB.upsertDocuments({ scope: "main", session_id: "not-allowed", documents: [] }),
    /Session ids are only valid/
  );

  await context.DrTransitionClientKB.upsertDocuments({
    scope: "main",
    documents: [{ id: "doc-1", title: "Main" }],
  });
  await context.DrTransitionClientKB.clearTemporary("session-1");

  assert.equal(invoked[0].command, "kb_upsert_documents");
  assert.equal(invoked[0].args.batch.scope, "main");
  assert.equal(invoked[1].command, "kb_clear_temporary");
  assert.equal(invoked[1].args.sessionId, "session-1");
});

test("client KB sync downloads hosted documents, embeds locally, stores, and indexes them", async () => {
  const documentsByScope = new Map();
  const upserts = [];
  const context = scriptContext({
    __TAURI__: {
      core: {
        invoke: async (command, args) => {
          if (command === "runtime_config") {
            return { ollama: { baseUrl: "http://127.0.0.1:11434", embeddingModel: "nomic-embed-text" } };
          }
          if (command === "ollama_embed_texts") {
            return args.texts.map((_, index) => [1, index + 0.5]);
          }
          throw new Error(`unexpected invoke ${command}`);
        },
      },
    },
    fetch: async (url) => {
      if (String(url).startsWith("/api/knowledge/sync/manifest")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            error: false,
            scope: "main",
            cursor: 1,
            checksum: "main:1:1",
            documents: [{ id: 1, checksum: "remote-checksum" }],
          }),
        };
      }
      if (String(url).startsWith("/api/knowledge/sync?")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            error: false,
            scope: "main",
            next_cursor: 1,
            has_more: false,
            deleted_document_ids: [],
            documents: [
              {
                id: 1,
                title: "Hosted policy",
                source_uri: "policy.txt",
                source_type: "txt",
                scope: "main",
                chunks: [{ id: 10, chunk_index: 0, content: "Hosted policy chunk" }],
              },
            ],
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  });
  context.DrTransitionAPI = { apiFetch: context.fetch };
  context.DrTransitionClientKB = {
    manifest: async () => ({ sync_cursor: null, document_checksums: {}, tombstones: {} }),
    listDocuments: async (scope) => documentsByScope.get(scope) || [],
    deleteDocuments: async () => ({ deleted: true }),
    upsertDocuments: async (batch) => {
      upserts.push(batch);
      const existing = documentsByScope.get(batch.scope) || [];
      documentsByScope.set(batch.scope, [
        ...existing.filter((item) => !(batch.documents || []).some((doc) => String(doc.id) === String(item.id))),
        ...(batch.documents || []),
      ]);
      return { ok: true };
    },
  };
  loadClientScript(context, "app/static/js/client-kb-sync.js");

  const result = await context.DrTransitionKBSync.syncScope("main");

  assert.equal(result.downloaded, 1);
  assert.equal(upserts.some((batch) => batch.documents?.[0]?.chunks?.[0]?.embedding?.length === 2), true);
  assert.equal(context.DrTransitionKBSync.index("main").chunk_count, 1);
});

test("client evidence lifecycle stores temporary evidence locally, promotes only package text, clears temp, and syncs validated evidence", async () => {
  const documentsBySession = new Map();
  const deleted = [];
  const promotedBodies = [];
  let syncedScope = "";
  const context = scriptContext({
    __TAURI__: {
      core: {
        invoke: async (command, args) => {
          if (command === "runtime_config") {
            return { ollama: { baseUrl: "http://127.0.0.1:11434", embeddingModel: "nomic-embed-text" } };
          }
          if (command === "ollama_embed_texts") {
            return args.texts.map(() => [0.1, 0.2, 0.3]);
          }
          throw new Error(`unexpected invoke ${command}`);
        },
      },
    },
    fetch: async (url) => {
      assert.equal(url, "https://example.test/evidence.txt");
      return {
        ok: true,
        status: 200,
        headers: { get: () => "text/plain" },
        text: async () => "Temporary evidence text that is parsed and embedded locally.",
      };
    },
  });
  context.DrTransitionClientKB = {
    upsertDocuments: async ({ session_id, documents }) => {
      documentsBySession.set(session_id, documents);
      return { ok: true };
    },
    listDocuments: async (_scope, sessionId) => documentsBySession.get(sessionId) || [],
    deleteDocuments: async ({ session_id, document_ids }) => {
      deleted.push(...document_ids);
      const remaining = (documentsBySession.get(session_id) || []).filter(
        (document) => !document_ids.includes(document.id)
      );
      documentsBySession.set(session_id, remaining);
      return { ok: true };
    },
    clearTemporary: async (sessionId) => {
      documentsBySession.delete(sessionId);
    },
  };
  context.DrTransitionAPI = {
    csrfFetch: async (url, options) => {
      assert.equal(url, "/api/validated-evidence/promote");
      const body = JSON.parse(options.body);
      promotedBodies.push(body);
      assert.equal(body.chunks[0].content.includes("Temporary evidence text"), true);
      assert.equal("embedding" in body.chunks[0], false);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          error: false,
          document: { id: 77, chunks: [{ id: 88, document_id: 77 }] },
          chunks: body.chunks.length,
          version: "2026-07-15T00:00:00",
        }),
      };
    },
  };
  context.DrTransitionKBSync = {
    syncScope: async (scope) => {
      syncedScope = scope;
      return { scope };
    },
  };
  loadClientScript(context, "app/static/js/client-evidence.js");

  const stored = await context.DrTransitionEvidence.storeTemporaryEvidence({
    sessionId: "session-1",
    evidenceUrl: "https://example.test/evidence.txt",
    context: "hazard_validation",
  });
  const promoted = await context.DrTransitionEvidence.promoteValidatedEvidence({
    sessionId: "session-1",
    validation: { status: "ok", decision: { label: "accepted" }, summary: "accepted locally" },
    context: { session: { country_id: 1, sector_id: 2 } },
  });

  assert.equal(stored.documents.length, 1);
  assert.equal(stored.documents[0].metadata.temporary, true);
  assert.equal(promoted.promoted_documents[0].document_id, 77);
  assert.equal(promotedBodies.length, 1);
  assert.equal(deleted.length, 1);
  assert.equal(syncedScope, "validated_evidence");
});

test("local RAG reports LLM unavailable before retrieval or chat proceeds", async () => {
  const context = scriptContext({
    __TAURI__: {
      core: {
        invoke: async (command) => {
          if (command === "ollama_model_status") {
            return {
              ollamaReachable: false,
              chatModelInstalled: false,
              embeddingModelInstalled: false,
              chatModel: "mistral-nemo",
              embeddingModel: "nomic-embed-text",
            };
          }
          throw new Error(`unexpected invoke ${command}`);
        },
      },
    },
  });
  loadClientScript(context, "app/static/js/client-llm-rag.js");

  await assert.rejects(
    () => context.DrTransitionLocalRAG.ask("Can I continue?", { scopes: ["main"] }),
    /Ollama is not reachable/
  );
});

test("mocked desktop flow stores only promoted evidence on the hosted server", async () => {
  const server = {
    sessions: [],
    promoted: [],
  };
  const documentsBySession = new Map();
  const context = scriptContext({
    __TAURI__: {
      core: {
        invoke: async (command, args) => {
          if (command === "runtime_config") {
            return { ollama: { baseUrl: "http://127.0.0.1:11434", embeddingModel: "nomic-embed-text" } };
          }
          if (command === "ollama_embed_texts") {
            return args.texts.map(() => [0.2, 0.4]);
          }
          throw new Error(`unexpected invoke ${command}`);
        },
      },
    },
    fetch: async (url) => {
      if (String(url).startsWith("/api/knowledge/sync/manifest")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ error: false, scope: "main", cursor: 0, checksum: "main:0:0", documents: [] }),
        };
      }
      if (String(url).startsWith("/api/knowledge/sync?")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ error: false, scope: "main", next_cursor: 0, has_more: false, documents: [], deleted_document_ids: [] }),
        };
      }
      if (url === "https://example.test/local-only.txt") {
        return {
          ok: true,
          status: 200,
          headers: { get: () => "text/plain" },
          text: async () => "Locally validated evidence.",
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  });
  context.DrTransitionAPI = {
    apiFetch: context.fetch,
    csrfFetch: async (url, options) => {
      if (url === "/api/sessions/state") {
        server.sessions.push(JSON.parse(options.body));
        return { ok: true, status: 200, json: async () => ({ error: false, session_id: "session-1" }) };
      }
      if (url === "/api/validated-evidence/promote") {
        const body = JSON.parse(options.body);
        server.promoted.push(body);
        return {
          ok: true,
          status: 200,
          json: async () => ({ error: false, document: { id: 7, chunks: [{ id: 8, document_id: 7 }] }, chunks: body.chunks.length, version: "v1" }),
        };
      }
      throw new Error(`unexpected csrf fetch ${url}`);
    },
  };
  context.csrfFetch = context.DrTransitionAPI.csrfFetch;
  context.DrTransitionClientKB = {
    manifest: async () => ({ sync_cursor: null, document_checksums: {}, tombstones: {} }),
    listDocuments: async (scope, sessionId) => {
      if (scope === "temporary") return documentsBySession.get(sessionId) || [];
      return [];
    },
    upsertDocuments: async ({ scope, session_id, documents }) => {
      if (scope === "temporary") documentsBySession.set(session_id, documents);
      return { ok: true };
    },
    deleteDocuments: async ({ session_id, document_ids }) => {
      documentsBySession.set(
        session_id,
        (documentsBySession.get(session_id) || []).filter((document) => !document_ids.includes(document.id))
      );
      return { ok: true };
    },
  };
  loadClientScript(context, "app/static/js/client-kb-sync.js");
  loadClientScript(context, "app/static/js/client-evidence.js");

  await context.DrTransitionKBSync.syncScope("main");
  await context.DrTransitionEvidence.storeTemporaryEvidence({
    sessionId: "session-1",
    evidenceUrl: "https://example.test/local-only.txt",
    context: "hazard_validation",
  });
  await context.DrTransitionAPI.csrfFetch("/api/sessions/state", {
    method: "POST",
    body: JSON.stringify({ session_id: "session-1", messages: [{ role: "user", content: "Reason" }] }),
  });
  await context.DrTransitionEvidence.promoteValidatedEvidence({
    sessionId: "session-1",
    validation: { status: "ok", decision: { label: "accepted" }, summary: "accepted locally" },
  });

  assert.equal(server.sessions.length, 1);
  assert.equal(server.promoted.length, 1);
  assert.equal(server.promoted[0].chunks[0].content, "Locally validated evidence.");
  assert.equal(documentsBySession.get("session-1").length, 0);
});

(function () {
  "use strict";

  const SYNC_SCOPES = ["main", "sector_prompt", "validated_evidence"];
  const DEFAULT_LIMIT = 200;
  const DEFAULT_INTERVAL_MS = 15 * 60 * 1000;
  const EMBED_BATCH_SIZE = 8;
  const indexByScope = new Map();
  let syncTimer = null;
  let syncInFlight = false;
  let lastStatus = {
    running: false,
    lastStartedAt: null,
    lastCompletedAt: null,
    scopes: {},
    error: null
  };

  function invoke() {
    return window.__TAURI__?.core?.invoke || null;
  }

  function isLocalBrowserOrigin() {
    const hostname = window.location?.hostname || "";
    return ["localhost", "127.0.0.1", "::1", ""].includes(hostname);
  }

  function assertSupportedRuntime() {
    if (invoke() || isLocalBrowserOrigin()) {
      return;
    }
    throw new Error(
      "Knowledge sync embeddings require the Windows desktop app. Open Dr Transition from the Windows app/installer."
    );
  }

  function clientStore() {
    if (!window.DrTransitionClientKB) {
      throw new Error("Client knowledge store is not available.");
    }
    return window.DrTransitionClientKB;
  }

  function chunkText(chunk) {
    return String(chunk?.content || chunk?.text || "").trim();
  }

  async function sha256(value) {
    const bytes = new TextEncoder().encode(String(value || ""));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  async function runtimeConfig() {
    const tauriInvoke = invoke();
    if (tauriInvoke) {
      try {
        return await tauriInvoke("runtime_config");
      } catch (error) {
        console.warn("Could not read Tauri runtime config", error);
      }
    }
    assertSupportedRuntime();
    return {
      ollama: {
        baseUrl: localStorage.getItem("dr_transition_ollama_base_url") || "http://127.0.0.1:11434",
        embeddingModel: localStorage.getItem("dr_transition_ollama_embedding_model") || "nomic-embed-text"
      }
    };
  }

  async function embedTexts(texts) {
    const cleanTexts = texts.map((text) => String(text || "").trim());
    const tauriInvoke = invoke();
    if (tauriInvoke) {
      return tauriInvoke("ollama_embed_texts", { texts: cleanTexts });
    }

    assertSupportedRuntime();
    const config = await runtimeConfig();
    const baseUrl = config.ollama?.baseUrl || "http://127.0.0.1:11434";
    const model = config.ollama?.embeddingModel || "nomic-embed-text";
    const embeddings = [];
    for (const text of cleanTexts) {
      const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/embeddings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, prompt: text })
      });
      if (!response.ok) {
        throw new Error(`Ollama embedding failed with status ${response.status}`);
      }
      const data = await response.json();
      embeddings.push(data.embedding || []);
    }
    return embeddings;
  }

  async function normalizeChunk(chunk, document, index) {
    const text = chunkText(chunk);
    return {
      id: String(chunk.id || `${document.id}:chunk:${index}`),
      text,
      page: Number.isFinite(Number(chunk.page_number)) ? Number(chunk.page_number) : null,
      source_ref: chunk.source_uri || document.source_uri || null,
      checksum: await sha256([
        chunk.id,
        chunk.chunk_index,
        text,
        chunk.source_uri,
        chunk.page_number
      ].join("\n")),
      embedding: null,
      metadata: {
        document_id: chunk.document_id ?? document.id,
        chunk_index: chunk.chunk_index ?? index,
        source_type: chunk.source_type || document.source_type || null,
        scope_level: chunk.scope_level || document.scope_level || null,
        country_id: chunk.country_id || document.country_id || null,
        region_id: chunk.region_id || document.region_id || null,
        sector_id: chunk.sector_id || document.sector_id || null,
        created_at: chunk.created_at || null
      }
    };
  }

  async function normalizeDocument(document) {
    const chunks = [];
    for (const [index, chunk] of (document.chunks || []).entries()) {
      const normalized = await normalizeChunk(chunk, document, index);
      if (normalized.text) {
        chunks.push(normalized);
      }
    }
    const checksum = document.checksum || await sha256(JSON.stringify({
      id: document.id,
      title: document.title,
      source_uri: document.source_uri,
      source_type: document.source_type,
      chunks: chunks.map((chunk) => chunk.checksum)
    }));
    return {
      id: String(document.id),
      title: document.title || null,
      source_uri: document.source_uri || null,
      source_type: document.source_type || null,
      checksum,
      version: document.created_at || null,
      updated_at: document.created_at || null,
      metadata: {
        scope: document.scope || null,
        scope_level: document.scope_level || null,
        session_key: document.session_key || null,
        country_id: document.country_id || null,
        region_id: document.region_id || null,
        sector_id: document.sector_id || null,
        created_at: document.created_at || null
      },
      chunks
    };
  }

  async function embedDocument(document) {
    const pending = document.chunks.filter((chunk) => chunk.text && !Array.isArray(chunk.embedding));
    for (let start = 0; start < pending.length; start += EMBED_BATCH_SIZE) {
      const batch = pending.slice(start, start + EMBED_BATCH_SIZE);
      const embeddings = await embedTexts(batch.map((chunk) => chunk.text));
      batch.forEach((chunk, index) => {
        chunk.embedding = embeddings[index] || [];
      });
    }
    return document;
  }

  function syncUrl(scope, cursor) {
    const params = new URLSearchParams({
      scope,
      since_id: String(cursor || 0),
      limit: String(DEFAULT_LIMIT),
      include_chunks: "true"
    });
    return `/api/knowledge/sync?${params.toString()}`;
  }

  function manifestUrl(scope) {
    const params = new URLSearchParams({ scope });
    return `/api/knowledge/sync/manifest?${params.toString()}`;
  }

  async function fetchScopeManifest(scope) {
    const fetcher = window.DrTransitionAPI?.apiFetch || fetch;
    const response = await fetcher(manifestUrl(scope));
    if (response.status === 401) {
      throw new Error("Authentication required for knowledge sync.");
    }
    if (!response.ok) {
      throw new Error(`Knowledge manifest failed for ${scope} with status ${response.status}.`);
    }
    const data = await response.json();
    if (data.error) {
      throw new Error(data.detail || `Knowledge manifest failed for ${scope}.`);
    }
    return data;
  }

  async function fetchSyncPage(scope, cursor) {
    const fetcher = window.DrTransitionAPI?.apiFetch || fetch;
    const response = await fetcher(syncUrl(scope, cursor));
    if (response.status === 401) {
      throw new Error("Authentication required for knowledge sync.");
    }
    if (!response.ok) {
      throw new Error(`Knowledge sync failed for ${scope} with status ${response.status}.`);
    }
    const data = await response.json();
    if (data.error) {
      throw new Error(data.detail || `Knowledge sync failed for ${scope}.`);
    }
    return data;
  }

  async function syncScope(scope) {
    const store = clientStore();
    const manifest = await store.manifest(scope);
    const remoteManifest = await fetchScopeManifest(scope);
    const remoteDocumentIds = new Set((remoteManifest.documents || []).map((document) => String(document.id)));
    const remoteChecksums = new Map(
      (remoteManifest.documents || []).map((document) => [String(document.id), String(document.checksum || "")])
    );
    const localDocuments = await store.listDocuments(scope);
    const staleIds = localDocuments
      .map((document) => String(document.id))
      .filter((documentId) => !remoteDocumentIds.has(documentId));
    const changedExistingIds = (remoteManifest.documents || [])
      .map((document) => String(document.id))
      .filter((documentId) => {
        const localChecksum = manifest.document_checksums?.[documentId];
        const remoteChecksum = remoteChecksums.get(documentId);
        return localChecksum && remoteChecksum && localChecksum !== remoteChecksum;
      });
    if (staleIds.length) {
      await store.deleteDocuments({
        scope,
        sync_cursor: String(remoteManifest.cursor ?? manifest.sync_cursor ?? 0),
        checksum: remoteManifest.checksum || null,
        document_ids: staleIds
      });
    }

    let cursor = changedExistingIds.length ? 0 : Number(manifest.sync_cursor || 0) || 0;
    let downloaded = 0;
    let deleted = staleIds.length;
    let hasMore = true;

    while (hasMore) {
      const data = await fetchSyncPage(scope, cursor);
      const deletedIds = (data.deleted_document_ids || []).map((id) => String(id));
      if (deletedIds.length) {
        await store.deleteDocuments({
          scope,
          sync_cursor: String(data.next_cursor ?? cursor),
          document_ids: deletedIds
        });
        deleted += deletedIds.length;
      }

      const documents = [];
      for (const rawDocument of data.documents || []) {
        documents.push(await embedDocument(await normalizeDocument(rawDocument)));
      }
      if (documents.length) {
        await store.upsertDocuments({
          scope,
          sync_cursor: String(data.next_cursor ?? cursor),
          documents
        });
        downloaded += documents.length;
      } else if (data.next_cursor !== undefined && String(data.next_cursor) !== String(cursor)) {
        await store.upsertDocuments({
          scope,
          sync_cursor: String(data.next_cursor),
          documents: []
        });
      }

      cursor = Number(data.next_cursor ?? cursor) || cursor;
      hasMore = Boolean(data.has_more);
      if (!hasMore) {
        break;
      }
    }

    await store.upsertDocuments({
      scope,
      sync_cursor: String(remoteManifest.cursor ?? cursor),
      checksum: remoteManifest.checksum || null,
      documents: []
    });
    await buildIndex(scope);
    return {
      scope,
      downloaded,
      deleted,
      cursor,
      remote_checksum: remoteManifest.checksum || null,
      remote_documents: remoteDocumentIds.size,
      changed_existing: changedExistingIds.length
    };
  }

  async function buildIndex(scope) {
    const documents = await clientStore().listDocuments(scope);
    const chunks = [];
    for (const document of documents) {
      for (const chunk of document.chunks || []) {
        if (!chunk.text || !Array.isArray(chunk.embedding) || !chunk.embedding.length) {
          continue;
        }
        chunks.push({
          scope,
          document_id: document.id,
          document_title: document.title || "",
          chunk_id: chunk.id,
          text: chunk.text,
          embedding: chunk.embedding,
          source_uri: chunk.source_ref || document.source_uri || "",
          page: chunk.page || null,
          metadata: {
            ...(document.metadata || {}),
            ...(chunk.metadata || {})
          }
        });
      }
    }
    const index = {
      scope,
      built_at: new Date().toISOString(),
      chunk_count: chunks.length,
      chunks
    };
    indexByScope.set(scope, index);
    try {
      localStorage.setItem(`dr_transition_kb_index_${scope}_meta`, JSON.stringify({
        built_at: index.built_at,
        chunk_count: index.chunk_count
      }));
    } catch (error) {
      console.warn("Could not persist KB index metadata", error);
    }
    return index;
  }

  async function syncAll(options = {}) {
    if (syncInFlight) return lastStatus;
    syncInFlight = true;
    lastStatus = {
      ...lastStatus,
      running: true,
      lastStartedAt: new Date().toISOString(),
      error: null
    };
    try {
      const scopes = options.scopes || SYNC_SCOPES;
      for (const scope of scopes) {
        lastStatus.scopes[scope] = await syncScope(scope);
      }
      lastStatus.lastCompletedAt = new Date().toISOString();
      return lastStatus;
    } catch (error) {
      lastStatus.error = String(error?.message || error);
      console.warn("Client KB sync failed", error);
      return lastStatus;
    } finally {
      lastStatus.running = false;
      syncInFlight = false;
    }
  }

  function start(options = {}) {
    const intervalMs = Number(options.intervalMs || DEFAULT_INTERVAL_MS);
    if (syncTimer) {
      clearInterval(syncTimer);
    }
    syncAll({ initial: true });
    syncTimer = setInterval(() => syncAll({ incremental: true }), intervalMs);
    return status();
  }

  function stop() {
    if (syncTimer) {
      clearInterval(syncTimer);
      syncTimer = null;
    }
  }

  function status() {
    return {
      ...lastStatus,
      indexes: Object.fromEntries(
        Array.from(indexByScope.entries()).map(([scope, index]) => [scope, {
          built_at: index.built_at,
          chunk_count: index.chunk_count
        }])
      )
    };
  }

  function index(scope) {
    return indexByScope.get(scope) || null;
  }

  window.DrTransitionKBSync = {
    start,
    stop,
    syncAll,
    syncScope,
    buildIndex,
    status,
    index
  };
})();

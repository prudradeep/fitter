(function () {
  "use strict";

  const DB_NAME = "dr-transition-knowledge";
  const DB_VERSION = 1;
  const PERSISTENT_SCOPES = ["main", "sector_prompt", "validated_evidence"];
  const TEMPORARY_SCOPE = "temporary";

  function tauriInvoke() {
    return window.__TAURI__?.core?.invoke || null;
  }

  function normalizeScope(scope) {
    if (![...PERSISTENT_SCOPES, TEMPORARY_SCOPE].includes(scope)) {
      throw new Error(`Unsupported knowledge scope: ${scope}`);
    }
    return scope;
  }

  function normalizeSession(scope, sessionId) {
    if (scope === TEMPORARY_SCOPE) {
      if (!sessionId) {
        throw new Error("Temporary knowledge requires a session id.");
      }
      return String(sessionId);
    }
    if (sessionId) {
      throw new Error("Session ids are only valid for temporary knowledge.");
    }
    return "";
  }

  function keyFor(scope, sessionId, documentId) {
    return [scope, sessionId || "", documentId || ""].join("\u001f");
  }

  function nowText() {
    return String(Math.floor(Date.now() / 1000));
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("documents")) {
          db.createObjectStore("documents", { keyPath: "key" });
        }
        if (!db.objectStoreNames.contains("manifests")) {
          db.createObjectStore("manifests", { keyPath: "key" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function withStore(storeName, mode, callback) {
    const db = await openDb();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, mode);
        const store = transaction.objectStore(storeName);
        let result;
        transaction.oncomplete = () => resolve(result);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
        result = callback(store);
      });
    } finally {
      db.close();
    }
  }

  function requestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function getManifest(scope, sessionId) {
    const key = keyFor(scope, sessionId);
    const record = await withStore("manifests", "readonly", (store) => requestToPromise(store.get(key)));
    if (record?.manifest) {
      return record.manifest;
    }
    return {
      store_version: 1,
      scope,
      session_id: sessionId || null,
      sync_cursor: null,
      checksum: null,
      document_checksums: {},
      tombstones: {},
      updated_at: nowText()
    };
  }

  async function putManifest(manifest) {
    const key = keyFor(manifest.scope, manifest.session_id || "");
    await withStore("manifests", "readwrite", (store) => store.put({ key, manifest }));
  }

  async function listBrowserDocuments(scope, sessionId) {
    const prefix = keyFor(scope, sessionId);
    const documents = [];
    await withStore("documents", "readonly", (store) => {
      const request = store.openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) {
          return;
        }
        if (cursor.key.startsWith(prefix)) {
          documents.push(cursor.value.document);
        }
        cursor.continue();
      };
    });
    return documents.sort((left, right) => String(left.id).localeCompare(String(right.id)));
  }

  async function status() {
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_store_status");
    }

    const persistentScopes = [];
    for (const scope of PERSISTENT_SCOPES) {
      const manifest = await getManifest(scope, "");
      persistentScopes.push({
        scope,
        session_id: null,
        document_count: Object.keys(manifest.document_checksums || {}).length,
        tombstone_count: Object.keys(manifest.tombstones || {}).length,
        sync_cursor: manifest.sync_cursor || null,
        checksum: manifest.checksum || null,
        path: "indexeddb"
      });
    }
    return { base_dir: "indexeddb", persistent_scopes: persistentScopes, temporary_sessions: [] };
  }

  async function manifest(scope, sessionId = null) {
    scope = normalizeScope(scope);
    sessionId = normalizeSession(scope, sessionId);
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_scope_manifest", { scope, sessionId: sessionId || null });
    }
    return getManifest(scope, sessionId);
  }

  async function listDocuments(scope, sessionId = null) {
    scope = normalizeScope(scope);
    sessionId = normalizeSession(scope, sessionId);
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_list_documents", { scope, sessionId: sessionId || null });
    }
    return listBrowserDocuments(scope, sessionId);
  }

  async function getDocument(scope, documentId, sessionId = null) {
    scope = normalizeScope(scope);
    sessionId = normalizeSession(scope, sessionId);
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_get_document", { scope, documentId, sessionId: sessionId || null });
    }
    const record = await withStore("documents", "readonly", (store) => requestToPromise(store.get(keyFor(scope, sessionId, documentId))));
    return record?.document || null;
  }

  async function upsertDocuments(batch) {
    const scope = normalizeScope(batch.scope);
    const sessionId = normalizeSession(scope, batch.session_id || batch.sessionId || null);
    const normalized = {
      scope,
      session_id: sessionId || null,
      sync_cursor: batch.sync_cursor || batch.syncCursor || null,
      checksum: batch.checksum || null,
      documents: batch.documents || []
    };
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_upsert_documents", { batch: normalized });
    }

    const manifest = await getManifest(scope, sessionId);
    for (const document of normalized.documents) {
      const checksum = document.checksum || `chunks:${(document.chunks || []).length}:title:${document.title || ""}`;
      manifest.document_checksums[document.id] = checksum;
      delete manifest.tombstones[document.id];
      await withStore("documents", "readwrite", (store) => store.put({ key: keyFor(scope, sessionId, document.id), document }));
    }
    if (normalized.sync_cursor) {
      manifest.sync_cursor = normalized.sync_cursor;
    }
    if (normalized.checksum) {
      manifest.checksum = normalized.checksum;
    }
    manifest.updated_at = nowText();
    await putManifest(manifest);
    return {
      scope,
      session_id: sessionId || null,
      document_count: Object.keys(manifest.document_checksums).length,
      tombstone_count: Object.keys(manifest.tombstones).length
    };
  }

  async function deleteDocuments(batch) {
    const scope = normalizeScope(batch.scope);
    const sessionId = normalizeSession(scope, batch.session_id || batch.sessionId || null);
    const documentIds = batch.document_ids || batch.documentIds || [];
    const normalized = {
      scope,
      session_id: sessionId || null,
      sync_cursor: batch.sync_cursor || batch.syncCursor || null,
      checksum: batch.checksum || null,
      document_ids: documentIds
    };
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_delete_documents", { batch: normalized });
    }

    const manifest = await getManifest(scope, sessionId);
    for (const documentId of normalized.document_ids) {
      const checksum = manifest.document_checksums[documentId] || null;
      delete manifest.document_checksums[documentId];
      manifest.tombstones[documentId] = { document_id: documentId, checksum, deleted_at: nowText() };
      await withStore("documents", "readwrite", (store) => store.delete(keyFor(scope, sessionId, documentId)));
    }
    if (normalized.sync_cursor) {
      manifest.sync_cursor = normalized.sync_cursor;
    }
    if (normalized.checksum) {
      manifest.checksum = normalized.checksum;
    }
    manifest.updated_at = nowText();
    await putManifest(manifest);
    return {
      scope,
      session_id: sessionId || null,
      document_count: Object.keys(manifest.document_checksums).length,
      tombstone_count: Object.keys(manifest.tombstones).length
    };
  }

  async function clearTemporary(sessionId) {
    if (!sessionId) {
      throw new Error("Temporary knowledge requires a session id.");
    }
    const invoke = tauriInvoke();
    if (invoke) {
      return invoke("kb_clear_temporary", { sessionId });
    }
    const documents = await listBrowserDocuments(TEMPORARY_SCOPE, String(sessionId));
    for (const document of documents) {
      await withStore("documents", "readwrite", (store) => store.delete(keyFor(TEMPORARY_SCOPE, String(sessionId), document.id)));
    }
    await withStore("manifests", "readwrite", (store) => store.delete(keyFor(TEMPORARY_SCOPE, String(sessionId))));
  }

  window.DrTransitionClientKB = {
    scopes: {
      MAIN: "main",
      SECTOR_PROMPT: "sector_prompt",
      VALIDATED_EVIDENCE: "validated_evidence",
      TEMPORARY: "temporary"
    },
    status,
    manifest,
    listDocuments,
    getDocument,
    upsertDocuments,
    deleteDocuments,
    clearTemporary
  };
})();

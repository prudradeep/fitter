(function () {
  "use strict";

  const TEMPORARY_SCOPE = "temporary";
  const MAX_CHARS_PER_CHUNK = 1400;
  const CHUNK_OVERLAP = 180;
  const EMBED_BATCH_SIZE = 8;
  const tempIndexBySession = new Map();

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
      "Temporary evidence embeddings require the Windows desktop app. Open Dr Transition from the Windows app/installer."
    );
  }

  function store() {
    if (!window.DrTransitionClientKB) {
      throw new Error("Client knowledge store is not available.");
    }
    return window.DrTransitionClientKB;
  }

  function newId(prefix) {
    if (crypto.randomUUID) {
      return `${prefix}:${crypto.randomUUID()}`;
    }
    return `${prefix}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
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

  function normalizeWhitespace(text) {
    return String(text || "")
      .replace(/\r/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
    doc.querySelectorAll("script, style, noscript, svg, canvas").forEach((node) => node.remove());
    return normalizeWhitespace(doc.body?.textContent || "");
  }

  function bytesToBinary(bytes) {
    let output = "";
    const size = 0x8000;
    for (let index = 0; index < bytes.length; index += size) {
      output += String.fromCharCode(...bytes.subarray(index, index + size));
    }
    return output;
  }

  function bytesToUtf8(bytes) {
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  }

  function readUInt16(bytes, offset) {
    return bytes[offset] | (bytes[offset + 1] << 8);
  }

  function readUInt32(bytes, offset) {
    return (
      bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24)
    ) >>> 0;
  }

  async function inflateRaw(bytes) {
    if (typeof DecompressionStream !== "function") {
      throw new Error("Compressed document content cannot be decompressed in this browser.");
    }
    let lastError = null;
    for (const format of ["deflate-raw", "deflate"]) {
      try {
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream(format));
        return new Uint8Array(await new Response(stream).arrayBuffer());
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error("Compressed document content could not be decompressed.");
  }

  async function zipEntries(bytes) {
    const entries = new Map();
    let offset = 0;
    while (offset + 30 <= bytes.length) {
      const signature = readUInt32(bytes, offset);
      if (signature !== 0x04034b50) {
        offset += 1;
        continue;
      }
      const flags = readUInt16(bytes, offset + 6);
      const method = readUInt16(bytes, offset + 8);
      const compressedSize = readUInt32(bytes, offset + 18);
      const fileNameLength = readUInt16(bytes, offset + 26);
      const extraLength = readUInt16(bytes, offset + 28);
      const nameStart = offset + 30;
      const nameEnd = nameStart + fileNameLength;
      const dataStart = nameEnd + extraLength;
      const usesDataDescriptor = Boolean(flags & 0x08);
      if (usesDataDescriptor || !compressedSize || dataStart + compressedSize > bytes.length) {
        offset = dataStart + Math.max(1, compressedSize);
        continue;
      }
      const name = bytesToUtf8(bytes.subarray(nameStart, nameEnd));
      const compressed = bytes.subarray(dataStart, dataStart + compressedSize);
      let content = null;
      if (method === 0) {
        content = compressed;
      } else if (method === 8) {
        content = await inflateRaw(compressed);
      }
      if (content) {
        entries.set(name, content);
      }
      offset = dataStart + compressedSize;
    }
    return entries;
  }

  async function docxToText(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const entries = await zipEntries(bytes);
    const xmlNames = [
      "word/document.xml",
      "word/footnotes.xml",
      "word/endnotes.xml",
      "word/comments.xml"
    ].filter((name) => entries.has(name));
    if (!xmlNames.length) {
      throw new Error("Could not find readable DOCX document XML.");
    }
    const sections = [];
    for (const name of xmlNames) {
      const xml = bytesToUtf8(entries.get(name));
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const paragraphs = Array.from(doc.getElementsByTagName("w:p")).map((paragraph) =>
        Array.from(paragraph.getElementsByTagName("w:t"))
          .map((node) => node.textContent || "")
          .join("")
      );
      sections.push(paragraphs.filter(Boolean).join("\n"));
    }
    return normalizeWhitespace(sections.join("\n\n"));
  }

  function decodePdfString(value) {
    return String(value || "")
      .replace(/\\([nrtbf()\\])/g, (_, code) => {
        const map = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", "(": "(", ")": ")", "\\": "\\" };
        return map[code] || code;
      })
      .replace(/\\([0-7]{1,3})/g, (_, octal) => String.fromCharCode(parseInt(octal, 8)))
      .replace(/\\\r?\n/g, "");
  }

  function decodePdfHexString(value) {
    const clean = String(value || "").replace(/\s+/g, "");
    const bytes = [];
    for (let index = 0; index < clean.length; index += 2) {
      bytes.push(parseInt(clean.slice(index, index + 2).padEnd(2, "0"), 16));
    }
    if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
      let text = "";
      for (let index = 2; index + 1 < bytes.length; index += 2) {
        text += String.fromCharCode((bytes[index] << 8) | bytes[index + 1]);
      }
      return text;
    }
    return bytes.map((byte) => String.fromCharCode(byte)).join("");
  }

  function extractPdfTextFromContent(content) {
    const text = [];
    const source = String(content || "");
    const textBlocks = source.match(/BT[\s\S]*?ET/g) || [source];
    for (const block of textBlocks) {
      const stringPattern = /\((?:\\.|[^\\)])*\)\s*Tj|\[(.*?)\]\s*TJ|<([0-9a-fA-F\s]+)>\s*Tj/g;
      let match;
      while ((match = stringPattern.exec(block))) {
        if (match[0].includes(" TJ")) {
          const arrayBody = match[1] || "";
          const parts = [];
          arrayBody.replace(/\((?:\\.|[^\\)])*\)|<([0-9a-fA-F\s]+)>/g, (token, hex) => {
            parts.push(hex ? decodePdfHexString(hex) : decodePdfString(token.slice(1, -1)));
            return token;
          });
          text.push(parts.join(""));
        } else if (match[2]) {
          text.push(decodePdfHexString(match[2]));
        } else {
          const token = match[0].replace(/\s*Tj$/, "");
          text.push(decodePdfString(token.slice(1, -1)));
        }
      }
    }
    return text.join(" ");
  }

  async function pdfToText(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const binary = bytesToBinary(bytes);
    const extracted = [];
    const streamPattern = /<<(.*?)>>\s*stream\r?\n?([\s\S]*?)\r?\n?endstream/g;
    let match;
    while ((match = streamPattern.exec(binary))) {
      const dictionary = match[1] || "";
      const raw = match[2] || "";
      let streamBytes = Uint8Array.from(raw, (character) => character.charCodeAt(0) & 0xff);
      if (/\/FlateDecode\b/.test(dictionary)) {
        try {
          streamBytes = await inflateRaw(streamBytes);
        } catch (error) {
          continue;
        }
      }
      const text = extractPdfTextFromContent(bytesToBinary(streamBytes));
      if (text) extracted.push(text);
    }
    if (!extracted.length) {
      extracted.push(extractPdfTextFromContent(binary));
    }
    const text = normalizeWhitespace(extracted.join("\n\n"));
    if (!text) {
      throw new Error("Could not extract readable text from this PDF locally.");
    }
    return text;
  }

  async function textFromFile(file) {
    const name = String(file?.name || "").toLowerCase();
    const type = String(file?.type || "").toLowerCase();
    if (name.endsWith(".pdf") || type === "application/pdf") {
      return pdfToText(file);
    }
    if (
      name.endsWith(".docx") ||
      type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ) {
      return docxToText(file);
    }
    if (
      name.endsWith(".txt") ||
      name.endsWith(".md") ||
      name.endsWith(".markdown") ||
      name.endsWith(".csv") ||
      name.endsWith(".json") ||
      type.startsWith("text/") ||
      type === "application/json"
    ) {
      return normalizeWhitespace(await file.text());
    }
    if (name.endsWith(".html") || name.endsWith(".htm") || type === "text/html") {
      return htmlToText(await file.text());
    }
    throw new Error("This file type cannot be parsed locally yet. Use PDF, DOCX, TXT, MD, CSV, JSON, or HTML for temporary evidence.");
  }

  async function textFromUrl(url) {
    const response = await fetch(url, { credentials: "omit", mode: "cors" });
    if (!response.ok) {
      throw new Error(`Could not fetch evidence URL. HTTP ${response.status}.`);
    }
    const contentType = response.headers.get("content-type") || "";
    const raw = await response.text();
    if (contentType.includes("html")) {
      return htmlToText(raw);
    }
    return normalizeWhitespace(raw);
  }

  function chunkText(text) {
    const clean = normalizeWhitespace(text);
    if (!clean) return [];
    const chunks = [];
    let start = 0;
    while (start < clean.length) {
      let end = Math.min(clean.length, start + MAX_CHARS_PER_CHUNK);
      if (end < clean.length) {
        const boundary = Math.max(
          clean.lastIndexOf("\n\n", end),
          clean.lastIndexOf(". ", end),
          clean.lastIndexOf(" ", end)
        );
        if (boundary > start + 400) {
          end = boundary + 1;
        }
      }
      chunks.push(clean.slice(start, end).trim());
      if (end >= clean.length) break;
      start = Math.max(0, end - CHUNK_OVERLAP);
    }
    return chunks.filter(Boolean);
  }

  async function sourceToDocument(source, context = {}) {
    const title = source.title || source.uri || "Temporary evidence";
    const text = source.text;
    const parts = chunkText(text);
    if (!parts.length) {
      throw new Error(`No readable text found in ${title}.`);
    }

    const chunkPayloads = parts.map((part, index) => ({
      id: `${source.id}:chunk:${index}`,
      text: part,
      page: null,
      source_ref: source.uri || title,
      checksum: null,
      embedding: null,
      metadata: {
        context: context.context || null,
        original_name: source.title || null,
        source_kind: source.kind,
        chunk_index: index,
        created_at: new Date().toISOString()
      }
    }));

    for (let start = 0; start < chunkPayloads.length; start += EMBED_BATCH_SIZE) {
      const batch = chunkPayloads.slice(start, start + EMBED_BATCH_SIZE);
      const embeddings = await embedTexts(batch.map((chunk) => chunk.text));
      batch.forEach((chunk, index) => {
        chunk.embedding = embeddings[index] || [];
      });
    }

    for (const chunk of chunkPayloads) {
      chunk.checksum = await sha256([chunk.id, chunk.text, chunk.source_ref].join("\n"));
    }

    const checksum = await sha256(JSON.stringify({
      title,
      uri: source.uri,
      chunks: chunkPayloads.map((chunk) => chunk.checksum)
    }));
    return {
      id: source.id,
      title,
      source_uri: source.uri || null,
      source_type: source.kind,
      checksum,
      version: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      metadata: {
        temporary: true,
        context: context.context || null,
        created_at: new Date().toISOString()
      },
      chunks: chunkPayloads
    };
  }

  async function buildTemporaryIndex(sessionId) {
    const documents = await store().listDocuments(TEMPORARY_SCOPE, sessionId);
    const chunks = [];
    for (const document of documents) {
      for (const chunk of document.chunks || []) {
        if (!chunk.text || !Array.isArray(chunk.embedding) || !chunk.embedding.length) continue;
        chunks.push({
          scope: TEMPORARY_SCOPE,
          session_id: sessionId,
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
      scope: TEMPORARY_SCOPE,
      session_id: sessionId,
      built_at: new Date().toISOString(),
      chunk_count: chunks.length,
      chunks
    };
    tempIndexBySession.set(sessionId, index);
    return index;
  }

  async function storeTemporaryEvidence({ sessionId, evidenceUrl = "", evidenceFile = null, context = "" }) {
    if (!sessionId) {
      throw new Error("Temporary evidence requires an active session.");
    }
    const sources = [];
    const cleanUrl = String(evidenceUrl || "").trim();
    if (cleanUrl) {
      sources.push({
        id: newId("temp-url"),
        title: cleanUrl,
        uri: cleanUrl,
        kind: "url",
        text: await textFromUrl(cleanUrl)
      });
    }
    if (evidenceFile instanceof File && evidenceFile.size > 0) {
      sources.push({
        id: newId("temp-file"),
        title: evidenceFile.name,
        uri: evidenceFile.name,
        kind: "file",
        text: await textFromFile(evidenceFile)
      });
    }
    if (!sources.length) {
      return { documents: [], chunks: 0 };
    }

    const documents = [];
    for (const source of sources) {
      documents.push(await sourceToDocument(source, { context }));
    }
    await store().upsertDocuments({
      scope: TEMPORARY_SCOPE,
      session_id: sessionId,
      documents
    });
    const index = await buildTemporaryIndex(sessionId);
    return {
      documents,
      chunks: documents.reduce((total, document) => total + (document.chunks || []).length, 0),
      index
    };
  }

  function contextValue(context, key) {
    if (!context || typeof context !== "object") return null;
    if (context[key] !== undefined && context[key] !== null) return context[key];
    const session = context.session && typeof context.session === "object" ? context.session : {};
    return session[key] ?? null;
  }

  function optionalNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function validationSummary(validation = {}) {
    const parts = [
      validation.status,
      validation.decision?.label,
      validation.summary,
      validation.decision?.reason
    ]
      .map((part) => String(part || "").trim())
      .filter(Boolean);
    return parts.join(" - ").slice(0, 240);
  }

  function promotionPayload(document, { sessionId, context = {}, validation = {} } = {}) {
    return {
      title: String(document.title || "Validated evidence").slice(0, 255),
      source_type: String(document.source_type || document.metadata?.source_kind || "validated_user_evidence").slice(0, 40),
      source_uri: document.source_uri || null,
      country_id: optionalNumber(contextValue(context, "country_id")),
      region_id: optionalNumber(contextValue(context, "region_id")),
      sector_id: optionalNumber(contextValue(context, "sector_id")),
      session_key: sessionId || null,
      validation_summary: validationSummary(validation),
      chunks: (document.chunks || [])
        .map((chunk, index) => ({
          content: String(chunk.text || chunk.content || "").trim(),
          chunk_index: Number.isFinite(Number(chunk.metadata?.chunk_index)) ? Number(chunk.metadata.chunk_index) : index,
          source_type: String(chunk.metadata?.source_type || document.source_type || "validated_user_evidence").slice(0, 40),
          source_uri: chunk.source_ref || document.source_uri || null,
          page_number: optionalNumber(chunk.page)
        }))
        .filter((chunk) => chunk.content)
    };
  }

  async function postPromotionPayload(payload) {
    const fetcher = window.DrTransitionAPI?.csrfFetch || (typeof csrfFetch === "function" ? csrfFetch : fetch);
    const response = await fetcher("/api/validated-evidence/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload)
    });
    if (response.status === 401) {
      throw new Error("Authentication required to promote validated evidence.");
    }
    if (!response.ok) {
      throw new Error(`Validated evidence promotion failed with status ${response.status}.`);
    }
    const data = await response.json();
    if (data.error) {
      throw new Error(data.detail || "Validated evidence promotion failed.");
    }
    return data;
  }

  async function promoteValidatedEvidence({ sessionId, context = {}, validation = {}, documentIds = null, clearPromoted = true } = {}) {
    if (!sessionId) {
      throw new Error("Validated evidence promotion requires an active session.");
    }
    const allDocuments = await store().listDocuments(TEMPORARY_SCOPE, sessionId);
    const selectedIds = Array.isArray(documentIds) && documentIds.length ? new Set(documentIds.map(String)) : null;
    const documents = selectedIds ? allDocuments.filter((document) => selectedIds.has(String(document.id))) : allDocuments;
    if (!documents.length) {
      return { promoted_documents: [], chunks: 0, sync_status: null };
    }

    const promoted = [];
    for (const document of documents) {
      const payload = promotionPayload(document, { sessionId, context, validation });
      if (!payload.chunks.length) continue;
      const result = await postPromotionPayload(payload);
      promoted.push({
        local_id: document.id,
        document: result.document,
        document_id: result.document?.id || null,
        chunks: result.chunks || 0,
        version: result.version || result.document?.created_at || null
      });
    }

    if (promoted.length && clearPromoted) {
      await store().deleteDocuments({
        scope: TEMPORARY_SCOPE,
        session_id: sessionId,
        document_ids: promoted.map((item) => item.local_id)
      });
      await buildTemporaryIndex(sessionId);
    }

    let syncStatus = null;
    if (promoted.length && window.DrTransitionKBSync?.syncScope) {
      syncStatus = await window.DrTransitionKBSync.syncScope("validated_evidence");
    }

    return {
      promoted_documents: promoted,
      chunks: promoted.reduce((total, item) => total + Number(item.chunks || 0), 0),
      sync_status: syncStatus
    };
  }

  async function clearTemporary(sessionId) {
    if (!sessionId) return;
    await store().clearTemporary(sessionId);
    tempIndexBySession.delete(sessionId);
  }

  function index(sessionId) {
    return tempIndexBySession.get(sessionId) || null;
  }

  window.DrTransitionEvidence = {
    storeTemporaryEvidence,
    clearTemporary,
    buildTemporaryIndex,
    index,
    promoteValidatedEvidence,
    textFromFile,
    textFromUrl,
    chunkText
  };
})();

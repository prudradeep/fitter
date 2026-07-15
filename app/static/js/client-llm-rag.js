(function () {
  "use strict";

  const DEFAULT_SCOPES = ["main", "sector_prompt", "validated_evidence"];
  const TEMPORARY_SCOPE = "temporary";
  const DEFAULT_TOP_K = 8;

  class LocalLlmError extends Error {
    constructor(message, code = "local_llm_error", detail = null) {
      super(message);
      this.name = "LocalLlmError";
      this.code = code;
      this.detail = detail;
    }
  }

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
    throw new LocalLlmError(
      "This hosted web page is running outside the Windows desktop app, so it cannot use the desktop local LLM/RAG bridge. Open Dr Transition from the Windows app/installer.",
      "desktop_runtime_required",
      { origin: window.location?.origin || "" }
    );
  }

  async function runtimeConfig() {
    const tauriInvoke = invoke();
    if (tauriInvoke) {
      return tauriInvoke("runtime_config");
    }
    assertSupportedRuntime();
    return {
      ollama: {
        baseUrl: localStorage.getItem("dr_transition_ollama_base_url") || "http://127.0.0.1:11434",
        chatModel: localStorage.getItem("dr_transition_ollama_chat_model") || "mistral-nemo",
        embeddingModel: localStorage.getItem("dr_transition_ollama_embedding_model") || "nomic-embed-text"
      },
      grounding: {
        enabled: localStorage.getItem("dr_transition_grounding_enabled") !== "false",
        rerankerUrl: localStorage.getItem("dr_transition_reranker_url") || "http://127.0.0.1:8081/rerank",
        nliUrl: localStorage.getItem("dr_transition_nli_url") || "http://127.0.0.1:8082/entail"
      }
    };
  }

  async function modelStatus() {
    const tauriInvoke = invoke();
    if (tauriInvoke) {
      return tauriInvoke("ollama_model_status");
    }
    assertSupportedRuntime();
    const config = await runtimeConfig();
    const baseUrl = config.ollama?.baseUrl || "http://127.0.0.1:11434";
    const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/tags`);
    if (!response.ok) {
      throw new LocalLlmError("Ollama is not reachable.", "ollama_unavailable", { status: response.status });
    }
    const data = await response.json();
    const models = (data.models || []).map((model) => model.name).filter(Boolean);
    const chatModel = config.ollama?.chatModel || "mistral-nemo";
    const embeddingModel = config.ollama?.embeddingModel || "nomic-embed-text";
    return {
      baseUrl,
      chatModel,
      embeddingModel,
      ollamaReachable: true,
      chatModelInstalled: hasModel(models, chatModel),
      embeddingModelInstalled: hasModel(models, embeddingModel),
      models
    };
  }

  function hasModel(models, expected) {
    return models.some((model) => model === expected || model.replace(/:latest$/, "") === expected);
  }

  async function assertAvailable() {
    const status = await modelStatus();
    if (!status.ollamaReachable) {
      throw new LocalLlmError("Ollama is not reachable. Start Ollama and try again.", "ollama_unavailable", status);
    }
    if (!status.chatModelInstalled) {
      throw new LocalLlmError(`Chat model is not installed. Run: ollama pull ${status.chatModel}`, "chat_model_missing", status);
    }
    if (!status.embeddingModelInstalled) {
      throw new LocalLlmError(`Embedding model is not installed. Run: ollama pull ${status.embeddingModel}`, "embedding_model_missing", status);
    }
    return status;
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
        throw new LocalLlmError(`Ollama embedding failed with status ${response.status}.`, "embedding_failed", { status: response.status });
      }
      const data = await response.json();
      embeddings.push(data.embedding || []);
    }
    return embeddings;
  }

  async function chat(messages, options = {}) {
    await assertAvailable();
    const cleanMessages = messages.map((message) => ({
      role: message.role || "user",
      content: String(message.content || "")
    }));
    const tauriInvoke = invoke();
    if (tauriInvoke) {
      return tauriInvoke("ollama_chat", { messages: cleanMessages, options: options.options || null });
    }
    assertSupportedRuntime();
    const config = await runtimeConfig();
    const baseUrl = config.ollama?.baseUrl || "http://127.0.0.1:11434";
    const model = options.model || config.ollama?.chatModel || "mistral-nemo";
    const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: cleanMessages,
        stream: false,
        options: options.options || {}
      })
    });
    if (!response.ok) {
      throw new LocalLlmError(`Ollama chat failed with status ${response.status}.`, "chat_failed", { status: response.status });
    }
    const data = await response.json();
    return data.message?.content || data.response || "";
  }

  function cosineSimilarity(left, right) {
    if (!Array.isArray(left) || !Array.isArray(right) || !left.length || left.length !== right.length) {
      return 0;
    }
    let dot = 0;
    let leftMag = 0;
    let rightMag = 0;
    for (let index = 0; index < left.length; index += 1) {
      dot += left[index] * right[index];
      leftMag += left[index] * left[index];
      rightMag += right[index] * right[index];
    }
    if (!leftMag || !rightMag) return 0;
    return dot / (Math.sqrt(leftMag) * Math.sqrt(rightMag));
  }

  async function indexForScope(scope, sessionId) {
    if (scope === TEMPORARY_SCOPE) {
      return window.DrTransitionEvidence?.index(sessionId)
        || await window.DrTransitionEvidence?.buildTemporaryIndex(sessionId)
        || null;
    }
    let index = window.DrTransitionKBSync?.index(scope);
    if (!index && window.DrTransitionKBSync?.buildIndex) {
      index = await window.DrTransitionKBSync.buildIndex(scope);
    }
    return index || null;
  }

  async function retrieve(query, options = {}) {
    await assertAvailable();
    const scopes = options.scopes || DEFAULT_SCOPES;
    const sessionId = options.sessionId || null;
    const topK = Number(options.topK || DEFAULT_TOP_K);
    const [queryEmbedding] = await embedTexts([query]);
    const results = [];

    for (const scope of scopes) {
      const index = await indexForScope(scope, sessionId);
      for (const chunk of index?.chunks || []) {
        const score = cosineSimilarity(queryEmbedding, chunk.embedding);
        if (score <= 0) continue;
        results.push({
          ...chunk,
          score,
          citation_id: ""
        });
      }
    }

    const candidates = results
      .sort((left, right) => right.score - left.score)
      .slice(0, Math.max(topK * 3, topK));
    const grounded = await applyGrounding(query, candidates, topK);
    return grounded
      .slice(0, topK)
      .map((item, index) => ({ ...item, citation_id: `S${index + 1}` }));
  }

  async function applyGrounding(query, contexts, topK) {
    if (!contexts.length) return contexts;
    const config = await runtimeConfig();
    if (config.grounding?.enabled === false) {
      return contexts.slice(0, topK);
    }
    const reranked = await rerankContexts(query, contexts, config);
    const entailed = await entailContexts(query, reranked.slice(0, Math.max(topK * 2, topK)), config);
    return entailed.sort((left, right) => {
      const leftEntailed = left.nli_entailed ? 1 : 0;
      const rightEntailed = right.nli_entailed ? 1 : 0;
      if (leftEntailed !== rightEntailed) return rightEntailed - leftEntailed;
      return Number(right.score || 0) - Number(left.score || 0);
    });
  }

  async function rerankContexts(query, contexts, config) {
    const url = config.grounding?.rerankerUrl || "http://127.0.0.1:8081/rerank";
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          documents: contexts.map((context) => String(context.text || "").slice(0, 16000))
        })
      });
      if (!response.ok) throw new Error(`reranker returned HTTP ${response.status}`);
      const payload = await response.json();
      const scores = Array.isArray(payload.scores) ? payload.scores : [];
      if (scores.length !== contexts.length) throw new Error("reranker returned an unexpected score count");
      return contexts
        .map((context, index) => ({
          ...context,
          retrieval_score: context.score,
          score: Number(scores[index] || 0),
          reranker_score: Number(scores[index] || 0)
        }))
        .sort((left, right) => right.score - left.score);
    } catch (error) {
      console.warn("Local reranker unavailable; using embedding retrieval scores.", error);
      return contexts;
    }
  }

  async function entailContexts(query, contexts, config) {
    const url = config.grounding?.nliUrl || "http://127.0.0.1:8082/entail";
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pairs: contexts.map((context) => ({
            premise: String(context.text || "").slice(0, 30000),
            hypothesis: String(query || "").slice(0, 10000)
          }))
        })
      });
      if (!response.ok) throw new Error(`NLI returned HTTP ${response.status}`);
      const payload = await response.json();
      const verdicts = Array.isArray(payload.results) ? payload.results : [];
      if (verdicts.length !== contexts.length) throw new Error("NLI returned an unexpected result count");
      return contexts.map((context, index) => {
        const verdict = verdicts[index] || {};
        const label = String(verdict.label || "").toLowerCase();
        const score = Number(verdict.score || 0);
        return {
          ...context,
          nli_label: label,
          nli_score: score,
          nli_entailed: ["entailment", "entailed"].includes(label) && score >= 0.5
        };
      });
    } catch (error) {
      console.warn("Local NLI unavailable; using reranked contexts without entailment labels.", error);
      return contexts;
    }
  }

  function citationsFromContexts(contexts) {
    return contexts.map((context) => ({
      id: context.citation_id,
      scope: context.scope,
      document_id: context.document_id,
      document_title: context.document_title,
      chunk_id: context.chunk_id,
      source_uri: context.source_uri,
      page: context.page,
      score: context.score,
      retrieval_score: context.retrieval_score,
      reranker_score: context.reranker_score,
      nli_label: context.nli_label,
      nli_score: context.nli_score,
      nli_entailed: context.nli_entailed
    }));
  }

  function buildPrompt({ question, contexts = [], task = "", responseFormat = "" }) {
    const contextText = contexts.length
      ? contexts.map((context) => [
          `[${context.citation_id}] ${context.document_title || "Untitled source"}`,
          `Scope: ${context.scope}`,
          context.source_uri ? `Source: ${context.source_uri}` : "",
          context.page ? `Page: ${context.page}` : "",
          context.nli_label ? `Entailment: ${context.nli_label} ${context.nli_score || 0}` : "",
          context.text
        ].filter(Boolean).join("\n")).join("\n\n")
      : "No retrieved local knowledge was available.";
    return [
      "You are Dr Transition's local analysis assistant.",
      "Use only the provided local context when making evidence-backed claims.",
      "Cite supporting sources with bracketed citation ids like [S1].",
      "If the context is insufficient, say what is missing.",
      task ? `Task: ${task}` : "",
      responseFormat ? `Response format: ${responseFormat}` : "",
      "",
      "Local context:",
      contextText,
      "",
      "User question:",
      question
    ].filter((part) => part !== "").join("\n");
  }

  function extractJson(text) {
    const raw = String(text || "").trim();
    const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
    const candidate = fenced ? fenced[1].trim() : raw;
    const start = candidate.indexOf("{");
    const end = candidate.lastIndexOf("}");
    if (start === -1 || end === -1 || end <= start) return null;
    try {
      return JSON.parse(candidate.slice(start, end + 1));
    } catch (_error) {
      return null;
    }
  }

  function parseResponse(text, citations = []) {
    const raw = String(text || "").trim();
    const citedIds = Array.from(new Set(Array.from(raw.matchAll(/\[(S\d+)\]/g)).map((match) => match[1])));
    return {
      raw,
      text: raw,
      json: extractJson(raw),
      cited_ids: citedIds,
      citations: citations.filter((citation) => citedIds.includes(citation.id))
    };
  }

  async function ask(question, options = {}) {
    const contexts = await retrieve(question, options);
    const citations = citationsFromContexts(contexts);
    const prompt = buildPrompt({
      question,
      contexts,
      task: options.task || "",
      responseFormat: options.responseFormat || ""
    });
    const raw = await chat([
      { role: "system", content: options.system || "You answer with careful, concise, cited analysis." },
      { role: "user", content: prompt }
    ], options);
    return {
      answer: parseResponse(raw, citations),
      contexts,
      citations,
      prompt
    };
  }

  window.DrTransitionLocalRAG = {
    LocalLlmError,
    assertAvailable,
    modelStatus,
    runtimeConfig,
    embedTexts,
    chat,
    retrieve,
    buildPrompt,
    citationsFromContexts,
    parseResponse,
    ask
  };
})();

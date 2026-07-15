(function () {
  "use strict";

  const WORKFLOW_JSON_FORMAT = `Return only a JSON object with this shape:
{
  "status": "ok | needs_more_info | rejected | error",
  "message": "HTML-free response for the user",
  "summary": "one sentence summary for voice/UI",
  "decision": {
    "label": "short outcome label",
    "confidence": "low | medium | high",
    "reason": "brief rationale"
  },
  "next_state": {},
  "citations": ["S1"]
}
Do not include markdown fences, retrieved context text, prompts, or private reasoning.`;

  function html(value) {
    return typeof escapeHtml === "function" ? escapeHtml(value) : String(value || "");
  }

  function paragraphs(text) {
    return String(text || "")
      .split(/\n{2,}/)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => `<p>${html(part).replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  function plainTextFromHtml(value) {
    return String(value || "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>/gi, "\n\n")
      .replace(/<[^>]+>/g, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function voiceSummaryFromText(text) {
    return String(text || "").replace(/\s+/g, " ").trim().slice(0, 260);
  }

  function optionObjects(labels) {
    return labels.map((label, index) => ({ id: index + 1, label }));
  }

  function selectedScopes(includeTemporary = true) {
    const scopes = ["main", "sector_prompt", "validated_evidence"];
    if (includeTemporary) scopes.push("temporary");
    return scopes;
  }

  function baseResponse(serverData, patch = {}) {
    const botMessage = patch.bot_message || serverData.bot_message || "";
    return {
      ...serverData,
      ...patch,
      bot_message: botMessage,
      voice_summary: patch.voice_summary || voiceSummaryFromText(plainTextFromHtml(botMessage)),
      error: Boolean(patch.error ?? serverData.error),
    };
  }

  function contextLine(context = {}) {
    const parts = [
      `step=${context.step || "selection"}`,
      `input_mode=${context.inputMode || "text"}`,
    ];
    if (context.sessionId) parts.push(`session_id=${context.sessionId}`);
    if (context.options?.length) {
      parts.push(`options=${context.options.map((option) => option.label || option).join(" | ")}`);
    }
    return parts.join("; ");
  }

  function fallbackIntro(context = {}) {
    const countries = (context.countries || []).slice(0, 12);
    const countryList = countries.length
      ? `Choose a country to begin. Available examples: ${countries.join(", ")}.`
      : "Choose a country to begin the analysis.";
    return [
      "I am ready to run this workflow locally. The hosted server will persist final state, while this desktop app handles local LLM and RAG work.",
      countryList,
    ].join("\n\n");
  }

  function fallbackHelp(context = {}) {
    return [
      `You are currently at ${context.step || "selection"}.`,
      "You can type a selection, ask a grounded question, or provide evidence when the form asks for it. Local evidence stays on this machine until validation and promotion.",
    ].join("\n\n");
  }

  function isHelp(message) {
    return /^(help|\/help|what can i do|how does this work)\??$/i.test(String(message || "").trim());
  }

  function looksLikeQuestion(message) {
    const value = String(message || "").trim();
    return /\?$/.test(value) || /^(what|why|how|when|where|which|can|does|do|is|are)\b/i.test(value);
  }

  function validationKind(context = {}) {
    const step = context.step || "";
    const mode = context.inputMode || "";
    if (mode === "evaluation_question" || step.startsWith("evaluation")) return "evaluation";
    if (step === "socio_demographic_review" || step === "add_dgs" || step === "dg_reason_evidence") return "socio_demographic";
    if (step.startsWith("mitigation") || mode === "mitigation_measure") return "mitigation";
    if (mode === "reason_evidence" || step.includes("hazard")) return "hazard";
    return "";
  }

  function normalizeJson(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function normalizeStatus(value) {
    const status = String(value || "ok").toLowerCase().replace(/\s+/g, "_");
    return ["ok", "needs_more_info", "rejected", "error"].includes(status) ? status : "ok";
  }

  function citationMetadata(citations = [], requestedIds = []) {
    const requested = new Set((requestedIds || []).map(String));
    return (citations || [])
      .filter((citation) => !requested.size || requested.has(String(citation.id)))
      .map((citation) => ({
        id: citation.id,
        scope: citation.scope,
        document_id: citation.document_id,
        document_title: citation.document_title,
        chunk_id: citation.chunk_id,
        source_uri: citation.source_uri,
      }));
  }

  function outputFromResult(workflow, result, fallbackMessage) {
    const parsed = normalizeJson(result?.answer?.json);
    const message = String(parsed.message || result?.answer?.text || fallbackMessage || "").trim();
    const summary = String(parsed.summary || message).replace(/\s+/g, " ").trim();
    const citedIds = Array.isArray(parsed.citations) ? parsed.citations : [];
    const citations = citationMetadata(result?.citations || [], citedIds);
    return {
      workflow,
      status: normalizeStatus(parsed.status),
      message,
      summary,
      decision: normalizeJson(parsed.decision),
      next_state: normalizeJson(parsed.next_state),
      citations,
      structured: parsed,
      context_count: Array.isArray(result?.contexts) ? result.contexts.length : 0,
    };
  }

  function errorOutput(workflow, error, fallbackMessage) {
    const detail = error?.message || String(error || "Local workflow unavailable.");
    return {
      workflow,
      status: "error",
      message: fallbackMessage || `Local ${workflow.replace(/_/g, " ")} is unavailable: ${detail}`,
      summary: detail,
      decision: { label: "local_llm_unavailable", confidence: "low", reason: detail },
      next_state: {},
      citations: [],
      structured: { status: "error", message: fallbackMessage || detail },
      context_count: 0,
    };
  }

  async function localAsk(question, options = {}) {
    if (!window.DrTransitionLocalRAG) {
      throw new Error("Local RAG layer is not available.");
    }
    return window.DrTransitionLocalRAG.ask(question, {
      responseFormat: WORKFLOW_JSON_FORMAT,
      ...options,
    });
  }

  async function structuredAsk(workflow, question, context, options = {}) {
    const result = await localAsk(question, {
      scopes: options.scopes || selectedScopes(Boolean(context?.sessionId)),
      sessionId: context?.sessionId,
      topK: options.topK || 8,
      task: options.task || workflow.replace(/_/g, " "),
      responseFormat: options.responseFormat || WORKFLOW_JSON_FORMAT,
    });
    return outputFromResult(workflow, result, options.fallbackMessage);
  }

  function responseForWorkflow(serverData, context, output, patch = {}) {
    const botMessage = paragraphs(output.message);
    return baseResponse(serverData, {
      step: context.step || serverData.step || "client_state",
      input_mode: context.inputMode || serverData.input_mode || "text",
      options: context.options || serverData.options || [],
      ...patch,
      bot_message: botMessage || patch.bot_message || serverData.bot_message || "",
      error: output.status === "error" || patch.error,
      validation_details: {
        ...(patch.validation_details || {}),
        workflow: output.workflow,
        status: output.status,
        summary: output.summary,
        decision: output.decision,
        next_state: output.next_state,
        citations: output.citations,
        context_count: output.context_count,
      },
      workflow_result: {
        workflow: output.workflow,
        status: output.status,
        summary: output.summary,
        decision: output.decision,
        next_state: output.next_state,
        citations: output.citations,
      },
    });
  }

  async function persistWorkflowState(response, output, context = {}) {
    const sessionId = context.sessionId || response.session_id;
    if (!sessionId || typeof csrfFetch !== "function") return;
    const content = plainTextFromHtml(response.bot_message || output.message || output.summary);
    const previousSession = normalizeJson(context.session);
    const phase = response.step || context.step || previousSession.phase || "client_state";
    const payload = {
      session_id: sessionId,
      phase,
      step: phase,
      input_mode: response.input_mode || context.inputMode || "text",
      session: {
        ...previousSession,
        phase,
        input_mode: response.input_mode || context.inputMode || previousSession.input_mode || "text",
        workflow_result: {
          workflow: output.workflow,
          status: output.status,
          summary: output.summary,
          decision: output.decision,
          next_state: output.next_state,
          citations: output.citations,
          completed_at: new Date().toISOString(),
        },
      },
      messages: content
        ? [
            {
              role: "bot",
              content,
              is_error: output.status === "error",
            },
          ]
        : [],
    };
    try {
      await csrfFetch("/api/sessions/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.warn("Workflow state persistence failed", error);
    }
  }

  async function finishWorkflow(serverData, context, output, patch = {}, persist = true) {
    const response = responseForWorkflow(serverData, context, output, patch);
    if (persist) await persistWorkflowState(response, output, context);
    return response;
  }

  async function intro(serverData, context = {}) {
    const workflow = "intro_help";
    try {
      const output = await structuredAsk(
        workflow,
        [
          "Write a concise welcome message for starting a Dr Transition policy analysis.",
          "Ask the user to choose a country.",
          `Runtime context: ${contextLine(context)}`,
        ].join("\n"),
        context,
        {
          scopes: ["sector_prompt", "main"],
          topK: 5,
          task: "Intro/help/voice summary generation",
          fallbackMessage: fallbackIntro(context),
        }
      );
      return finishWorkflow(serverData, context, output, {
        step: "country",
        input_mode: "text",
        options: optionObjects(context.countries || []),
      });
    } catch (error) {
      const output = errorOutput(workflow, error, fallbackIntro(context));
      return finishWorkflow(serverData, context, output, {
        step: "country",
        input_mode: "text",
        options: optionObjects(context.countries || []),
      });
    }
  }

  async function help(serverData, context = {}) {
    const workflow = "intro_help";
    try {
      const output = await structuredAsk(
        workflow,
        `Explain what the user can do next. Runtime context: ${contextLine(context)}`,
        context,
        {
          topK: 5,
          task: "Intro/help/voice summary generation",
          fallbackMessage: fallbackHelp(context),
        }
      );
      return finishWorkflow(serverData, context, output);
    } catch (error) {
      return finishWorkflow(serverData, context, errorOutput(workflow, error, fallbackHelp(context)));
    }
  }

  async function groundedQuestion(message, serverData, context = {}) {
    const workflow = "grounded_question";
    try {
      const output = await structuredAsk(
        workflow,
        [`Answer the user's grounded question using only local retrieved context.`, `Question: ${message}`, `Runtime context: ${contextLine(context)}`].join("\n"),
        context,
        {
          topK: 8,
          task: "Grounded question answering",
          fallbackMessage: "I found local context, but could not format the answer.",
        }
      );
      const sourceText = output.citations.length
        ? `\n\nSources: ${output.citations.map((citation) => `${citation.id} ${citation.document_title || citation.source_uri || citation.scope}`).join("; ")}`
        : "";
      return finishWorkflow(serverData, context, { ...output, message: `${output.message}${sourceText}` });
    } catch (error) {
      return finishWorkflow(serverData, context, errorOutput(workflow, error));
    }
  }

  async function validate(kind, message, serverData, context = {}) {
    const workflow = `${kind}_validation`;
    const prompts = {
      hazard: "Validate whether the user reason supports treating this as a transition hazard.",
      socio_demographic: "Validate whether the affected socio-demographic group selection is justified.",
      mitigation: "Validate whether the mitigation measure addresses the selected hazard and affected groups.",
      evaluation: "Validate whether the evaluation score and rationale are consistent with the evidence.",
    };
    try {
      const output = await structuredAsk(
        workflow,
        [
          prompts[kind] || "Validate the current workflow answer.",
          `User input: ${message || ""}`,
          `Runtime context: ${contextLine(context)}`,
          "Return a structured validation decision. Use decision.label as accepted, rejected, or needs_more_info. Cite only local sources that support the decision.",
        ].join("\n"),
        context,
        {
          topK: 10,
          task: `${kind} validation`,
          fallbackMessage: "The local validation completed, but no message was returned.",
        }
      );
      return finishWorkflow(serverData, context, output, {
        validation_details: {
          title: `${kind.replace(/_/g, " ")} validation`,
          reason: output.decision.reason || output.summary,
        },
      });
    } catch (error) {
      return finishWorkflow(serverData, context, errorOutput(workflow, error));
    }
  }

  async function openSelection(message, serverData, context = {}) {
    const workflow = "open_conversation_selection";
    try {
      const output = await structuredAsk(
        workflow,
        [
          "Help the user continue an open selection step.",
          "If their message matches an available option, explain the intended selection briefly.",
          "If it does not match, ask them to choose an available option or ask a grounded question.",
          `User message: ${message || ""}`,
          `Runtime context: ${contextLine(context)}`,
        ].join("\n"),
        context,
        {
          topK: 5,
          task: "Open conversation selection",
          fallbackMessage: "Please choose one of the available options, or ask a grounded question.",
        }
      );
      return finishWorkflow(serverData, context, output);
    } catch (error) {
      const fallback = context.options?.length
        ? "Please choose one of the available options, or ask a grounded question."
        : "I could not resolve that selection locally.";
      return finishWorkflow(serverData, context, errorOutput(workflow, error, fallback));
    }
  }

  async function statsDeepDive(message, serverData, context = {}) {
    const workflow = "stats_deep_dive";
    try {
      const output = await structuredAsk(
        workflow,
        [`Answer this stats deep-dive question from local knowledge only.`, `Question: ${message}`, `Runtime context: ${contextLine(context)}`].join("\n"),
        context,
        {
          topK: 8,
          task: "Stats deep-dive",
          fallbackMessage: "The local stats deep-dive completed, but no answer was returned.",
        }
      );
      return finishWorkflow(serverData, context, output);
    } catch (error) {
      return finishWorkflow(serverData, context, errorOutput(workflow, error));
    }
  }

  async function autoUserTesting(context = {}) {
    const workflow = "auto_user_testing";
    try {
      const output = await structuredAsk(
        workflow,
        [
          "Generate the next realistic user message for automated local workflow testing.",
          "Return the message in the JSON message field only.",
          `Runtime context: ${contextLine(context)}`,
        ].join("\n"),
        context,
        {
          scopes: ["sector_prompt", "main"],
          topK: 4,
          task: "Auto-user testing",
          fallbackMessage: "Help",
        }
      );
      return String(output.message || output.structured?.message || "").replace(/^["']|["']$/g, "").trim();
    } catch (_error) {
      const option = (context.options || [])[0];
      return typeof option === "string" ? option : option?.label || "Help";
    }
  }

  async function handleChatTurn({ message = "", serverData = {}, context = {} } = {}) {
    const cleanMessage = String(message || "").trim();
    const effectiveContext = {
      ...context,
      sessionId: context.sessionId || serverData.session_id || null,
    };
    if (!cleanMessage || cleanMessage === "/reset") return intro(serverData, effectiveContext);
    if (isHelp(cleanMessage)) return help(serverData, effectiveContext);
    const kind = validationKind(effectiveContext);
    if (kind && effectiveContext.inputMode !== "text") {
      return validate(kind, cleanMessage, serverData, effectiveContext);
    }
    if (looksLikeQuestion(cleanMessage)) return groundedQuestion(cleanMessage, serverData, effectiveContext);
    if (["country", "region", "sector", "client_state", ""].includes(effectiveContext.step || "")) {
      return openSelection(cleanMessage, serverData, effectiveContext);
    }
    return groundedQuestion(cleanMessage, serverData, effectiveContext);
  }

  window.DrTransitionWorkflows = {
    handleChatTurn,
    statsDeepDive,
    autoUserTesting,
    intro,
    help,
    groundedQuestion,
    validate,
  };
})();

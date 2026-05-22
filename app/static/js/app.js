const sessionKey = "dr_transition_session_id";
const inputStateKeyPrefix = "dr_transition_input_state_";
const voiceEnabledKey = "dr_transition_voice_enabled";
const voicePreferenceKey = "dr_transition_voice_preference";
const teacherAvatarPath = "/static/img/teacher.png";

const chatLog = document.querySelector("#chatLog");
const optionTray = document.querySelector("#optionTray");
const chatForm = document.querySelector("#chatForm");
const messageInputRow = document.querySelector("#messageInputRow");
const messageInput = document.querySelector("#messageInput");
const micButton = document.querySelector("#micButton");
const reasonEvidenceFields = document.querySelector("#reasonEvidenceFields");
const evaluationFields = document.querySelector("#evaluationFields");
const primaryInputLabel = document.querySelector("#primaryInputLabel");
const secondaryInputLabel = document.querySelector("#secondaryInputLabel");
const reasonInput = document.querySelector("#reasonInput");
const evidenceInput = document.querySelector("#evidenceInput");
const evidenceFileInput = document.querySelector("#evidenceFileInput");
const evidenceUrlField = document.querySelector("#evidenceUrlField");
const evidenceFileField = document.querySelector("#evidenceFileField");
const secondaryReasonInput = document.querySelector("#secondaryReasonInput");
const scoreInput = document.querySelector("#scoreInput");
const scoreValue = document.querySelector("#scoreValue");
const evaluationReasonInput = document.querySelector("#evaluationReasonInput");
const evaluationEvidenceInput = document.querySelector("#evaluationEvidenceInput");
const evaluationEvidenceFileInput = document.querySelector("#evaluationEvidenceFileInput");
const sendButton = document.querySelector("#sendButton");
const resetButton = document.querySelector("#resetButton");
const logoutForm = document.querySelector(".logout-form");
const profileButton = document.querySelector("#profileButton");
const profilePanel = document.querySelector("#profilePanel");
const closeProfileButton = document.querySelector("#closeProfileButton");
const changePasswordLink = document.querySelector("#changePasswordLink");
const changePasswordDialog = document.querySelector("#changePasswordDialog");
const changePasswordForm = document.querySelector("#changePasswordForm");
const changePasswordMessage = document.querySelector("#changePasswordMessage");
const currentPasswordInput = document.querySelector("#currentPasswordInput");
const newPasswordInput = document.querySelector("#newPasswordInput");
const confirmNewPasswordInput = document.querySelector("#confirmNewPasswordInput");
const cancelPasswordButton = document.querySelector("#cancelPasswordButton");
const voiceAssistantToggle = document.querySelector("#voiceAssistantToggle");
const voicePreferenceSelect = document.querySelector("#voicePreferenceSelect");
const voiceAnalyzerElement = document.querySelector("#voiceAnalyzer");
const sessionsButton = document.querySelector("#sessionsButton");
const sessionsPanel = document.querySelector("#sessionsPanel");
const closeSessionsButton = document.querySelector("#closeSessionsButton");
const sessionsList = document.querySelector("#sessionsList");
const renameSessionDialog = document.querySelector("#renameSessionDialog");
const renameSessionForm = document.querySelector("#renameSessionForm");
const renameSessionInput = document.querySelector("#renameSessionInput");
const cancelRenameButton = document.querySelector("#cancelRenameButton");
const sessionEmpty = document.querySelector("#sessionEmpty");

const sessionFields = {
  country: document.querySelector("#sessionCountry"),
  region: document.querySelector("#sessionRegion"),
  sector: document.querySelector("#sessionSector"),
};

let sessionId = localStorage.getItem(sessionKey);
let loading = false;
let inputMode = "text";
let highlightedOptionLabel = "";
let pendingRenameSessionId = "";
let pendingRenameTitleElement = null;
let availableVoices = [];
let recognition = null;
let listening = false;
let micSupported = false;
let voiceAnalyzerCanvas = null;
let voiceAnalyzerContext = null;
let voiceAnalyzerFrame = null;
let voiceAnalyzerSpeaking = false;
let voiceAnalyzerLevel = 0;
let voiceAnalyzerTarget = 0;
let voiceAnalyzerLastBoundaryAt = 0;
let voiceAnalyzerProgress = 0;

const defaultPlaceholder = "Type a country, region, or sector...";

function plainTextFromHtml(html) {
  const element = document.createElement("div");
  element.innerHTML = html;
  return element.textContent.replace(/\s+/g, " ").trim();
}

function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  availableVoices = window.speechSynthesis.getVoices();
}

function selectedVoice() {
  if (!availableVoices.length) loadVoices();
  const preference = voicePreferenceSelect?.value || "auto";
  const englishVoices = availableVoices.filter((voice) => voice.lang?.startsWith("en"));
  const voices = englishVoices.length ? englishVoices : availableVoices;
  if (!voices.length) return null;

  if (preference === "female") {
    return (
      voices.find((voice) => /female|woman|zira|susan|samantha|victoria|karen/i.test(voice.name)) ||
      voices[0]
    );
  }
  if (preference === "male") {
    return (
      voices.find((voice) => /male|man|david|mark|daniel|alex|fred/i.test(voice.name)) ||
      voices[0]
    );
  }
  return voices.find((voice) => voice.default) || voices[0];
}

function voiceAssistantEnabled() {
  return Boolean(voiceAssistantToggle?.checked);
}

function ensureVoiceAnalyzer() {
  if (!voiceAnalyzerElement || !voiceAssistantEnabled()) return;
  voiceAnalyzerElement.hidden = false;

  if (!voiceAnalyzerCanvas) {
    voiceAnalyzerCanvas = document.createElement("canvas");
    voiceAnalyzerCanvas.className = "voice-analyzer-canvas";
    voiceAnalyzerElement.appendChild(voiceAnalyzerCanvas);
    voiceAnalyzerContext = voiceAnalyzerCanvas.getContext("2d");
  }
  drawVoiceAnalyzer(false);
}

function prepareVoiceAnalyzer(text) {
  voiceAnalyzerSpeaking = true;
  voiceAnalyzerLevel = 0.34;
  voiceAnalyzerTarget = 0.34;
  voiceAnalyzerLastBoundaryAt = performance.now();
  voiceAnalyzerProgress = 0;
  if (text.length) voiceAnalyzerProgress = Math.min(0.98, 1 / text.length);
}

function syncVoiceAnalyzerVisibility() {
  if (!voiceAnalyzerElement) return;
  voiceAnalyzerElement.hidden = !voiceAssistantEnabled();
  if (voiceAssistantEnabled()) ensureVoiceAnalyzer();
  else stopVoiceAnalyzer();
}

function startVoiceAnalyzer(text = "") {
  if (!voiceAssistantEnabled()) return;
  ensureVoiceAnalyzer();
  if (!voiceAnalyzerContext) return;

  voiceAnalyzerElement?.classList.add("is-active");
  prepareVoiceAnalyzer(text);
  cancelAnimationFrame(voiceAnalyzerFrame);
  animateVoiceAnalyzer();
}

function stopVoiceAnalyzer() {
  voiceAnalyzerSpeaking = false;
  voiceAnalyzerLevel = 0;
  voiceAnalyzerTarget = 0;
  cancelAnimationFrame(voiceAnalyzerFrame);
  voiceAnalyzerFrame = null;
  voiceAnalyzerElement?.classList.remove("is-active");
  drawVoiceAnalyzer(false);
}

function syncVoiceAnalyzerToSpeech(event, text) {
  if (!voiceAnalyzerSpeaking || !text) return;
  const charIndex = Math.max(0, Math.min(event.charIndex || 0, text.length - 1));
  const wordMatch = text.slice(charIndex).match(/\S+/);
  const word = wordMatch ? wordMatch[0] : "";
  const wordWeight = Math.min(1, Math.max(0.35, word.length / 12));

  voiceAnalyzerProgress = Math.max(0.02, Math.min(0.98, charIndex / text.length));
  voiceAnalyzerTarget = 0.48 + wordWeight * 0.52;
  voiceAnalyzerLevel = Math.max(voiceAnalyzerLevel, voiceAnalyzerTarget);
  voiceAnalyzerLastBoundaryAt = performance.now();
}

function sizeVoiceAnalyzerCanvas() {
  if (!voiceAnalyzerCanvas || !voiceAnalyzerElement) return { width: 0, height: 0, scale: 1 };
  const rect = voiceAnalyzerElement.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  if (voiceAnalyzerCanvas.width !== Math.floor(width * scale)) {
    voiceAnalyzerCanvas.width = Math.floor(width * scale);
  }
  if (voiceAnalyzerCanvas.height !== Math.floor(height * scale)) {
    voiceAnalyzerCanvas.height = Math.floor(height * scale);
  }
  voiceAnalyzerCanvas.style.width = `${width}px`;
  voiceAnalyzerCanvas.style.height = `${height}px`;
  voiceAnalyzerContext.setTransform(scale, 0, 0, scale, 0, 0);
  return { width, height, scale };
}

function drawVoiceAnalyzer(active) {
  if (!voiceAnalyzerContext) return;
  const { width, height } = sizeVoiceAnalyzerCanvas();
  if (!width || !height) return;

  const ctx = voiceAnalyzerContext;
  const midY = height / 2;
  const now = performance.now();
  const boundaryAge = now - voiceAnalyzerLastBoundaryAt;
  const decay = active ? Math.exp(-boundaryAge / 260) : 0;
  voiceAnalyzerLevel += (voiceAnalyzerTarget * decay - voiceAnalyzerLevel) * 0.28;
  if (active && boundaryAge > 360) {
    voiceAnalyzerTarget *= 0.82;
  }

  ctx.clearRect(0, 0, width, height);

  const clusters = [
    { center: 0.14, spread: 0.055, power: 0.93 },
    { center: 0.38, spread: 0.04, power: 0.54 },
    { center: 0.57, spread: 0.035, power: 0.58 },
    { center: 0.84, spread: 0.055, power: 1 },
  ];
  const barCount = Math.max(34, Math.floor(width / 13));
  const gap = width / (barCount + 1);
  const barWidth = Math.max(4, Math.min(7, gap * 0.42));
  const maxBarHeight = height * 0.82;
  const speechLevel = active ? Math.max(0.16, Math.min(1, voiceAnalyzerLevel)) : 0.42;

  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "#6a20c8");
  gradient.addColorStop(0.48, "#7b22d8");
  gradient.addColorStop(1, "#5a1db7");

  ctx.shadowColor = "rgba(123, 34, 216, 0.42)";
  ctx.shadowBlur = active ? 9 : 4;
  ctx.fillStyle = gradient;
  for (let index = 0; index < barCount; index += 1) {
    const x = gap * (index + 1);
    const normalizedX = x / width;
    let envelope = 0.08;
    clusters.forEach((cluster) => {
      envelope +=
        cluster.power *
        Math.exp(-((normalizedX - cluster.center) ** 2) / (2 * cluster.spread ** 2));
    });
    const wordFocus = Math.exp(-((normalizedX - voiceAnalyzerProgress) ** 2) / (2 * 0.07 ** 2));
    const speechShape = 0.58 + speechLevel * 0.34 + wordFocus * speechLevel * 0.46;
    const barHeight = Math.max(6, Math.min(maxBarHeight, envelope * maxBarHeight * speechShape));
    roundRect(ctx, x - barWidth / 2, midY - barHeight / 2, barWidth, barHeight, barWidth / 2);
    ctx.fill();
  }
  ctx.shadowBlur = 0;
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

function animateVoiceAnalyzer() {
  drawVoiceAnalyzer(true);
  if (voiceAnalyzerSpeaking) {
    voiceAnalyzerFrame = requestAnimationFrame(animateVoiceAnalyzer);
  }
}

function pauseSpeech() {
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  stopVoiceAnalyzer();
}

function speakServerMessage(html) {
  if (!voiceAssistantToggle?.checked || !("speechSynthesis" in window)) return;
  const text = plainTextFromHtml(html);
  if (!text) return;
  pauseSpeech();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = selectedVoice();
  if (voice) utterance.voice = voice;
  utterance.rate = 1;
  utterance.pitch = voicePreferenceSelect?.value === "male" ? 0.92 : 1.02;
  utterance.onstart = () => startVoiceAnalyzer(text);
  utterance.onboundary = (event) => syncVoiceAnalyzerToSpeech(event, text);
  utterance.onend = stopVoiceAnalyzer;
  utterance.onerror = stopVoiceAnalyzer;
  window.speechSynthesis.speak(utterance);
}

function configureVoiceControls() {
  const speechSupported = "speechSynthesis" in window;
  if (!voiceAssistantToggle || !voicePreferenceSelect) return;
  voiceAssistantToggle.checked = localStorage.getItem(voiceEnabledKey) === "true";
  voicePreferenceSelect.value = localStorage.getItem(voicePreferenceKey) || "auto";
  voiceAssistantToggle.disabled = !speechSupported;
  voicePreferenceSelect.disabled = !speechSupported;
  syncVoicePreferenceVisibility();
  syncVoiceAnalyzerVisibility();
  if (speechSupported) {
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
  }
}

function syncVoicePreferenceVisibility() {
  const voiceSelectWrap = voicePreferenceSelect?.closest(".voice-select");
  if (!voiceSelectWrap) return;
  voiceSelectWrap.hidden = !voiceAssistantToggle?.checked;
}

function configureMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !micButton) {
    if (micButton) micButton.disabled = true;
    return;
  }

  micSupported = true;
  recognition = new Recognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  recognition.onstart = () => {
    listening = true;
    micButton.classList.add("listening");
    micButton.setAttribute("aria-label", "Stop listening");
  };
  recognition.onend = () => {
    listening = false;
    micButton.classList.remove("listening");
    micButton.setAttribute("aria-label", "Speak message");
  };
  recognition.onresult = (event) => {
    let transcript = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      transcript += event.results[index][0].transcript;
    }
    messageInput.value = transcript.trim();
    updateOptionHighlight();
  };
}

function placeholderForStep(step, options = []) {
  if (options.length) {
    const optionLabels = options.map((option) => option.label);
    if (step === "hazard_profile_selection") {
      return "Select a hazard above, or type the hazard name...";
    }
    if (step === "socio_demographic_review") {
      return "Select Add more DGs or Move to next step...";
    }
    if (step === "stats_deep_dive") {
      return "Ask a follow-up question, or choose the next action...";
    }
    if (step === "reason_confirmation") {
      return "Select Yes or No...";
    }
    if (step === "mitigation_reason") {
      return "Enter a mitigation measure and reason below...";
    }
    if (step === "mitigation_review") {
      return "Ask about this mitigation, or move to next step...";
    }
    if (step === "target_population_question") {
      return "Choose a target population option...";
    }
    if (optionLabels.includes("Move to next step")) {
      return "Choose one of the options above...";
    }
    return "Select an option above, or type your answer...";
  }

  const placeholders = {
    add_dgs: "Enter one profile, or comma-separated profiles...",
    hazards: "Type the hazard you want to add...",
    mitigation: "Ask a mitigation question or continue the plan...",
    evaluation_question: "Use the score slider below...",
    complete: "Ask a follow-up question...",
    country: "Type or select a country...",
    region: "Type or select a region...",
    sector: "Type or select a sector...",
  };

  return placeholders[step] || defaultPlaceholder;
}

function setReasonEvidencePlaceholders(step, mode = "reason_evidence") {
  if (mode === "mitigation_reason") {
    primaryInputLabel.textContent = "Mitigation measure";
    secondaryInputLabel.textContent = "Reason";
    reasonInput.placeholder = "What mitigation measure should be used?";
    secondaryReasonInput.placeholder = "Why is this mitigation measure appropriate?";
    secondaryReasonInput.closest("label").hidden = false;
    evidenceUrlField.hidden = true;
    evidenceFileField.hidden = true;
    return;
  }

  primaryInputLabel.textContent = "Reason";
  secondaryInputLabel.textContent = "Reason";
  secondaryReasonInput.closest("label").hidden = true;
  evidenceUrlField.hidden = false;
  evidenceFileField.hidden = false;

  if (step === "socio_demographic_review") {
    reasonInput.placeholder = "Why should these DGs be treated as severely affected?";
    evidenceInput.placeholder = "https://example.org/demographic-evidence";
    return;
  }

  reasonInput.placeholder = "Why should this be treated as a hazard?";
  evidenceInput.placeholder = "https://example.org/hazard-evidence";
}

function normalizeForMatch(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function compactForMatch(value) {
  return normalizeForMatch(value).replace(/\s+/g, "");
}

function levenshteinDistance(a, b) {
  const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  const current = Array.from({ length: b.length + 1 }, () => 0);

  for (let i = 1; i <= a.length; i += 1) {
    current[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(
        current[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + cost,
      );
    }
    previous.splice(0, previous.length, ...current);
  }

  return previous[b.length];
}

function fuzzyScore(input, optionLabel) {
  const query = compactForMatch(input);
  const label = compactForMatch(optionLabel);
  if (!query || !label) return 0;
  if (query === label) return 1;
  if (label.includes(query)) return Math.min(0.95, 0.66 + query.length / label.length);

  const distance = levenshteinDistance(query, label);
  const length = Math.max(query.length, label.length);
  return 1 - distance / length;
}

function updateOptionHighlight() {
  highlightedOptionLabel = "";
  const query = messageInput.value.trim();
  const buttons = Array.from(optionTray.querySelectorAll("button"));
  buttons.forEach((button) => button.classList.remove("fuzzy-match"));

  if (!query || inputMode !== "text" || !buttons.length) return;

  const best = buttons.reduce(
    (match, button) => {
      const score = fuzzyScore(query, button.textContent);
      return score > match.score ? { button, score } : match;
    },
    { button: null, score: 0 },
  );

  if (best.button && best.score >= 0.45) {
    best.button.classList.add("fuzzy-match");
    highlightedOptionLabel = best.button.textContent;
  }
}

function nowLabel() {
  return new Intl.DateTimeFormat([], {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

function scrollToBottom() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addMessage(role, text, isError = false) {
  const row = document.createElement("div");
  row.className = `message-row ${role}${isError ? " error" : ""}`;

  const avatar = document.createElement(role === "bot" ? "img" : "div");
  avatar.className = `chat-avatar ${role === "bot" ? "teacher-avatar" : "user-avatar"}`;
  if (role === "bot") {
    avatar.src = teacherAvatarPath;
    avatar.alt = "Dr Transition";
  } else {
    avatar.setAttribute("aria-label", "User");
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "bot") {
    bubble.innerHTML = text;
  } else {
    bubble.textContent = text;
  }

  const timestamp = document.createElement("span");
  timestamp.className = "timestamp";
  timestamp.textContent = nowLabel();
  bubble.appendChild(timestamp);

  if (role === "user") {
    row.appendChild(bubble);
    row.appendChild(avatar);
  } else {
    row.appendChild(avatar);
    row.appendChild(bubble);
  }
  chatLog.appendChild(row);
  scrollToBottom();
  return row;
}

async function typeServerMessage(row, html) {
  const bubble = row.querySelector(".bubble");
  const timestamp = bubble.querySelector(".timestamp");
  timestamp.remove();
  bubble.textContent = "";

  const template = document.createElement("template");
  template.innerHTML = html;

  async function typeNode(node, parent) {
    if (node.nodeType === Node.TEXT_NODE) {
      const textNode = document.createTextNode("");
      parent.appendChild(textNode);
      const text = node.textContent || "";
      for (const char of text) {
        textNode.textContent += char;
        scrollToBottom();
        await new Promise((resolve) => setTimeout(resolve, 8));
      }
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) return;

    const clone = node.cloneNode(false);
    parent.appendChild(clone);
    for (const child of Array.from(node.childNodes)) {
      await typeNode(child, clone);
    }
  }

  for (const child of Array.from(template.content.childNodes)) {
    await typeNode(child, bubble);
  }
  bubble.appendChild(timestamp);
  scrollToBottom();
}

function addTyping() {
  const row = document.createElement("div");
  row.className = "message-row bot";
  row.dataset.typing = "true";
  row.innerHTML = `
    <img class="chat-avatar teacher-avatar" src="${teacherAvatarPath}" alt="Dr Transition" />
    <div class="bubble">
      <span class="typing" aria-label="Dr Transition is typing">
        <span></span><span></span><span></span>
      </span>
    </div>
  `;
  chatLog.appendChild(row);
  scrollToBottom();
  return row;
}

function setLoading(value) {
  loading = value;
  messageInput.disabled = value;
  reasonInput.disabled = value;
  secondaryReasonInput.disabled = value;
  evidenceInput.disabled = value;
  evidenceFileInput.disabled = value;
  scoreInput.disabled = value;
  evaluationReasonInput.disabled = value;
  evaluationEvidenceInput.disabled = value;
  evaluationEvidenceFileInput.disabled = value;
  sendButton.disabled = value;
  micButton.disabled = value || !micSupported || inputMode !== "text";
  optionTray.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.used === "true";
  });
}

function setInputMode(mode = "text", step = "", options = []) {
  inputMode = mode;
  const reasonEvidenceMode = mode === "reason_evidence" || mode === "mitigation_reason";
  const evaluationMode = mode === "evaluation_question";
  micButton.disabled = !micSupported || reasonEvidenceMode || evaluationMode;
  messageInput.placeholder = placeholderForStep(step, options);
  setReasonEvidencePlaceholders(step, mode);
  reasonEvidenceFields.classList.toggle("mitigation-mode", mode === "mitigation_reason");
  messageInputRow.hidden = reasonEvidenceMode || evaluationMode;
  reasonEvidenceFields.hidden = !reasonEvidenceMode;
  evaluationFields.hidden = !evaluationMode;

  if (reasonEvidenceMode) {
    reasonInput.focus();
  } else if (evaluationMode) {
    scoreInput.value = "5";
    scoreValue.textContent = "5";
    evaluationReasonInput.value = "";
    evaluationEvidenceInput.value = "";
    scoreInput.focus();
  } else {
    reasonInput.value = "";
    secondaryReasonInput.value = "";
    evidenceInput.value = "";
    evidenceFileInput.value = "";
    evaluationReasonInput.value = "";
    evaluationEvidenceInput.value = "";
    evaluationEvidenceFileInput.value = "";
    messageInput.focus();
  }
}

function updateSessionCard(session) {
  sessionFields.country.textContent = session.country || "Not selected";
  sessionFields.region.textContent = session.region || "Not selected";
  sessionFields.sector.textContent = session.sector || "Not selected";
  const hasSession = Boolean(session.country || session.region || session.sector);
  sessionEmpty.hidden = hasSession;
  document.querySelector(".session-list").hidden = !hasSession;
}

function disableOldOptions() {
  optionTray.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
    button.dataset.used = "true";
  });
}

function renderOptions(options) {
  optionTray.innerHTML = "";
  highlightedOptionLabel = "";
  options.forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "option-pill";
    button.textContent = option.label;
    button.addEventListener("click", () => {
      pauseSpeech();
      disableOldOptions();
      sendMessage(option.label, true);
    });
    optionTray.appendChild(button);
  });
  updateOptionHighlight();
}

function inputStateKey(id = sessionId) {
  return id ? `${inputStateKeyPrefix}${id}` : "";
}

function saveCurrentInputState() {
  const key = inputStateKey();
  if (!key) return;
  const state = {
    inputMode,
    message: messageInput.value,
    reason: reasonInput.value,
    secondaryReason: secondaryReasonInput.value,
    evidenceUrl: evidenceInput.value,
    score: scoreInput.value,
    evaluationReason: evaluationReasonInput.value,
    evaluationEvidenceUrl: evaluationEvidenceInput.value,
  };
  localStorage.setItem(key, JSON.stringify(state));
}

function clearCurrentInputState() {
  const key = inputStateKey();
  if (key) localStorage.removeItem(key);
}

function applySavedInputState() {
  const key = inputStateKey();
  if (!key) return;
  const saved = localStorage.getItem(key);
  if (!saved) return;

  try {
    const state = JSON.parse(saved);
    if (state.inputMode && state.inputMode !== inputMode) return;
    messageInput.value = state.message || "";
    reasonInput.value = state.reason || "";
    secondaryReasonInput.value = state.secondaryReason || "";
    evidenceInput.value = state.evidenceUrl || "";
    scoreInput.value = state.score || "5";
    scoreValue.textContent = scoreInput.value;
    evaluationReasonInput.value = state.evaluationReason || "";
    evaluationEvidenceInput.value = state.evaluationEvidenceUrl || "";
  } catch (error) {
    localStorage.removeItem(key);
  }
}

async function loadSessions() {
  if (!sessionsList) return;
  sessionsList.innerHTML = `<p class="sessions-empty">Loading sessions...</p>`;
  try {
    const response = await fetch("/api/sessions");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    renderSessions(data.sessions || []);
  } catch (error) {
    sessionsList.innerHTML = `<p class="sessions-empty">Could not load sessions.</p>`;
    console.error("Sessions request failed", error);
  }
}

function renderSessions(sessions) {
  if (!sessionsList) return;
  sessionsList.innerHTML = "";
  if (!sessions.length) {
    sessionsList.innerHTML = `<p class="sessions-empty">No saved sessions yet.</p>`;
    return;
  }

  sessions.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item";
    button.dataset.sessionId = item.session_id;
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = item.title || "New policy session";
    const updatedAt = document.createElement("small");
    updatedAt.textContent = formatSessionDate(item.updated_at);
    button.appendChild(title);
    button.appendChild(updatedAt);
    button.addEventListener("click", () => restoreSession(item.session_id));

    const renameButton = document.createElement("button");
    renameButton.type = "button";
    renameButton.className = "session-rename-button";
    renameButton.textContent = "✎";
    renameButton.setAttribute("aria-label", `Rename ${title.textContent}`);
    renameButton.title = "Rename session";
    renameButton.addEventListener("click", (event) => {
      event.stopPropagation();
      openRenameDialog(item.session_id, button.querySelector(".session-title"));
    });

    const row = document.createElement("div");
    row.className = "session-row";
    row.appendChild(button);
    row.appendChild(renameButton);
    sessionsList.appendChild(row);
  });
}

function openRenameDialog(targetSessionId, titleElement) {
  pendingRenameSessionId = targetSessionId;
  pendingRenameTitleElement = titleElement;
  const currentTitle = titleElement?.textContent?.trim() || "New policy session";
  renameSessionInput.value = currentTitle;
  if (typeof renameSessionDialog.showModal === "function") {
    renameSessionDialog.showModal();
  } else {
    renameSessionDialog.removeAttribute("hidden");
  }
  renameSessionInput.focus();
  renameSessionInput.select();
}

function closeRenameDialog() {
  pendingRenameSessionId = "";
  pendingRenameTitleElement = null;
  if (typeof renameSessionDialog.close === "function") {
    renameSessionDialog.close();
  } else {
    renameSessionDialog.setAttribute("hidden", "");
  }
}

async function renameSession(targetSessionId, titleElement, nextTitle) {
  const cleanTitle = nextTitle.trim();
  if (!cleanTitle) return;

  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: cleanTitle }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Session rename failed");
    if (titleElement) titleElement.textContent = data.title;
    closeRenameDialog();
  } catch (error) {
    console.error("Session rename failed", error);
  }
}

function formatSessionDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

async function restoreSession(nextSessionId) {
  if (!nextSessionId) return;
  pauseSpeech();
  saveCurrentInputState();
  setLoading(true);
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(nextSessionId)}`);
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Session restore failed");

    sessionId = data.session_id;
    localStorage.setItem(sessionKey, sessionId);
    chatLog.innerHTML = "";
    optionTray.innerHTML = "";
    (data.messages || []).forEach((message) => {
      addMessage(message.role, message.content, Boolean(message.is_error));
    });
    updateSessionCard(data.session || {});
    setInputMode(data.input_mode || "text", data.step, data.options || []);
    renderOptions(data.options || []);
    applySavedInputState();
    sessionsPanel.hidden = true;
  } catch (error) {
    console.error("Session restore failed", error);
  } finally {
    setLoading(false);
  }
}

async function sendMessage(message = "", echoUser = false, extras = {}) {
  if (loading) return;
  const cleanMessage = message.trim();
  if (echoUser && cleanMessage) addMessage("user", cleanMessage);
  clearCurrentInputState();

  const typing = addTyping();
  setLoading(true);

  try {
    const hasEvidenceFile = extras.evidenceFile instanceof File && extras.evidenceFile.size > 0;
    const hasEvidenceUrl = Boolean(extras.evidenceUrl);
    const useMultipart = hasEvidenceFile || hasEvidenceUrl;
    const response = await fetch("/api/chat", {
      method: "POST",
      ...(useMultipart
        ? {
            body: buildChatFormData(cleanMessage, extras),
          }
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message: cleanMessage,
              session_id: sessionId,
            }),
          }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const data = await response.json();
    sessionId = data.session_id;
    localStorage.setItem(sessionKey, sessionId);

    typing.remove();
    const botRow = addMessage("bot", "", data.error);
    speakServerMessage(data.bot_message);
    await typeServerMessage(botRow, data.bot_message);
    updateSessionCard(data.session);
    setInputMode(data.input_mode || "text", data.step, data.options || []);
    renderOptions(data.options || []);
    loadSessions();
  } catch (error) {
    typing.remove();
    console.error("Chat request failed", error);
  } finally {
    setLoading(false);
    if (inputMode === "reason_evidence" || inputMode === "mitigation_reason") {
      reasonInput.focus();
    } else if (inputMode === "evaluation_question") {
      scoreInput.focus();
    } else {
      messageInput.focus();
    }
  }
}

function buildChatFormData(message, extras = {}) {
  const formData = new FormData();
  formData.append("message", message);
  if (sessionId) formData.append("session_id", sessionId);
  if (extras.evidenceUrl) formData.append("evidence_url", extras.evidenceUrl);
  if (extras.evidenceFile instanceof File && extras.evidenceFile.size > 0) {
    formData.append("evidence_file", extras.evidenceFile);
  }
  return formData;
}

function evidenceSummary(url, file) {
  const lines = [];
  if (url) lines.push(`Evidence URL: ${url}`);
  if (file instanceof File && file.size > 0) lines.push(`Evidence file: ${file.name}`);
  return lines;
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();

  if (inputMode === "reason_evidence" || inputMode === "mitigation_reason") {
    const primaryValue = reasonInput.value.trim();
    const evidenceUrl = evidenceInput.value.trim();
    const evidenceFile = evidenceFileInput.files[0];

    if (inputMode === "mitigation_reason") {
      const secondaryValue = secondaryReasonInput.value.trim();
      if (!primaryValue || !secondaryValue) return;
      const value = `Mitigation measure: ${primaryValue}\nReason: ${secondaryValue}`;
      reasonInput.value = "";
      secondaryReasonInput.value = "";
      addMessage("user", value);
      sendMessage(value, false);
      return;
    }

    if (!primaryValue) return;
    const value = [`Reason: ${primaryValue}`, ...evidenceSummary(evidenceUrl, evidenceFile)].join("\n");
    reasonInput.value = "";
    evidenceInput.value = "";
    evidenceFileInput.value = "";
    addMessage("user", value);
    sendMessage(`Reason: ${primaryValue}`, false, { evidenceUrl, evidenceFile });
    return;
  }

  if (inputMode === "evaluation_question") {
    const score = scoreInput.value.trim();
    const reason = evaluationReasonInput.value.trim();
    const evidenceUrl = evaluationEvidenceInput.value.trim();
    const evidenceFile = evaluationEvidenceFileInput.files[0];
    const lines = [`Score: ${score}`];
    if (reason) lines.push(`Reason: ${reason}`);
    lines.push(...evidenceSummary(evidenceUrl, evidenceFile));
    const value = lines.join("\n");
    evaluationReasonInput.value = "";
    evaluationEvidenceInput.value = "";
    evaluationEvidenceFileInput.value = "";
    addMessage("user", value);
    sendMessage(lines.filter((line) => !line.startsWith("Evidence ")).join("\n"), false, {
      evidenceUrl,
      evidenceFile,
    });
    return;
  }

  const value = messageInput.value.trim();
  if (!value) return;
  messageInput.value = "";
  highlightedOptionLabel = "";
  disableOldOptions();
  addMessage("user", value);
  sendMessage(value, false);
});

messageInput.addEventListener("input", updateOptionHighlight);
scoreInput.addEventListener("input", () => {
  scoreValue.textContent = scoreInput.value;
});

voiceAssistantToggle?.addEventListener("change", () => {
  localStorage.setItem(voiceEnabledKey, String(voiceAssistantToggle.checked));
  syncVoicePreferenceVisibility();
  syncVoiceAnalyzerVisibility();
  if (!voiceAssistantToggle.checked && "speechSynthesis" in window) {
    pauseSpeech();
  }
});

voicePreferenceSelect?.addEventListener("change", () => {
  localStorage.setItem(voicePreferenceKey, voicePreferenceSelect.value);
});

micButton?.addEventListener("click", () => {
  if (!recognition || inputMode !== "text") return;
  if (listening) {
    recognition.stop();
    return;
  }
  messageInput.focus();
  try {
    recognition.start();
  } catch (error) {
    console.error("Speech recognition failed", error);
  }
});

resetButton.addEventListener("click", async () => {
  pauseSpeech();
  clearCurrentInputState();
  localStorage.removeItem(sessionKey);
  sessionId = null;
  chatLog.innerHTML = "";
  optionTray.innerHTML = "";
  updateSessionCard({ country: null, region: null, sector: null });
  setInputMode("text");
  await sendMessage("/reset", false);
});

sessionsButton?.addEventListener("click", async () => {
  sessionsPanel.hidden = !sessionsPanel.hidden;
  if (!sessionsPanel.hidden) {
    pauseSpeech();
    await loadSessions();
  }
});

closeSessionsButton?.addEventListener("click", () => {
  sessionsPanel.hidden = true;
});

profileButton?.addEventListener("click", () => {
  profilePanel.hidden = !profilePanel.hidden;
});

closeProfileButton?.addEventListener("click", () => {
  profilePanel.hidden = true;
});

renameSessionForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingRenameSessionId) return;
  await renameSession(
    pendingRenameSessionId,
    pendingRenameTitleElement,
    renameSessionInput.value,
  );
});

cancelRenameButton?.addEventListener("click", closeRenameDialog);

function openChangePasswordDialog() {
  changePasswordForm.reset();
  changePasswordMessage.hidden = true;
  profilePanel.hidden = true;
  if (typeof changePasswordDialog.showModal === "function") {
    changePasswordDialog.showModal();
  } else {
    changePasswordDialog.removeAttribute("hidden");
  }
  currentPasswordInput.focus();
}

function closeChangePasswordDialog() {
  if (typeof changePasswordDialog.close === "function") {
    changePasswordDialog.close();
  } else {
    changePasswordDialog.setAttribute("hidden", "");
  }
}

function showPasswordMessage(message, isError = true) {
  changePasswordMessage.textContent = message;
  changePasswordMessage.hidden = false;
  changePasswordMessage.classList.toggle("success", !isError);
}

changePasswordLink?.addEventListener("click", openChangePasswordDialog);
cancelPasswordButton?.addEventListener("click", closeChangePasswordDialog);

changePasswordForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const newPassword = newPasswordInput.value;
  const confirmPassword = confirmNewPasswordInput.value;
  if (newPassword !== confirmPassword) {
    showPasswordMessage("New passwords do not match.");
    return;
  }

  try {
    const response = await fetch("/api/profile/password", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: currentPasswordInput.value,
        new_password: newPassword,
        confirm_password: confirmPassword,
      }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) {
      showPasswordMessage(data.detail || "Could not update password.");
      return;
    }
    showPasswordMessage("Password updated.", false);
    setTimeout(closeChangePasswordDialog, 700);
  } catch (error) {
    showPasswordMessage("Could not update password.");
    console.error("Password change failed", error);
  }
});

logoutForm?.addEventListener("submit", () => {
  clearCurrentInputState();
  localStorage.removeItem(sessionKey);
});

document.addEventListener("DOMContentLoaded", () => {
  configureVoiceControls();
  configureMic();
  clearCurrentInputState();
  localStorage.removeItem(sessionKey);
  sessionId = null;
  loadSessions();
  sendMessage("", false);
});

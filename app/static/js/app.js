const appSettings = window.DrTransitionSettings || {};
const storageKeys = appSettings.storageKeys || {};
const sessionKey = storageKeys.session || "dr_transition_session_id";
const inputStateKeyPrefix = storageKeys.inputStatePrefix || "dr_transition_input_state_";
const voiceEnabledKey = storageKeys.voiceEnabled || "dr_transition_voice_enabled";
const voicePreferenceKey = storageKeys.voicePreference || "dr_transition_voice_preference";
const voiceLanguageKey = storageKeys.voiceLanguage || "dr_transition_voice_language";
const voiceRateKey = storageKeys.voiceRate || "dr_transition_voice_rate";
const voiceVolumeKey = storageKeys.voiceVolume || "dr_transition_voice_volume";
const typingEffectKey = storageKeys.typingEffect || "dr_transition_typing_effect_enabled";
const autoConversationKey = storageKeys.autoConversation || "dr_transition_auto_conversation_enabled";
const validationModeKey = storageKeys.validationMode || "dr_transition_validation_mode";
const crowdSourcingKey = storageKeys.crowdSourcing || "dr_transition_crowd_sourcing_enabled";
const teacherAvatarPath = appSettings.assets?.teacherAvatarPath || "/static/img/teacher.png";
const collapsibleMessageWordLimit = appSettings.chat?.collapsibleMessageWordLimit || 100;

const chatLog = document.querySelector("#chatLog");
const chatScrollBottomButton = document.querySelector("#chatScrollBottomButton");
const optionTray = document.querySelector("#optionTray");
const chatForm = document.querySelector("#chatForm");
const messageInputRow = document.querySelector("#messageInputRow");
const messageInput = document.querySelector("#messageInput");
const textareaInput = document.querySelector("#textareaInput");
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
const settingsButton = document.querySelector("#settingsButton");
const settingsDrawer = document.querySelector("#settingsDrawer");
const closeSettingsButton = document.querySelector("#closeSettingsButton");
const voiceAssistantToggle = document.querySelector("#voiceAssistantToggle");
const typingEffectToggle = document.querySelector("#typingEffectToggle");
const autoConversationToggle = document.querySelector("#autoConversationToggle");
const validationModeToggle = document.querySelector("#validationModeToggle");
const validationModeLabel = document.querySelector("#validationModeLabel");
const crowdSourcingToggle = document.querySelector("#crowdSourcingToggle");
const voicePreferenceButton = document.querySelector("#voicePreferenceButton");
const voicePreferenceDialog = document.querySelector("#voicePreferenceDialog");
const closeVoicePreferenceButton = document.querySelector("#closeVoicePreferenceButton");
const voicePreferenceSummary = document.querySelector("#voicePreferenceSummary");
const voicePreferenceSelect = document.querySelector("#voicePreferenceSelect");
const voiceLanguageSelect = document.querySelector("#voiceLanguageSelect");
const speechRateInput = document.querySelector("#speechRateInput");
const speechRateValue = document.querySelector("#speechRateValue");
const speechVolumeInput = document.querySelector("#speechVolumeInput");
const speechVolumeValue = document.querySelector("#speechVolumeValue");
const previewVoiceButton = document.querySelector("#previewVoiceButton");
const exportSessionButton = document.querySelector("#exportSessionButton");
const importSessionButton = document.querySelector("#importSessionButton");
const importSessionInput = document.querySelector("#importSessionInput");
const exportSessionStatus = document.querySelector("#exportSessionStatus");
const syncSettingsPanel = document.querySelector("#syncSettingsPanel");
const syncMyDataToggle = document.querySelector("#syncMyDataToggle");
const syncNowButton = document.querySelector("#syncNowButton");
const syncStatus = document.querySelector("#syncStatus");
const voiceAnalyzerElement = document.querySelector("#voiceAnalyzer");
const sessionsButton = document.querySelector("#sessionsButton");
const sessionsPanel = document.querySelector("#sessionsPanel");
const closeSessionsButton = document.querySelector("#closeSessionsButton");
const sessionsList = document.querySelector("#sessionsList");
const knowledgeButton = document.querySelector("#knowledgeButton");
const promptsButton = document.querySelector("#promptsButton");
const knowledgeDialog = document.querySelector("#knowledgeDialog");
const promptLibraryDialog = document.querySelector("#promptLibraryDialog");
const closePromptLibraryButton = document.querySelector("#closePromptLibraryButton");
const closeKnowledgeButton = document.querySelector("#closeKnowledgeButton");
const knowledgeUploadForm = document.querySelector("#knowledgeUploadForm");
const knowledgeUrlForm = document.querySelector("#knowledgeUrlForm");
const knowledgeSearchForm = document.querySelector("#knowledgeSearchForm");
const knowledgeFileInput = document.querySelector("#knowledgeFileInput");
const knowledgeUrlInput = document.querySelector("#knowledgeUrlInput");
const knowledgeSearchInput = document.querySelector("#knowledgeSearchInput");
const knowledgeMessage = document.querySelector("#knowledgeMessage");
const knowledgeProgressSection = document.querySelector("#knowledgeProgressSection");
const knowledgeProgressList = document.querySelector("#knowledgeProgressList");
const knowledgeDocuments = document.querySelector("#knowledgeDocuments");
const knowledgeDocumentCount = document.querySelector("#knowledgeDocumentCount");
const knowledgeDropzone = document.querySelector(".knowledge-dropzone");
const knowledgeResults = document.querySelector("#knowledgeResults");
const canManageMainKnowledge = knowledgeDialog?.dataset.canManageMainKb === "true";
const canManagePrompts = promptLibraryDialog?.dataset.canManagePrompts === "true";
const sectorPromptReindexButton = document.querySelector("#sectorPromptReindexButton");
const sectorPromptSearchForm = document.querySelector("#sectorPromptSearchForm");
const sectorPromptSectorInput = document.querySelector("#sectorPromptSectorInput");
const sectorPromptSearchInput = document.querySelector("#sectorPromptSearchInput");
const sectorPromptMessage = document.querySelector("#sectorPromptMessage");
const sectorPromptResults = document.querySelector("#sectorPromptResults");
const promptLibrarySection = document.querySelector("#promptLibrarySection");
const newPromptButton = document.querySelector("#newPromptButton");
const refreshPromptsButton = document.querySelector("#refreshPromptsButton");
const promptSourceSelect = document.querySelector("#promptSourceSelect");
const promptSourceMessage = document.querySelector("#promptSourceMessage");
const promptSearchInput = document.querySelector("#promptSearchInput");
const promptCatalogueCount = document.querySelector("#promptCatalogueCount");
const promptList = document.querySelector("#promptList");
const promptEditorForm = document.querySelector("#promptEditorForm");
const promptEditorTitle = document.querySelector("#promptEditorTitle");
const promptEditorMeta = document.querySelector("#promptEditorMeta");
const promptKeyField = document.querySelector("#promptKeyField");
const promptKeyInput = document.querySelector("#promptKeyInput");
const promptContentInput = document.querySelector("#promptContentInput");
const promptEditorMessage = document.querySelector("#promptEditorMessage");
const savePromptButton = document.querySelector("#savePromptButton");
const renameSessionDialog = document.querySelector("#renameSessionDialog");
const renameSessionForm = document.querySelector("#renameSessionForm");
const renameSessionInput = document.querySelector("#renameSessionInput");
const cancelRenameButton = document.querySelector("#cancelRenameButton");
const statsDeepDiveDialog = document.querySelector("#statsDeepDiveDialog");
const closeStatsDialogButton = document.querySelector("#closeStatsDialogButton");
const statsDialogLog = document.querySelector("#statsDialogLog");
const statsDialogForm = document.querySelector("#statsDialogForm");
const statsDialogInput = document.querySelector("#statsDialogInput");
const statsDialogSendButton = document.querySelector("#statsDialogSendButton");
const targetPopulationDialog = document.querySelector("#targetPopulationDialog");
const targetPopulationForm = document.querySelector("#targetPopulationForm");
const targetPopulationDialogBody = document.querySelector("#targetPopulationDialogBody");
const closeTargetPopulationButton = document.querySelector("#closeTargetPopulationButton");
const cancelTargetPopulationButton = document.querySelector("#cancelTargetPopulationButton");
const targetAllGeneralPopulationButton = document.querySelector("#targetAllGeneralPopulationButton");
const resetTargetPopulationButton = document.querySelector("#resetTargetPopulationButton");
const methodologyDialog = document.querySelector("#methodologyDialog");
const closeMethodologyButton = document.querySelector("#closeMethodologyButton");
const methodologyFrame = document.querySelector("#methodologyFrame");
const surveyResultsDialog = document.querySelector("#surveyResultsDialog");
const surveyResultsImage = document.querySelector("#surveyResultsImage");
const surveyResultsViewport = document.querySelector("#surveyResultsViewport");
const closeSurveyResultsButton = document.querySelector("#closeSurveyResultsButton");
const surveyResultsZoomIn = document.querySelector("#surveyResultsZoomIn");
const surveyResultsZoomOut = document.querySelector("#surveyResultsZoomOut");
const surveyResultsZoomReset = document.querySelector("#surveyResultsZoomReset");
const platformUsersDialog = document.querySelector("#platformUsersDialog");
const platformUsersImage = document.querySelector("#platformUsersImage");
const platformUsersViewport = document.querySelector("#platformUsersViewport");
const closePlatformUsersButton = document.querySelector("#closePlatformUsersButton");
const platformUsersZoomIn = document.querySelector("#platformUsersZoomIn");
const platformUsersZoomOut = document.querySelector("#platformUsersZoomOut");
const platformUsersZoomReset = document.querySelector("#platformUsersZoomReset");
const uiTour = document.querySelector("#uiTour");
const uiTourCard = document.querySelector(".ui-tour-card");
const uiTourStep = document.querySelector("#uiTourStep");
const uiTourTitle = document.querySelector("#uiTourTitle");
const uiTourText = document.querySelector("#uiTourText");
const uiTourSkip = document.querySelector("#uiTourSkip");
const uiTourBack = document.querySelector("#uiTourBack");
const uiTourNext = document.querySelector("#uiTourNext");
const sessionEmpty = document.querySelector("#sessionEmpty");
const selectedHazardContext = document.querySelector("#selectedHazardContext");
const selectedContextLabel = document.querySelector("#selectedContextLabel");
const selectedHazardName = document.querySelector("#selectedHazardName");
const affectedProfileContext = document.querySelector("#affectedProfileContext");
const affectedProfileList = document.querySelector("#affectedProfileList");
const affectedProfileEmpty = document.querySelector("#affectedProfileEmpty");
const mitigationReviewContext = document.querySelector("#mitigationReviewContext");
const benefitedProfileList = document.querySelector("#benefitedProfileList");
const benefitedProfileEmpty = document.querySelector("#benefitedProfileEmpty");
const mitigationConfidenceScore = document.querySelector("#mitigationConfidenceScore");
const mitigationGroundingStatus = document.querySelector("#mitigationGroundingStatus");
const mitigationSupportedDimensions = document.querySelector("#mitigationSupportedDimensions");
const mitigationVerdictStability = document.querySelector("#mitigationVerdictStability");
const mitigationSupportCorpus = document.querySelector("#mitigationSupportCorpus");
const mitigationLastNote = document.querySelector("#mitigationLastNote");
const stageVisualTitle = document.querySelector("#stageVisualTitle");
const stageVisualText = document.querySelector("#stageVisualText");
const stageProgressFill = document.querySelector("#stageProgressFill");
const stageProgress = document.querySelector(".stage-progress");
const stageProgressToggle = document.querySelector("#stageProgressToggle");
const stageProgressCurrent = document.querySelector("#stageProgressCurrent");
const stageSteps = Array.from(document.querySelectorAll("[data-stage-key]"));
const stageMap = document.querySelector("#stageMap");
const stageIconGrid = document.querySelector("#stageIconGrid");
const stageCoverageRows = JSON.parse(stageMap?.dataset.coverage || "[]");
const europeMapPath = stageMap?.dataset.europeMapPath || "";
const appShell = document.querySelector(".app-shell");
const workspaceResizer = document.querySelector("#workspaceResizer");
const floatingStatsButton = document.querySelector("#floatingStatsButton");

const sessionFields = {
  country: document.querySelector("#sessionCountry"),
  region: document.querySelector("#sessionRegion"),
  sector: document.querySelector("#sessionSector"),
};

const appState = {
  inputMode: "text",
  highlightedOptionLabel: "",
  currentStep: "",
  currentSession: {},
  currentOptions: [],
  currentOtherOptions: [],
};
const reportOptionScopes = new Map([
  ["download report mitigation measure", "current"],
  ["download report all mitigation measures created by me against this hazard", "user_hazard"],
  ["download report all mitigation measures created against this hazard from all users", "all_hazard"],
]);

let sessionId = localStorage.getItem(sessionKey);
let loading = false;
let statsDialogLoading = false;
let statsDialogStarted = false;
let pendingRenameSessionId = "";
let pendingRenameTitleElement = null;
let availableVoices = [];
let populatingVoicePreferenceControls = false;
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
let currentTargetPopulationQuestion = null;
let targetPopulationQuestions = [];
let targetPopulationAnswers = [];
let surveyResultsZoom = 1;
let platformUsersZoom = 1;
let autoConversationTimer = null;
let autoConversationTurns = 0;
const autoConversationTurnLimit = appSettings.chat?.autoConversationTurnLimit || 80;
let renderedVisualKey = "";
let renderedStageCardsKey = "";
let stageVisualRenderId = 0;
let stageMapRetryTimer = null;
const stageMapRetryAttempts = new Map();
const mapTopologyCache = new Map();
let optionTooltipElement = null;
let optionTooltipTarget = null;
let sourceCitationTooltipTarget = null;
let listedHazardOptions = [];
let promptRows = [];
let selectedPromptId = "";
let creatingPrompt = false;
let promptSourceLoading = false;

const defaultPlaceholder = "Type a country, region, or sector...";
const panelWidthKey = storageKeys.panelWidth || "dr_transition_visual_panel_width";
const defaultVisualPanelPercent = appSettings.stagePanel?.defaultVisualPanelPercent || 43;
const visualPanelMinPercent = appSettings.stagePanel?.minPercent || 30;
const visualPanelMaxPercent = appSettings.stagePanel?.maxPercent || 62;
const hazardOptionActionLabels = new Set([
  "show hazards added by experts",
  "show co-created hazards",
  "show listed hazards",
].map(normalizeForMatch));
const coverageCountries = stageCoverageRows
  .filter((row) => row.code)
  .map((row) => ({
    code: row.code,
    name: row.coverage,
    mapPath: row.map_path,
    sectors: row.sectors || "Not configured",
    hazards: Number(row.hazards) || 0,
    analyses: Number(row.analyses) || 0,
    regionAnalyses: row.region_analyses || {},
  }));
const countryMapData = new Map(
  coverageCountries.filter((country) => country.mapPath).map((country) => [country.name, country.mapPath]),
);
const coverageByCountryName = new Map(
  coverageCountries.map((country) => [country.name, country]),
);
const stageIconSets = {
  sector: [
    {
      title: "Transport",
      text: "Mobility, access, public transport, and charging infrastructure.",
      icon: "M4 16h16M6 16l2-7h8l2 7M8 16v3M16 16v3M8 11h8M7 7h10",
    },
    {
      title: "Housing",
      text: "Retrofits, affordability, energy performance, and household impacts.",
      icon: "M4 11l8-7 8 7M6 10v9h12v-9M10 19v-5h4v5",
    },
    {
      title: "Energy",
      text: "Clean energy systems, costs, security, and vulnerable customers.",
      icon: "M13 3L5 14h6l-1 7 8-11h-6l1-7z",
    },
  ],
  hazards: [
    {
      title: "Hazards",
      text: "Capture social risks and negative impacts.",
      icon: "M12 3l10 18H2L12 3zM12 9v5M12 17h.01",
    },
    {
      title: "Affected Profiles",
      text: "Connect hazards to vulnerable demographic groups.",
      icon: "M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 21a8 8 0 0116 0",
    },
    {
      title: "Mitigation measures",
      text: "Track the responses developed for the selected hazards.",
      icon: "M7 3h7l4 4v14H7V3zM14 3v5h5M9 13h6M9 17h6",
    },
  ],
  mitigation: [
    {
      title: "Mitigation",
      text: "Turn each hazard into a concrete response.",
      icon: "M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7l7-4z",
    },
    {
      title: "Policy Fit",
      text: "Check how the action addresses the identified impact.",
      icon: "M5 13l4 4L19 7",
    },
    {
      title: "Plan",
      text: "Build a practical mitigation pathway.",
      icon: "M4 6h16M4 12h10M4 18h7",
    },
  ],
  evaluation: [
    {
      title: "Score",
      text: "Rate the strength of the mitigation plan.",
      icon: "M4 19V5M9 19V9M14 19V3M19 19v-7",
    },
    {
      title: "Review",
      text: "Compare rationale, confidence, and evidence.",
      icon: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11",
    },
    {
      title: "Complete",
      text: "Close the analysis with a validated record.",
      icon: "M12 22a10 10 0 110-20 10 10 0 010 20zM8 12l3 3 5-6",
    },
  ],
};
const stageVisuals = {
  country: {
    index: 0,
    title: "Choose the national context",
    text: "The first signal anchors the analysis in one of the supported European countries.",
  },
  region: {
    index: 1,
    title: "Narrow into your region",
    text: "Regional selection lets the evidence and policy impacts become more specific.",
  },
  sector: {
    index: 2,
    title: "Sectors analyzed",
    text: "Transport, housing, and energy pathways change which hazards and profiles matter most.",
  },
  hazards: {
    index: 3,
    title: "",
    text: "",
  },
  mitigation: {
    index: 4,
    title: "Build mitigation options",
    text: "Mitigation planning turns identified hazards into practical countermeasures.",
  },
  evaluation: {
    index: 5,
    title: "Evaluate the plan",
    text: "The final stage reviews strength, confidence, and evidence before the analysis closes.",
  },
};

function stageKeyForStep(step = "", mode = appState.inputMode) {
  if (mode === "mitigation_measure") return "hazards";
  if (["country", "national_scope"].includes(step)) return "country";
  if (["region"].includes(step)) return "region";
  if (["sector"].includes(step)) return "sector";
  if (
    [
      "hazards",
      "add_hazard",
      "reason_confirmation",
      "hazard_profile_selection",
      "socio_demographic_review",
      "add_dgs",
      "stats_deep_dive",
      "target_population_question",
      "mitigation_measure",
      "mitigation_reason",
      "mitigation_evidence_decision",
      "mitigation_evidence",
      "mitigation_duplicate_suggestion",
      "mitigation_duplicate_report",
      "mitigation_clarity",
    ].includes(step)
  ) {
    return "hazards";
  }
  if (step.startsWith("mitigation") || step === "mitigation") return "mitigation";
  if (step.startsWith("evaluation") || step === "complete") return "evaluation";
  return "country";
}

function shouldShowHazardContextOnly(step = appState.currentStep, mode = appState.inputMode) {
  return (
    mode === "mitigation_measure"
    || [
      "mitigation_measure",
      "mitigation_reason",
      "mitigation_evidence_decision",
      "mitigation_evidence",
      "mitigation_duplicate_suggestion",
      "mitigation_duplicate_report",
      "mitigation_clarity",
    ].includes(step)
  );
}

function updateStageVisual(step = "", session = {}, options = appState.currentOptions) {
  appState.currentStep = step;
  appState.currentOptions = options || [];
  renderSelectedHazardContext(session);
  const key = stageKeyForStep(step, appState.inputMode);
  const visual = stageVisuals[key] || stageVisuals.country;
  const showingPracticalConsiderations = shouldShowPracticalConsiderationsVisual(step, session);
  if (stageVisualTitle) {
    stageVisualTitle.textContent = showingPracticalConsiderations
      ? ""
      : visual.title;
  }
  if (stageVisualText) {
    stageVisualText.hidden = showingPracticalConsiderations;
    stageVisualText.textContent = showingPracticalConsiderations
      ? ""
      : key === "sector" ? sectorStageText(session, appState.currentOptions) : visual.text;
  }
  if (stageProgressFill) {
    const percent = (visual.index / Math.max(1, stageSteps.length - 1)) * 100;
    stageProgressFill.style.width = `${percent}%`;
  }
  if (stageProgressCurrent) {
    stageProgressCurrent.textContent = key.charAt(0).toUpperCase() + key.slice(1);
  }
stageSteps.forEach((item, index) => {
    item.classList.toggle("active", index <= visual.index);
    item.classList.toggle("current", item.dataset.stageKey === key);
  });
  document.body.dataset.analysisStage = key;
  renderDynamicStageVisual(key, appState.currentSession, appState.currentOptions);
  updateFloatingStatsButton();
}

stageProgressToggle?.addEventListener("click", () => {
  if (!stageProgress || !stageProgressToggle) return;
  const isCollapsed = stageProgress.classList.toggle("is-collapsed");
  stageProgressToggle.setAttribute("aria-expanded", String(!isCollapsed));
  stageProgressToggle.setAttribute(
    "aria-label",
    isCollapsed ? "Show analysis stages" : "Hide analysis stages",
  );
});

function sectorStageText(session = {}, options = appState.currentOptions) {
  const sectors = sectorItemsForSession(session, options)
    .map((item) => item.title)
    .filter(Boolean);
  if (!sectors.length) return stageVisuals.sector.text;
  return `${formatReadableList(sectors)} ${sectors.length === 1 ? "pathway changes" : "pathways change"} which hazards and profiles matter most.`;
}

function formatReadableList(items = []) {
  if (items.length <= 1) return items[0] || "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

function updateFloatingStatsButton() {
  if (!floatingStatsButton) return;
  const hasSector = Boolean(appState.currentSession?.sector);
  const canOpen = hasSector && !["country", "national_scope", "region", "sector"].includes(appState.currentStep);
  floatingStatsButton.hidden = !canOpen;
}

function showStageMap({ keepIcons = false } = {}) {
  if (stageMap) {
    stageMap.hidden = false;
    restartStageAnimation(stageMap);
  }
  if (stageIconGrid && !keepIcons) stageIconGrid.hidden = true;
  stageMap?.parentElement?.classList.toggle("has-map-summary", keepIcons);
}

function showStageIcons({ keepMap = false } = {}) {
  if (stageMap && !keepMap) stageMap.hidden = true;
  if (stageIconGrid) {
    stageIconGrid.hidden = false;
    restartStageAnimation(stageIconGrid);
  }
  stageMap?.parentElement?.classList.toggle("has-map-summary", keepMap);
}

function restartStageAnimation(element) {
  element.classList.remove("stage-visual-enter");
  void element.offsetWidth;
  element.classList.add("stage-visual-enter");
}

function mapNavigationOptions() {
  return {
    enabled: true,
    enableButtons: true,
    enableMouseWheelZoom: true,
    buttonOptions: {
      align: "right",
      verticalAlign: "top",
      theme: {
        fill: "#ffffff",
        stroke: "#d9dee7",
        r: 6,
        style: {
          color: "#111827",
          fontWeight: "800",
        },
      },
    },
  };
}

function mapChartOptions(topology) {
  return {
    map: topology,
    panning: { enabled: true, type: "xy" },
    panKey: "shift",
    spacing: [0, 0, 0, 0],
  };
}

function stageCountryTooltipWidth() {
  const panelWidth = stageMap?.getBoundingClientRect().width || window.innerWidth;
  return Math.max(240, Math.min(330, panelWidth - 36, window.innerWidth - 48));
}

function clampVisualPanelPercent(percent) {
  return Math.max(visualPanelMinPercent, Math.min(visualPanelMaxPercent, percent));
}

function resizeStageChart() {
  window.Highcharts?.charts?.forEach((chart) => {
    if (chart?.renderTo === stageMap) chart.reflow();
  });
}

function finalizeStageMapRender(visualKey) {
  stageMapRetryAttempts.delete(visualKey);
  window.requestAnimationFrame(resizeStageChart);
  window.setTimeout(resizeStageChart, 120);
}

function scheduleStageMapRetry(expectedStageKey, visualKey, reason = "") {
  const attempts = stageMapRetryAttempts.get(visualKey) || 0;
  if (attempts >= 5) return;
  stageMapRetryAttempts.set(visualKey, attempts + 1);
  window.clearTimeout(stageMapRetryTimer);
  stageMapRetryTimer = window.setTimeout(() => {
    if (stageKeyForStep(appState.currentStep, appState.inputMode) !== expectedStageKey) return;
    if (reason) console.warn(`Retrying ${visualKey}: ${reason}`);
    renderedVisualKey = "";
    renderDynamicStageVisual(expectedStageKey, appState.currentSession, appState.currentOptions);
  }, 220 * (attempts + 1));
}

function applyVisualPanelPercent(percent, persist = true) {
  if (!appShell || window.matchMedia("(max-width: 900px)").matches) return;
  const adjustedPercent = clampVisualPanelPercent(percent);
  appShell.style.setProperty("--visual-panel-width", `${adjustedPercent}%`);
  workspaceResizer?.setAttribute("aria-valuenow", String(Math.round(adjustedPercent)));
  if (persist) localStorage.setItem(panelWidthKey, String(adjustedPercent));
  window.requestAnimationFrame(resizeStageChart);
}

function configureWorkspaceResizer() {
  if (!appShell || !workspaceResizer) return;
  const savedPercent = Number.parseFloat(localStorage.getItem(panelWidthKey) || "");
  applyVisualPanelPercent(
    Number.isFinite(savedPercent) ? savedPercent : defaultVisualPanelPercent,
    false,
  );

  let pointerId = null;

  workspaceResizer.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 900px)").matches) return;
    pointerId = event.pointerId;
    workspaceResizer.setPointerCapture(pointerId);
    document.body.classList.add("is-resizing-panels");
    event.preventDefault();
  });

  workspaceResizer.addEventListener("pointermove", (event) => {
    if (pointerId !== event.pointerId) return;
    const bounds = appShell.getBoundingClientRect();
    if (!bounds.width) return;
    const percent = ((event.clientX - bounds.left) / bounds.width) * 100;
    applyVisualPanelPercent(percent);
  });

  const stopResize = (event) => {
    if (pointerId !== event.pointerId) return;
    workspaceResizer.releasePointerCapture(pointerId);
    pointerId = null;
    document.body.classList.remove("is-resizing-panels");
  };

  workspaceResizer.addEventListener("pointerup", stopResize);
  workspaceResizer.addEventListener("pointercancel", stopResize);
  workspaceResizer.addEventListener("keydown", (event) => {
    const currentPercent =
      Number.parseFloat(appShell.style.getPropertyValue("--visual-panel-width")) ||
      defaultVisualPanelPercent;
    const increment = event.shiftKey ? 5 : 2;
    if (event.key === "ArrowLeft") {
      applyVisualPanelPercent(currentPercent - increment);
    } else if (event.key === "ArrowRight") {
      applyVisualPanelPercent(currentPercent + increment);
    } else if (event.key === "Home") {
      applyVisualPanelPercent(visualPanelMinPercent);
    } else if (event.key === "End") {
      applyVisualPanelPercent(visualPanelMaxPercent);
    } else {
      return;
    }
    event.preventDefault();
  });
}

async function fetchMapTopology(path) {
  if (mapTopologyCache.has(path)) return mapTopologyCache.get(path);
  const response = await fetch(mapTopologyUrl(path));
  if (!response.ok) throw new Error(`Map data failed with status ${response.status}`);
  const topology = await response.json();
  mapTopologyCache.set(path, topology);
  return topology;
}

function mapTopologyUrl(path) {
  const value = String(path || "").trim();
  if (/^https?:\/\//i.test(value) || value.startsWith("/")) return value;
  return `/static/mapdata/${value.replace(/^\/+/, "")}`;
}

async function renderDynamicStageVisual(key, session = {}, options = appState.currentOptions) {
  if (session?.selected_hazard) {
    renderStageIcons(key, session, options);
    return;
  }
  if (key === "country") {
    await renderCountrySelectionMap();
    return;
  }
  if (key === "region" || key === "sector") {
    await renderRegionMap(session.country, session.region);
    return;
  }
  renderStageIcons(key, session, options);
}

async function renderCountrySelectionMap() {
  const visualKey = "country-map";
  if (!stageMap || !window.Highcharts || !europeMapPath) {
    showStageMap();
    scheduleStageMapRetry("country", visualKey, "map dependencies were not ready");
    return;
  }
  if (renderedVisualKey === visualKey) {
    showStageMap();
    resizeStageChart();
    return;
  }
  const renderId = ++stageVisualRenderId;
  showStageMap();

  try {
    const topology = await fetchMapTopology(europeMapPath);
    if (renderId !== stageVisualRenderId) return;
    const coverageByCode = new Map(coverageCountries.map((country) => [country.code, country]));
    const activeCodes = new Set(coverageCountries.map((country) => country.code));
    const data = topology.features.map((feature) => {
      const code = feature.properties["iso-a2"];
      const active = activeCodes.has(code);
      const countryName = coverageByCode.get(code)?.name || feature.properties.name;
      const countryMeta = coverageByCountryName.get(countryName);
      return {
        "hc-key": feature.properties["hc-key"],
        value: active ? 1 : 0,
        color: active ? "#4d4d4d" : "#c7ccd3",
        name: countryName,
        sectors: countryMeta?.sectors || "Not configured",
        hazards: countryMeta?.hazards ?? 0,
        analyses: countryMeta?.analyses ?? 0,
        enabledCountry: active,
      };
    });
    const activeData = data.filter((point) => point.enabledCountry);

    Highcharts.mapChart(stageMap, {
      chart: mapChartOptions(topology),
      title: { text: null },
      credits: { enabled: false },
      legend: { enabled: false },
      mapNavigation: mapNavigationOptions(),
      tooltip: {
        useHTML: true,
        borderWidth: 0,
        padding: 0,
        shadow: false,
        backgroundColor: "transparent",
        outside: false,
        positioner(labelWidth, labelHeight) {
          const chartWidth = this.chart.chartWidth || labelWidth;
          const chartHeight = this.chart.chartHeight || labelHeight;
          return {
            x: Math.max(8, Math.min(18, chartWidth - labelWidth - 8)),
            y: Math.max(8, Math.min(18, chartHeight - labelHeight - 8)),
          };
        },
        formatter() {
          if (!this.point.enabledCountry) return false;
          const country = escapeHtml(this.point.name);
          const sectors = escapeHtml(this.point.sectors);
          const tooltipWidth = stageCountryTooltipWidth();
          return `
            <div class="stage-country-tooltip" style="width: ${tooltipWidth}px; max-width: ${tooltipWidth}px;">
              <div class="stage-country-tooltip-main">
                <span aria-hidden="true"></span>
                <div>
                  <strong>${country}</strong>
                  <p>${sectors.replace(/, /g, " / ")}</p>
                </div>
              </div>
              <div class="stage-country-tooltip-count">
                <small>Analyses</small>
                <strong>${this.point.analyses}</strong>
              </div>
            </div>
          `;
        },
      },
      plotOptions: {
        map: {
          borderColor: "#7a8493",
          borderWidth: 0.45,
          states: { hover: { color: "#6d22c7" } },
        },
      },
      series: [
        {
          name: "Europe",
          data,
          joinBy: "hc-key",
          color: "#c7ccd3",
          nullColor: "#c7ccd3",
          enableMouseTracking: false,
          states: {
            inactive: { enabled: false },
            hover: { enabled: false },
          },
        },
        {
          name: "Country",
          data: activeData,
          joinBy: "hc-key",
          color: "#4d4d4d",
          nullColor: "transparent",
          states: {
            hover: { color: "#6d22c7" },
          },
        },
      ],
    });
    renderedVisualKey = visualKey;
    finalizeStageMapRender(visualKey);
  } catch (error) {
    console.error("Country stage map failed", error);
    renderedVisualKey = "";
    showStageMap();
    scheduleStageMapRetry("country", visualKey, error?.message || "map render failed");
  }
}

async function renderRegionMap(country, region, { keepCards = false } = {}) {
  const countryMapPath = countryMapData.get(country);
  const countryMeta = coverageByCountryName.get(country);
  const visualKey = `region-map-${country}-${region || ""}`;
  const expectedStageKey = stageKeyForStep(appState.currentStep, appState.inputMode);
  if (!stageMap || !window.Highcharts || !countryMapPath) {
    showStageMap();
    scheduleStageMapRetry(expectedStageKey, visualKey, "map dependencies were not ready");
    return;
  }
  if (renderedVisualKey === visualKey) {
    showStageMap({ keepIcons: keepCards });
    resizeStageChart();
    return;
  }
  const renderId = ++stageVisualRenderId;
  showStageMap({ keepIcons: keepCards });

  try {
    const topology = await fetchMapTopology(countryMapPath);
    if (renderId !== stageVisualRenderId) return;
    const selectedRegion = normalizeRegionForMapMatch(region || "");
    const data = topology.features.map((feature) => {
      const name = feature.properties.name || feature.properties.NAME_1 || "";
      const selected = selectedRegion && normalizeRegionForMapMatch(name) === selectedRegion;
      const regionAnalyses = Object.entries(countryMeta?.regionAnalyses || {}).find(
        ([regionName]) => normalizeRegionForMapMatch(regionName) === normalizeRegionForMapMatch(name),
      )?.[1] ?? 0;
      return {
        "hc-key": feature.properties["hc-key"],
        value: selected ? 1 : 0,
        color: selected ? "#6d22c7" : "#c7ccd3",
        name,
        analyses: Number(regionAnalyses) || 0,
      };
    });

    Highcharts.mapChart(stageMap, {
      chart: mapChartOptions(topology),
      title: { text: null },
      credits: { enabled: false },
      legend: { enabled: false },
      mapNavigation: mapNavigationOptions(),
      tooltip: {
        useHTML: true,
        borderWidth: 0,
        padding: 0,
        shadow: false,
        backgroundColor: "transparent",
        formatter() {
          const regionName = escapeHtml(this.point.name);
          const tooltipWidth = stageCountryTooltipWidth();
          return `
            <div class="stage-country-tooltip" style="width: ${tooltipWidth}px; max-width: ${tooltipWidth}px;">
              <div class="stage-country-tooltip-main">
                <span aria-hidden="true"></span>
                <div><strong>${regionName}</strong></div>
              </div>
              <div class="stage-country-tooltip-count">
                <small>Analyses</small>
                <strong>${this.point.analyses}</strong>
              </div>
            </div>
          `;
        },
      },
      plotOptions: {
        map: {
          borderColor: "#7a8493",
          borderWidth: 0.55,
          states: { hover: { color: "#7428d2" } },
        },
      },
      series: [{ name: "Region", data, joinBy: "hc-key", nullColor: "#c7ccd3" }],
    });
    renderedVisualKey = visualKey;
    finalizeStageMapRender(visualKey);
  } catch (error) {
    console.error("Region stage map failed", error);
    renderedVisualKey = "";
    showStageMap({ keepIcons: keepCards });
    scheduleStageMapRetry(expectedStageKey, visualKey, error?.message || "map render failed");
  }
}

function normalizeRegionForMapMatch(value = "") {
  const normalized = normalizeForMatch(
    value.normalize("NFD").replace(/[\u0300-\u036f]/g, ""),
  );
  const aliases = {
    bayern: "bavaria",
    bavaria: "bavaria",
    hessen: "hesse",
    hesse: "hesse",
    niedersachsen: "lower saxony",
    "lower saxony": "lower saxony",
    "nordrhein westfalen": "north rhine westphalia",
    "north rhine westphalia": "north rhine westphalia",
    "rheinland pfalz": "rhineland palatinate",
    "rhineland palatinate": "rhineland palatinate",
    sachsen: "saxony",
    saxony: "saxony",
    "sachsen anhalt": "saxony anhalt",
    "saxony anhalt": "saxony anhalt",
    thuringen: "thuringia",
    thuringia: "thuringia",
  };
  return aliases[normalized] || normalized;
}

function sectorItemsForSession(session = {}, options = []) {
  const labels = options.length
    ? options.map((option) => option.label)
    : (coverageByCountryName.get(session.country)?.sectors || "")
        .split(",")
        .map((sector) => sector.trim())
        .filter(Boolean);
  const sectorIcons = new Map(stageIconSets.sector.map((item) => [normalizeForMatch(item.title), item.icon]));
  return labels.map((label) => ({
    title: label,
    text: ``,
    icon: sectorIcons.get(normalizeForMatch(label)) || "M4 7h16M4 12h16M4 17h16",
  }));
}

function hazardSummaryItems(session = {}) {
  return [
    {
      title: "Hazards",
      text: String(Number(session.hazard_count) || 0),
      icon: "M12 3l10 18H2L12 3zM12 9v5M12 17h.01",
      stat: true,
    },
    {
      title: "Affected profiles",
      text: String(Number(session.affected_profile_count) || 0),
      icon: "M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 21a8 8 0 0116 0",
      stat: true,
    },
    {
      title: "Mitigation measures",
      text: String(Number(session.mitigation_measure_count) || 0),
      icon: "M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7l7-4z",
      stat: true,
    },
  ];
}

function populationPercentage(value) {
  if (value === null || value === undefined || String(value).trim() === "") return "—";
  const percentage = Number(value);
  return Number.isFinite(percentage) ? `${percentage.toFixed(1)}%` : "—";
}

function populationTrendElement(regionalValue, nationalValue) {
  const regional = Number(regionalValue);
  const national = Number(nationalValue);
  if (!Number.isFinite(regional) || !Number.isFinite(national)) return null;
  const difference = regional - national;
  if (Math.abs(difference) < 0.05) {
    return createElement("span", {
      className: "population-trend is-equal",
      text: "•",
      attrs: {
        title: "Equal to national",
        "aria-label": "equal to national",
      },
    });
  }
  const higher = difference > 0;
  return createElement("span", {
    className: `population-trend ${higher ? "is-up" : "is-down"}`,
    text: higher ? "↑" : "↓",
    attrs: {
      title: `${higher ? "Higher" : "Lower"} than national`,
      "aria-label": `${higher ? "higher" : "lower"} than national`,
    },
  });
}

function stageCollapseIcon() {
  return createElement("span", {
    className: "stage-collapse-icon",
    attrs: { "aria-hidden": "true" },
  });
}

function appendStageTableHeader(table) {
  table.appendChild(
    createElement("thead", {}, [
      createElement("tr", {}, [
        createElement("th", { text: "Hazard", attrs: { scope: "col" } }),
        createElement("th", { text: "Regional", attrs: { scope: "col" } }),
        createElement("th", { text: "National", attrs: { scope: "col" } }),
      ]),
    ]),
  );
}

function stageHazardPopulationRow(hazard, index) {
  const hazardLabel = String(hazard.hazard || "Hazard");
  const regionalCell = createElement("td", { text: populationPercentage(hazard.regional_population_pct) });
  const trend = populationTrendElement(hazard.regional_population_pct, hazard.national_population_pct);
  if (trend) regionalCell.appendChild(trend);
  return createElement("tr", {}, [
    createElement("td", {}, [
      createElement("span", { className: "stage-hazard-rank", text: String(index + 1) }),
      createElement("span", {
        className: "stage-hazard-name",
        text: hazardLabel,
        attrs: { title: hazardLabel },
      }),
    ]),
    regionalCell,
    createElement("td", { text: populationPercentage(hazard.national_population_pct) }),
  ]);
}

function stageHazardTableDetails({ className, ariaLabel, label, title, rows }) {
  const tbody = createElement("tbody");
  rows.forEach((hazard, index) => tbody.appendChild(stageHazardPopulationRow(hazard, index)));
  const table = createElement("table");
  appendStageTableHeader(table);
  table.appendChild(tbody);
  return createElement(
    "details",
    { className: `${className} stage-collapsible-section`, attrs: { "aria-label": ariaLabel, open: "" } },
    [
      createElement("summary", { className: `${className}-heading` }, [
        createElement("div", {}, [
          createElement("span", { text: label }),
          createElement("h3", { text: title }),
        ]),
        stageCollapseIcon(),
      ]),
      createElement("div", { className: `${className}-scroll` }, [table]),
    ],
  );
}

function renderHazardPopulationTable(session = {}) {
  const hazards = Array.isArray(session.top_hazards) ? session.top_hazards.slice(0, 3) : [];
  const additionalHazards = additionalHazardPopulationRows(session);
  const counts = [
    ["Hazards", session.hazard_count],
    ["Affected profiles", session.affected_profile_count],
    ["Mitigation measures", session.mitigation_measure_count],
  ];
  clearElement(stageIconGrid);
  if (!session.selected_hazard) {
    stageIconGrid.appendChild(
      createElement(
        "div",
        { className: "stage-hazard-summary", attrs: { "aria-label": "Sector analysis totals" } },
        counts.map(([label, value]) =>
          createElement("article", {}, [
            createElement("strong", { text: String(Number(value) || 0) }),
            createElement("span", { text: label }),
          ]),
        ),
      ),
    );
  }

  const topHazards = stageHazardTableDetails({
    className: "stage-hazard-table",
    ariaLabel: "Top three hazard population comparison",
    label: "Population comparison",
    title: "Top 3 hazards",
    rows: hazards,
  });
  topHazards.querySelector(".stage-hazard-table-heading > div > span")?.appendChild(
    createElement("span", {
      className: "population-comparison-info",
      text: "i",
      attrs: {
        tabindex: "0",
        title:
          "For each hazard, Regional and National values are the arithmetic mean of the mapped affected-profile percentages: sum of available profile percentages divided by number of mapped profiles, rounded to 1 decimal.",
        "aria-label":
          "For each hazard, Regional and National values are the arithmetic mean of the mapped affected-profile percentages: sum of available profile percentages divided by number of mapped profiles, rounded to 1 decimal.",
      },
    }),
  );
  topHazards.querySelector(".stage-hazard-table-heading > div > span")?.classList.add("population-comparison-label");
  stageIconGrid.appendChild(topHazards);

  if (additionalHazards.length) {
    stageIconGrid.appendChild(
      stageHazardTableDetails({
        className: "stage-additional-hazards",
        ariaLabel: "Hazards added by experts",
        label: "Additional hazards",
        title: "Hazards added by experts",
        rows: additionalHazards,
      }),
    );
  }
}

function additionalHazardPopulationRows(session = {}) {
  if (Array.isArray(session.additional_hazard_population) && session.additional_hazard_population.length) {
    return session.additional_hazard_population
      .map((row) => ({
        hazard: String(row?.hazard || "").trim(),
        regional_population_pct: row?.regional_population_pct,
        national_population_pct: row?.national_population_pct,
      }))
      .filter((row) => row.hazard);
  }
  return Array.isArray(session.additional_hazards)
    ? session.additional_hazards
        .map((hazard) => ({
          hazard: String(hazard || "").trim(),
          regional_population_pct: null,
          national_population_pct: null,
        }))
        .filter((row) => row.hazard)
    : [];
}

function shouldShowPracticalConsiderationsVisual(step = appState.currentStep, session = appState.currentSession) {
  const targetPopulationIdentified =
    Array.isArray(session?.benefited_profiles)
    && session.benefited_profiles.some((profile) => String(profile || "").trim());
  return (
    !targetPopulationIdentified
    && Array.isArray(session?.practical_considerations)
    && session.practical_considerations.length > 0
    && [
      "reason_confirmation",
      "mitigation_measure",
      "mitigation_duplicate_suggestion",
      "mitigation_duplicate_report",
      "mitigation_reason",
      "mitigation_clarity",
      "mitigation_evidence_decision",
      "mitigation_evidence",
      "mitigation_target_population",
    ].includes(step)
  );
}

function renderPracticalConsiderationsVisual(session = {}) {
  const items = Array.isArray(session.practical_considerations)
    ? session.practical_considerations
        .map((item) => String(item || "").trim())
        .filter(Boolean)
    : [];
  if (!items.length) return;
  const displayItems = items
    .map((item) => ({
      raw: item,
      title: practicalConsiderationTitle(item),
    }))
    .filter((item) => item.title && !isPracticalPlaceholderText(item.title));
  if (!displayItems.length) return;
  clearElement(stageIconGrid);
  stageIconGrid.appendChild(
    createElement(
      "section",
      {
        className: "practical-considerations-visual",
        attrs: { "aria-label": "General considerations to mitigate the negative effects" },
      },
      [
        createElement(
          "div",
          { className: "practical-visual-orbit", attrs: { "aria-hidden": "true" } },
          [createElement("span"), createElement("span"), createElement("span")],
        ),
        createElement("div", { className: "practical-visual-heading" }, [
          createElement("span", { text: "Design checklist" }),
          createElement("h3", {
            text: `${displayItems.length} general ${displayItems.length === 1 ? "consideration" : "considerations"} to mitigate the negative effects`,
          }),
        ]),
        createElement(
          "ol",
          { className: "practical-consideration-list" },
          displayItems.map((item, index) =>
            createElement(
              "li",
              {
                attrs: {
                  style: `--practical-index: ${index}`,
                  title: item.raw || item.title,
                },
              },
              [
                createElement("span", { className: "practical-step", text: String(index + 1) }),
                createElement("p", { text: item.title }),
              ],
            ),
          ),
        ),
      ],
    ),
  );
}

function practicalConsiderationTitle(value) {
  let text = String(value || "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  const colonIndex = text.indexOf(":");
  if (colonIndex > 6 && colonIndex <= 90) {
    text = text.slice(0, colonIndex);
  } else {
    const sentenceMatch = text.match(/^(.{18,110}?[.!?])\s+/);
    if (sentenceMatch) text = sentenceMatch[1].replace(/[.!?]+$/, "");
  }
  return text.replace(/^[\s\-–—:]+|[\s\-–—:.]+$/g, "") || "Practical consideration";
}

function isPracticalPlaceholderText(value) {
  const normalized = normalizeForMatch(value);
  return (
    normalized === "dynamic theme heading"
    || normalized === "markdown paragraph summarising the theme"
    || normalized === "markdown paragraph summarizing the theme"
    || normalized === "markdown bullet point"
    || normalized.includes("dynamic theme heading")
    || normalized.startsWith("markdown paragraph")
    || normalized.startsWith("markdown bullet")
  );
}

function renderStageIcons(key, session = {}, options = appState.currentOptions, { keepMap = false } = {}) {
  if (!stageIconGrid) return;
  const hazardContextOnly = key === "hazards" && shouldShowHazardContextOnly();
  const practicalVisible = shouldShowPracticalConsiderationsVisual(appState.currentStep, session);
  const mitigationMeasure = String(session?.mitigation_measure || "").trim();
  const mitigationContextOnly = mitigationMeasure && ["mitigation", "evaluation"].includes(key);
  if (mitigationContextOnly) {
    renderedStageCardsKey = `icons-${key}-mitigation-context-hidden`;
    clearElement(stageIconGrid);
    stageIconGrid.hidden = true;
    if (!keepMap && stageMap) stageMap.hidden = true;
    stageMap?.parentElement?.classList.toggle("has-map-summary", false);
    return;
  }
  const topHazardKey = (session.top_hazards || [])
    .map((hazard) => `${hazard.hazard}:${hazard.regional_population_pct}:${hazard.national_population_pct}`)
    .join("|");
  const additionalHazardKey = additionalHazardPopulationRows(session)
    .map((hazard) => `${hazard.hazard}:${hazard.regional_population_pct}:${hazard.national_population_pct}`)
    .join("|");
  const practicalKey = (session.practical_considerations || []).join("|");
  const visualKey = `icons-${key}-${hazardContextOnly && !practicalVisible ? "context-only" : "summary"}-${appState.currentStep}-${session.country || ""}-${session.selected_hazard || ""}-${session.hazard_count || 0}-${session.affected_profile_count || 0}-${session.mitigation_measure_count || 0}-${topHazardKey}-${additionalHazardKey}-${practicalKey}-${options.map((option) => option.label).join("|")}`;
  if (renderedStageCardsKey === visualKey) return;
  renderedStageCardsKey = visualKey;
  if (!keepMap) stageVisualRenderId += 1;
  if (hazardContextOnly && !practicalVisible) {
    clearElement(stageIconGrid);
    stageIconGrid.hidden = true;
    if (!keepMap && stageMap) stageMap.hidden = true;
    stageMap?.parentElement?.classList.toggle("has-map-summary", false);
    return;
  }
  showStageIcons({ keepMap });

  if (practicalVisible) {
    renderPracticalConsiderationsVisual(session);
    return;
  }

  if (key === "hazards" && Array.isArray(session.top_hazards) && session.top_hazards.length) {
    renderHazardPopulationTable(session);
    return;
  }

  const items =
    (key === "sector" && sectorItemsForSession(session, options).length
      ? sectorItemsForSession(session, options)
      : null) ||
    (key === "hazards" ? hazardSummaryItems(session) : null) ||
    stageIconSets[key] ||
    [
      {
        title: "Country",
        text: "Start with one of the six supported European countries.",
        icon: "M3 6h18M3 12h18M3 18h18M7 3a17 17 0 000 18M17 3a17 17 0 010 18",
      },
      {
        title: "Region",
        text: "Move from national context into a more specific regional analysis.",
        icon: "M12 21s7-5.2 7-11a7 7 0 10-14 0c0 5.8 7 11 7 11zM12 10h.01",
      },
      {
        title: "Sector",
        text: "Select the policy sector that shapes the transition pathway.",
        icon: "M4 7h16M4 12h16M4 17h16",
      },
    ];

  clearElement(stageIconGrid);
  items.forEach((item, index) => {
    const article = createElement("article", {
      className: `stage-icon-card${item.stat ? " stage-stat-card" : ""}`,
      attrs: { style: `--stage-card-index: ${index}` },
    });
    if (!item.stat) article.appendChild(stageIconElement(item.icon));
    article.appendChild(
      createElement("p", {
        className: item.stat ? "stage-stat-value" : "",
        text: String(item.text || ""),
      }),
    );
    article.appendChild(createElement("h3", { text: String(item.title || "") }));
    stageIconGrid.appendChild(article);
  });
}

function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  availableVoices = window.speechSynthesis.getVoices();
  populateVoicePreferenceControls();
}

function voiceLabel(voice) {
  const name = String(voice?.name || "Browser voice").trim();
  const lang = String(voice?.lang || "").trim();
  return lang ? `${name} (${lang})` : name;
}

function currentVoiceLanguage() {
  return localStorage.getItem(voiceLanguageKey) || voiceLanguageSelect?.value || navigator.language || "en-US";
}

function currentVoiceRate() {
  const value = Number.parseFloat(localStorage.getItem(voiceRateKey) || speechRateInput?.value || "1");
  return Number.isFinite(value) ? Math.min(1.6, Math.max(0.6, value)) : 1;
}

function currentVoiceVolume() {
  const value = Number.parseFloat(localStorage.getItem(voiceVolumeKey) || speechVolumeInput?.value || "1");
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1;
}

function voicePreferenceValue() {
  return localStorage.getItem(voicePreferenceKey) || voicePreferenceSelect?.value || "auto";
}

function populateVoicePreferenceControls() {
  if (populatingVoicePreferenceControls) return;
  populatingVoicePreferenceControls = true;
  const speechSupported = "speechSynthesis" in window;
  try {
    const savedVoice = voicePreferenceValue();
    const savedLanguage = currentVoiceLanguage();
    if (voiceLanguageSelect) {
      const languages = Array.from(
        new Set([
          savedLanguage,
          navigator.language || "en-US",
          "en-US",
          ...availableVoices.map((voice) => voice.lang).filter(Boolean),
        ]),
      ).sort((a, b) => a.localeCompare(b));
      clearElement(voiceLanguageSelect);
      languages.forEach((language) => {
        voiceLanguageSelect.appendChild(
          createElement("option", {
            text: languageDisplayName(language),
            attrs: { value: language },
          }),
        );
      });
      voiceLanguageSelect.value = languages.includes(savedLanguage) ? savedLanguage : "en-US";
      voiceLanguageSelect.disabled = !speechSupported;
    }
    if (voicePreferenceSelect) {
      clearElement(voicePreferenceSelect);
      voicePreferenceSelect.appendChild(
        createElement("option", {
          text: "Automatic browser voice",
          attrs: { value: "auto" },
        }),
      );
      const language = voiceLanguageSelect?.value || savedLanguage;
      const matchingVoices = availableVoices.filter((voice) => !language || voice.lang === language);
      const voices = matchingVoices.length ? matchingVoices : availableVoices;
      voices.forEach((voice) => {
        voicePreferenceSelect.appendChild(
          createElement("option", {
            text: voiceLabel(voice),
            attrs: { value: voice.voiceURI || voice.name },
          }),
        );
      });
      const optionValues = Array.from(voicePreferenceSelect.options).map((option) => option.value);
      voicePreferenceSelect.value = optionValues.includes(savedVoice) ? savedVoice : "auto";
      voicePreferenceSelect.disabled = !speechSupported;
    }
    if (speechRateInput) {
      speechRateInput.value = String(currentVoiceRate());
      speechRateInput.disabled = !speechSupported;
    }
    if (speechVolumeInput) {
      speechVolumeInput.value = String(currentVoiceVolume());
      speechVolumeInput.disabled = !speechSupported;
    }
    if (previewVoiceButton) previewVoiceButton.disabled = !speechSupported;
    updateVoicePreferenceDisplay();
  } finally {
    populatingVoicePreferenceControls = false;
  }
}

function languageDisplayName(language) {
  const value = String(language || "en-US");
  try {
    const displayNames = new Intl.DisplayNames([navigator.language || "en"], { type: "language" });
    const [languageCode, region] = value.split("-");
    const name = displayNames.of(languageCode) || value;
    return region ? `${name} (${region})` : name;
  } catch {
    return value;
  }
}

function updateVoicePreferenceDisplay() {
  if (speechRateValue) speechRateValue.textContent = `${currentVoiceRate().toFixed(1)}x`;
  if (speechVolumeValue) speechVolumeValue.textContent = `${Math.round(currentVoiceVolume() * 100)}%`;
  if (!voicePreferenceSummary) return;
  const voice = populatingVoicePreferenceControls ? null : selectedVoice();
  const summary = voice ? voiceLabel(voice) : languageDisplayName(currentVoiceLanguage());
  voicePreferenceSummary.textContent = summary;
}

function selectedVoice() {
  if (!availableVoices.length) loadVoices();
  const preference = voicePreferenceValue();
  const language = currentVoiceLanguage();
  const languageVoices = availableVoices.filter((voice) => voice.lang === language);
  const englishVoices = availableVoices.filter((voice) => voice.lang?.startsWith("en"));
  const voices = languageVoices.length ? languageVoices : englishVoices.length ? englishVoices : availableVoices;
  if (!voices.length) return null;

  const exactVoice = voices.find((voice) => voice.voiceURI === preference || voice.name === preference);
  if (exactVoice) return exactVoice;
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

function previewSelectedVoice() {
  if (!("speechSynthesis" in window)) return;
  pauseSpeech();
  const utterance = new SpeechSynthesisUtterance("This is a sample of the selected assistant voice.");
  const voice = selectedVoice();
  if (voice) utterance.voice = voice;
  utterance.lang = currentVoiceLanguage();
  utterance.rate = currentVoiceRate();
  utterance.volume = currentVoiceVolume();
  utterance.pitch = 1;
  window.speechSynthesis.speak(utterance);
}

function voiceAssistantEnabled() {
  return Boolean(voiceAssistantToggle?.checked);
}

function typingEffectEnabled() {
  return typingEffectToggle ? typingEffectToggle.checked : true;
}

function configureTypingEffectControl() {
  if (!typingEffectToggle) return;
  const saved = localStorage.getItem(typingEffectKey);
  typingEffectToggle.checked = saved === null ? true : saved === "true";
}

function currentValidationMode() {
  return localStorage.getItem(validationModeKey) === "easy" ? "easy" : "strict";
}

function configureValidationModeControl() {
  if (!validationModeToggle) return;
  const mode = currentValidationMode();
  validationModeToggle.checked = mode === "strict";
  if (validationModeLabel) {
    validationModeLabel.textContent = mode === "strict" ? "Strict" : "Easy";
  }
}

function crowdSourcingEnabled() {
  return localStorage.getItem(crowdSourcingKey) === "true";
}

function configureCrowdSourcingControl() {
  if (!crowdSourcingToggle) return;
  crowdSourcingToggle.checked = crowdSourcingEnabled();
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
  const now = performance.now();
  const boundaryAge = now - voiceAnalyzerLastBoundaryAt;
  const decay = active ? Math.exp(-boundaryAge / 260) : 0;
  voiceAnalyzerLevel += (voiceAnalyzerTarget * decay - voiceAnalyzerLevel) * 0.28;
  if (active && boundaryAge > 360) {
    voiceAnalyzerTarget *= 0.82;
  }

  ctx.clearRect(0, 0, width, height);

  const clusters = [
    { center: 0.16, spread: 0.06, power: 0.95 },
    { center: 0.31, spread: 0.045, power: 0.72 },
    { center: 0.47, spread: 0.055, power: 1 },
    { center: 0.68, spread: 0.045, power: 0.62 },
    { center: 0.84, spread: 0.07, power: 0.86 },
  ];
  const barCount = Math.max(28, Math.floor(width / 3.8));
  const gap = width / (barCount + 2);
  const barWidth = Math.max(2, Math.min(4, gap * 0.58));
  const baseline = height - 4;
  const maxBarHeight = height - 8;
  const speechLevel = active ? Math.max(0.16, Math.min(1, voiceAnalyzerLevel)) : 0.42;

  ctx.save();
  ctx.shadowColor = active ? "rgba(56, 189, 248, 0.34)" : "rgba(59, 130, 246, 0.2)";
  ctx.shadowBlur = active ? 7 : 4;
  ctx.fillStyle = "rgba(18, 75, 216, 0.88)";
  ctx.fillRect(0, baseline, width, 2);

  for (let index = 0; index < barCount; index += 1) {
    const x = gap * (index + 1.4);
    const normalizedX = x / width;
    let envelope = 0.08;
    clusters.forEach((cluster) => {
      envelope +=
        cluster.power *
        Math.exp(-((normalizedX - cluster.center) ** 2) / (2 * cluster.spread ** 2));
    });
    const wordFocus = Math.exp(-((normalizedX - voiceAnalyzerProgress) ** 2) / (2 * 0.07 ** 2));
    const flicker = 0.74 + 0.26 * Math.sin(now / 170 + index * 1.37);
    const speechShape = 0.46 + speechLevel * 0.3 + wordFocus * speechLevel * 0.42;
    const barHeight = Math.max(
      3,
      Math.min(maxBarHeight, envelope * maxBarHeight * speechShape * flicker),
    );
    const barGradient = ctx.createLinearGradient(0, baseline - barHeight, 0, baseline);
    if (index % 5 === 0) {
      barGradient.addColorStop(0, "#67e8f9");
      barGradient.addColorStop(0.42, "#22d3ee");
      barGradient.addColorStop(1, "#1d4ed8");
    } else if (index % 4 === 0) {
      barGradient.addColorStop(0, "#d8b4fe");
      barGradient.addColorStop(0.45, "#a855f7");
      barGradient.addColorStop(1, "#2563eb");
    } else {
      barGradient.addColorStop(0, "#60a5fa");
      barGradient.addColorStop(0.5, "#2563eb");
      barGradient.addColorStop(1, "#1e40af");
    }
    ctx.fillStyle = barGradient;
    roundRect(ctx, x - barWidth / 2, baseline - barHeight, barWidth, barHeight, 0.8);
    ctx.fill();
  }
  ctx.shadowBlur = 0;
  ctx.restore();
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

function speakServerMessage(html, voiceSummary = "") {
  if (!voiceAssistantToggle?.checked || !("speechSynthesis" in window)) return;
  const text = String(voiceSummary || "").trim() || (typeof voiceSummaryFromHtml === "function"
    ? voiceSummaryFromHtml(html)
    : plainTextFromHtml(html));
  if (!text) return;
  pauseSpeech();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = selectedVoice();
  if (voice) utterance.voice = voice;
  utterance.lang = currentVoiceLanguage();
  utterance.rate = currentVoiceRate();
  utterance.volume = currentVoiceVolume();
  utterance.pitch = 1;
  utterance.onstart = () => startVoiceAnalyzer(text);
  utterance.onboundary = (event) => syncVoiceAnalyzerToSpeech(event, text);
  utterance.onend = stopVoiceAnalyzer;
  utterance.onerror = stopVoiceAnalyzer;
  window.speechSynthesis.speak(utterance);
}

function configureVoiceControls() {
  const speechSupported = "speechSynthesis" in window;
  if (!voiceAssistantToggle) return;
  voiceAssistantToggle.checked = localStorage.getItem(voiceEnabledKey) === "true";
  if (autoConversationToggle) {
    autoConversationToggle.checked = localStorage.getItem(autoConversationKey) === "true";
  }
  voiceAssistantToggle.disabled = !speechSupported;
  populateVoicePreferenceControls();
  syncVoicePreferenceVisibility();
  syncVoiceAnalyzerVisibility();
  if (speechSupported) {
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
  }
}

function syncVoicePreferenceVisibility() {
  if (!voicePreferenceButton) return;
  const visible = Boolean(voiceAssistantToggle?.checked);
  voicePreferenceButton.hidden = !visible;
  if (!visible && voicePreferenceDialog?.open) {
    closeVoicePreferenceDialog();
  }
}

function openSettingsDrawer() {
  if (!settingsDrawer || !settingsButton) return;
  settingsDrawer.hidden = false;
  settingsButton.setAttribute("aria-expanded", "true");
}

function closeSettingsDrawer() {
  if (!settingsDrawer || !settingsButton) return;
  settingsDrawer.hidden = true;
  settingsButton.setAttribute("aria-expanded", "false");
}

function openVoicePreferenceDialog() {
  if (!voicePreferenceDialog) return;
  if (!voiceAssistantEnabled()) return;
  populateVoicePreferenceControls();
  if (typeof voicePreferenceDialog.showModal === "function") {
    voicePreferenceDialog.showModal();
  } else {
    voicePreferenceDialog.setAttribute("open", "");
  }
}

function closeVoicePreferenceDialog() {
  if (!voicePreferenceDialog) return;
  if (typeof voicePreferenceDialog.close === "function") {
    voicePreferenceDialog.close();
  } else {
    voicePreferenceDialog.removeAttribute("open");
  }
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

function hasPendingCustomProfileReason(session = appState.currentSession) {
  return Boolean(String(session?.custom_hazard?.pending_profile_reason_group || "").trim());
}

function placeholderForStep(step, options = [], session = appState.currentSession) {
  if (step === "custom_hazard_profile_reason" || hasPendingCustomProfileReason(session)) {
    const group = String(session?.custom_hazard?.pending_profile_reason_group || "").trim();
    return group
      ? `Explain how this hazard affects ${group}...`
      : "Explain how this hazard affects the added group...";
  }
  if (options.length) {
    const optionLabels = options.map((option) => option.label);
    if (step === "system_inquiry_observation" || step === "system_inquiry_followup") {
      return "Write your reflection, or choose an option above...";
    }
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
    if (step === "mitigation_evidence") {
      return "Add evidence or choose Skip...";
    }
    if (step === "mitigation_clarity") {
      return "Answer all clarification questions...";
    }
    if (step === "mitigation_review") {
      return "Ask about this mitigation, or move to next step...";
    }
    if (step === "custom_hazard_group_review") {
      return "Type a group to remove, or add/edit an affected group...";
    }
    if (step === "target_population_question" || step === "add_dgs") {
      return "Choose a socio-demographic option...";
    }
    if (optionLabels.includes("Move to next step")) {
      return "Choose one of the options above...";
    }
    return "Select an option above, or type your answer...";
  }

  const placeholders = {
    add_dgs: "Choose socio-demographic options...",
    hazards: "Type the hazard you want to add...",
    custom_hazard_group_review: "Type a group to remove, or add/edit an affected group...",
    custom_hazard_profile_reason: "Explain how this hazard affects the added group...",
    mitigation: "Ask a mitigation question or continue the plan...",
    mitigation_clarity: "Answer all clarification questions...",
    system_inquiry_observation: "Write your reflection...",
    system_inquiry_followup: "Write your follow-up reflection...",
    evaluation_question: "Use the score slider below...",
    complete: "Ask a follow-up question...",
    country: "Type or select a country...",
    region: "Type or select a region...",
    sector: "Type or select a sector...",
  };

  return placeholders[step] || defaultPlaceholder;
}

function setReasonEvidencePlaceholders(step, mode = "reason_evidence") {
  if (mode === "mitigation_measure") {
    primaryInputLabel.textContent = "Mitigation measure";
    reasonInput.placeholder = "What mitigation measure should be used?";
    reasonInput.closest("label").hidden = false;
    secondaryReasonInput.closest("label").hidden = true;
    evidenceUrlField.hidden = true;
    evidenceFileField.hidden = true;
    return;
  }

  primaryInputLabel.textContent = "Reason/Justification";
  secondaryInputLabel.textContent = "Reason/Justification";
  secondaryReasonInput.closest("label").hidden = true;
  reasonInput.closest("label").hidden = mode === "evidence_only";
  evidenceUrlField.hidden = mode === "reason_only";
  evidenceFileField.hidden = mode === "reason_only";

  if (mode === "evidence_only") {
    evidenceInput.placeholder = step === "mitigation_evidence"
      ? "https://example.org/mitigation-evidence"
      : "https://example.org/hazard-evidence";
    return;
  }

  if (step === "socio_demographic_review") {
    primaryInputLabel.textContent = "Reason/Justification (optional)";
    reasonInput.placeholder = "Why should these DGs be treated as severely affected?";
    evidenceInput.placeholder = "https://example.org/demographic-evidence";
    return;
  }

  if (step === "mitigation_reason") {
    reasonInput.placeholder = "Why is this mitigation measure appropriate?";
    evidenceInput.placeholder = "https://example.org/mitigation-evidence";
    return;
  }

  reasonInput.placeholder = "Why should this be treated as a hazard?";
  evidenceInput.placeholder = "https://example.org/hazard-evidence";
}

function isQuickSelectPopulationLabel(label) {
  return ["Quick Select Target Population", "Quick Select Affected Population Group"].includes(label);
}

function isTargetPopulationActionLabel(label) {
  return ["Skip", "Skip all"].includes(label) || isQuickSelectPopulationLabel(label);
}

function updateOptionHighlight() {
  appState.highlightedOptionLabel = "";
  const query = messageInput.value.trim();
  const buttons = Array.from(optionTray.querySelectorAll("button"));
  buttons.forEach((button) => button.classList.remove("fuzzy-match"));

  if (!query || appState.inputMode !== "text" || !buttons.length) return;

  const best = buttons.reduce(
    (match, button) => {
      const score = fuzzyScore(query, button.textContent);
      return score > match.score ? { button, score } : match;
    },
    { button: null, score: 0 },
  );

  if (best.button && best.score >= 0.45) {
    best.button.classList.add("fuzzy-match");
    appState.highlightedOptionLabel = best.button.textContent;
  }
}

function nowLabel() {
  return new Intl.DateTimeFormat([], {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

function scrollToBottom(targetLog = chatLog) {
  if (!targetLog) return;
  targetLog.scrollTop = targetLog.scrollHeight;
  if (targetLog === chatLog) updateChatScrollBottomButton();
}

function updateChatScrollBottomButton() {
  if (!chatLog || !chatScrollBottomButton) return;
  const distanceFromBottom = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight;
  chatScrollBottomButton.hidden = distanceFromBottom <= 100;
}

chatLog?.addEventListener("scroll", updateChatScrollBottomButton, { passive: true });
chatScrollBottomButton?.addEventListener("click", () => {
  chatLog?.scrollTo({ top: chatLog.scrollHeight, behavior: "smooth" });
});

chatLog?.addEventListener("click", (event) => {
  const platformUsersButton = event.target.closest("[data-open-platform-users], .platform-users-source-button");
  if (platformUsersButton) {
    event.preventDefault();
    openPlatformUsersDialog();
    return;
  }
  const surveyResultsButton = event.target.closest("[data-open-survey-results], .survey-source-button");
  if (surveyResultsButton) {
    event.preventDefault();
    openSurveyResultsDialog();
    return;
  }
  const headingLabel = event.target.closest(".hazard-group-heading > span");
  const headingLabelText = normalizeForMatch(headingLabel?.textContent || "");
  if (headingLabelText === "platform users") {
    event.preventDefault();
    openPlatformUsersDialog();
    return;
  }
  if (headingLabelText === "from the survey") {
    event.preventDefault();
    openSurveyResultsDialog();
    return;
  }
  const methodologyButton = event.target.closest("[data-open-methodology]");
  if (!methodologyButton) return;
  event.preventDefault();
  openMethodologyDialog();
});

function collapseExpandedMessages(targetLog = chatLog) {
  if (!targetLog) return;
  targetLog.querySelectorAll(".bubble.is-collapsible.is-expanded").forEach((bubble) => {
    setCollapsibleBubbleExpanded(bubble, false);
  });
}

function setCollapsibleBubbleExpanded(bubble, expanded) {
  if (!bubble?.classList.contains("is-collapsible")) return;
  bubble.classList.toggle("is-expanded", expanded);
  const toggle = bubble.querySelector(".bubble-toggle");
  if (toggle) toggle.textContent = expanded ? "Show less" : "Show more";
}

function syncCollapsibleMessages(targetLog = chatLog) {
  if (!targetLog) return;
  const messageBubbles = Array.from(targetLog.querySelectorAll(".message-row .bubble"));
  const latestBubble = messageBubbles.at(-1);
  messageBubbles.forEach((bubble) => {
    if (!bubble.classList.contains("is-collapsible")) return;
    setCollapsibleBubbleExpanded(bubble, bubble === latestBubble);
  });
}

function flashRequiredField(field) {
  if (!field) return;
  field.focus();
  field.classList.remove("field-required-flash");
  void field.offsetWidth;
  field.classList.add("field-required-flash");
  window.setTimeout(() => field.classList.remove("field-required-flash"), 2400);
}

function parseChartData(element, attribute) {
  try {
    const value = JSON.parse(element.dataset[attribute] || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function renderRadarCategoryRing(chart, categories) {
  chart.parentCategoryRing?.destroy();
  if (!chart.pane?.[0]?.center || !categories.length) return;

  const groups = [];
  categories.forEach((category, index) => {
    const label = String(category || "Evaluation");
    const previous = groups.at(-1);
    if (previous?.label === label) previous.end = index;
    else groups.push({ label, start: index, end: index });
  });

  const [paneX, paneY, paneSize] = chart.pane[0].center;
  const centerX = chart.plotLeft + paneX;
  const centerY = chart.plotTop + paneY;
  const radarRadius = paneSize / 2;
  const outerRadius = radarRadius + 65;
  const step = (Math.PI * 2) / categories.length;
  const firstAxisAngle = -Math.PI / 2;
  const ring = chart.renderer.g("radar-parent-category-ring").attr({ zIndex: 3 }).add();
  const categoryColors = ["#7c3aed", "#0891b2", "#ea580c", "#16a34a", "#db2777", "#2563eb"];

  groups.forEach((group, groupIndex) => {
    const color = categoryColors[groupIndex % categoryColors.length];
    const categoryGap = 0.035;
    const startAngle = firstAxisAngle + (group.start - 0.5) * step + categoryGap;
    const endAngle = firstAxisAngle + (group.end + 0.5) * step - categoryGap;
    const middleAngle = (startAngle + endAngle) / 2;
    const startX = centerX + Math.cos(startAngle) * outerRadius;
    const startY = centerY + Math.sin(startAngle) * outerRadius;
    const endX = centerX + Math.cos(endAngle) * outerRadius;
    const endY = centerY + Math.sin(endAngle) * outerRadius;
    chart.renderer
      .path([
        "M", startX, startY,
        "A", outerRadius, outerRadius, 0, endAngle - startAngle > Math.PI ? 1 : 0, 1, endX, endY,
      ])
      .attr({
        fill: "none",
        stroke: color,
        "stroke-linecap": "round",
        "stroke-width": 3,
      })
      .add(ring);

    [startAngle, endAngle].forEach((angle) => {
      const tickInner = outerRadius - 7;
      const tickOuter = outerRadius + 7;
      chart.renderer
        .path([
          "M", centerX + Math.cos(angle) * tickInner, centerY + Math.sin(angle) * tickInner,
          "L", centerX + Math.cos(angle) * tickOuter, centerY + Math.sin(angle) * tickOuter,
        ])
        .attr({ stroke: color, "stroke-linecap": "round", "stroke-width": 3 })
        .add(ring);
    });

    const labelRadius = outerRadius + 32;
    const isLowerArc = Math.sin(middleAngle) > 0;
    const textStartAngle = isLowerArc ? endAngle : startAngle;
    const textEndAngle = isLowerArc ? startAngle : endAngle;
    const textStartX = centerX + Math.cos(textStartAngle) * labelRadius;
    const textStartY = centerY + Math.sin(textStartAngle) * labelRadius;
    const textEndX = centerX + Math.cos(textEndAngle) * labelRadius;
    const textEndY = centerY + Math.sin(textEndAngle) * labelRadius;
    const guideId = `radar-category-path-${chart.index}-${groupIndex}`;
    const textGuide = chart.renderer
      .path([
        "M", textStartX, textStartY,
        "A", labelRadius, labelRadius, 0, endAngle - startAngle > Math.PI ? 1 : 0,
        isLowerArc ? 0 : 1, textEndX, textEndY,
      ])
      .attr({
        fill: "none",
        id: guideId,
        stroke: "none",
      })
      .add(ring);
    const svgNamespace = "http://www.w3.org/2000/svg";
    const xlinkNamespace = "http://www.w3.org/1999/xlink";
    const textElement = document.createElementNS(svgNamespace, "text");
    const textPathElement = document.createElementNS(svgNamespace, "textPath");
    textElement.setAttribute("dy", "-2");
    textElement.setAttribute("fill", color);
    textElement.setAttribute("font-family", "Arial, sans-serif");
    textElement.setAttribute("font-size", "13px");
    textElement.setAttribute("font-weight", "800");
    textElement.setAttribute("letter-spacing", "0.2px");
    textElement.setAttribute("paint-order", "stroke");
    textElement.setAttribute("stroke", "#ffffff");
    textElement.setAttribute("stroke-linejoin", "round");
    textElement.setAttribute("stroke-width", "2");
    textPathElement.setAttribute("href", `#${guideId}`);
    textPathElement.setAttributeNS(xlinkNamespace, "xlink:href", `#${guideId}`);
    textPathElement.setAttribute("startOffset", "50%");
    textPathElement.setAttribute("text-anchor", "middle");
    textPathElement.textContent = group.label;
    textElement.appendChild(textPathElement);
    ring.element.appendChild(textElement);
  });

  chart.parentCategoryRing = ring;
}

function rotateRadarAxisLabels(chart, labelCount) {
  const ticks = chart.xAxis?.[0]?.ticks || {};
  Object.values(ticks).forEach((tick) => {
    if (!tick?.label || !Number.isFinite(Number(tick.pos))) return;
    let rotation = (Number(tick.pos) * 360) / labelCount;
    while (rotation > 90) rotation -= 180;
    while (rotation < -90) rotation += 180;
    tick.label
      .attr({ rotation })
      .css({ color: "#475569", fontWeight: "500", textOutline: "4px #ffffff" });
  });
}

function renderMitigationVennFallback(chart, affected, mitigation, overlap) {
  chart.mitigationVennGroup?.destroy();
  const group = chart.renderer.g("mitigation-venn-fallback").attr({ zIndex: 2 }).add();
  const centerX = chart.plotLeft + chart.plotWidth / 2;
  const centerY = chart.plotTop + chart.plotHeight / 2 + 12;
  const radius = Math.min(chart.plotWidth, chart.plotHeight) * 0.3;
  const offset = radius * 0.48;

  chart.renderer.circle(centerX - offset, centerY, radius).attr({
    fill: "rgba(8, 145, 178, 0.48)", stroke: "#0e7490", "stroke-width": 2,
  }).add(group);
  chart.renderer.circle(centerX + offset, centerY, radius).attr({
    fill: "rgba(124, 58, 237, 0.43)", stroke: "#6d28d9", "stroke-width": 2,
  }).add(group);

  const addCenteredText = (textValue, x, y, style = {}) => {
    const label = chart.renderer.text(textValue, x, y).css({
      color: "#0f172a", fontSize: "12px", fontWeight: "700", textOutline: "3px #ffffff", ...style,
    }).add(group);
    label.attr({ x: x - label.getBBox().width / 2 });
  };
  addCenteredText("Hazard profiles", centerX - offset * 1.45, centerY - 8);
  addCenteredText(String(affected.length), centerX - offset * 1.45, centerY + 14, { fontSize: "16px" });
  addCenteredText("Shared", centerX, centerY - 8);
  addCenteredText(String(overlap.length), centerX, centerY + 14, { fontSize: "16px" });
  addCenteredText("Mitigation measure", centerX + offset * 1.45, centerY - 8);
  addCenteredText(String(mitigation.length), centerX + offset * 1.45, centerY + 14, { fontSize: "16px" });
  chart.mitigationVennGroup = group;
}

function groupedTargetPopulationLabels(labels = []) {
  const grouped = new Map();
  const passthrough = [];
  labels.map(String).map((label) => label.trim()).filter(Boolean).forEach((label) => {
    if (!label.includes(":")) {
      passthrough.push(label);
      return;
    }
    const [questionPart, ...answerParts] = label.split(":");
    const question = questionPart.trim();
    const answer = answerParts.join(":").trim();
    if (!question || !answer) {
      passthrough.push(label);
      return;
    }
    if (!grouped.has(question)) grouped.set(question, []);
    const answers = grouped.get(question);
    if (!answers.some((item) => normalizeForMatch(item) === normalizeForMatch(answer))) {
      answers.push(answer);
    }
  });
  return [
    ...Array.from(grouped, ([question, answers]) => `${question}: ${answers.join(", ")}`),
    ...passthrough,
  ];
}

function targetPopulationComparableText(label) {
  const value = String(label || "").trim();
  if (!value.includes(":")) return value;
  const [, ...answerParts] = value.split(":");
  return answerParts.join(":").trim() || value;
}

function meaningfulPopulationKey(value) {
  const tokens = normalizeForMatch(value)
    .split(/\s+/)
    .filter(Boolean)
    .filter((token) => ![
      "a",
      "an",
      "and",
      "by",
      "group",
      "groups",
      "in",
      "of",
      "or",
      "people",
      "person",
      "persons",
      "population",
      "populations",
      "the",
      "with",
    ].includes(token));
  if (tokens.length >= 2 || tokens.some((token) => token.length >= 6)) {
    return tokens.join(" ");
  }
  return "";
}

function addPopulationKey(keys, value) {
  const normalized = normalizeForMatch(value);
  if (normalized) keys.add(normalized);
  const meaningful = meaningfulPopulationKey(value);
  if (meaningful) keys.add(meaningful);
}

function compoundPopulationParts(label) {
  const text = targetPopulationComparableText(label);
  const parts = text
    .split(/\s+(?:and|or)\s+|[,;/]+/i)
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length <= 1) return [];

  const prefixMatch = text.match(
    /^(people|persons|person|residents|households|workers|individuals)\s+(?:in|with|from|of|affected by|living in)?\s+/i,
  );
  const prefix = prefixMatch?.[0] || "";
  return parts.flatMap((part, index) => {
    const values = [part];
    if (index > 0 && prefix && !new RegExp(`^${prefix}`, "i").test(part)) {
      values.push(`${prefix}${part}`);
    }
    return values;
  });
}

function populationOverlapKeys(label) {
  const keys = new Set();
  addPopulationKey(keys, targetPopulationComparableText(label));
  compoundPopulationParts(label).forEach((part) => addPopulationKey(keys, part));
  return keys;
}

function populationLabelsOverlap(leftLabel, rightLabel) {
  const leftKeys = populationOverlapKeys(leftLabel);
  const rightKeys = populationOverlapKeys(rightLabel);
  return Array.from(leftKeys).some((key) => rightKeys.has(key));
}

function overlappingAffectedPopulationLabels(affected, mitigation) {
  const overlap = [];
  affected.forEach((affectedLabel) => {
    if (mitigation.some((mitigationLabel) => populationLabelsOverlap(affectedLabel, mitigationLabel))) {
      overlap.push(affectedLabel);
    }
  });
  return overlap;
}

function initializeHighcharts(root = document) {
  if (!root?.querySelectorAll) return;
  const metricTiles = Array.from(root.querySelectorAll(".metric-tile:not([data-bar-ready])"));
  const maxMetric = Math.max(1, ...metricTiles.map((element) => Number(element.dataset.value) || 0));
  metricTiles.forEach((element) => {
    const percentage = Math.max(0, Math.min(100, ((Number(element.dataset.value) || 0) / maxMetric) * 100));
    element.style.setProperty("--metric-fill", `${percentage}%`);
    element.dataset.barReady = "true";
  });

  if (!window.Highcharts) return;

  root.querySelectorAll(".js-mitigation-venn-chart:not([data-chart-ready])").forEach((element) => {
    const affected = parseChartData(element, "affected").map(String);
    const mitigation = parseChartData(element, "mitigation").map(String);
    if (!affected.length || !mitigation.length) return;
    const overlap = overlappingAffectedPopulationLabels(affected, mitigation);
    const affectedDisplay = groupedTargetPopulationLabels(affected);
    const mitigationDisplay = groupedTargetPopulationLabels(mitigation);
    const overlapDisplay = groupedTargetPopulationLabels(overlap);
    element.dataset.chartReady = "true";
    if (!Highcharts.seriesTypes?.venn) {
      Highcharts.chart(element, {
        chart: {
          height: 390,
          backgroundColor: "transparent",
          events: {
            render() {
              renderMitigationVennFallback(
                this,
                affectedDisplay,
                mitigationDisplay,
                overlapDisplay,
              );
            },
          },
        },
        title: {
          text: "Affected and target populations(Hover on chart to see details)",
          style: { color: "#0f172a", fontSize: "16px", fontWeight: "700" },
        },
        credits: { enabled: false },
        legend: { enabled: false },
        xAxis: { visible: false },
        yAxis: { visible: false },
        series: [],
      });
      return;
    }
    Highcharts.chart(element, {
      chart: { type: "venn", height: 390, backgroundColor: "transparent" },
      title: {
        text: "Affected and target populations(Hover on chart to see details)",
        style: { color: "#0f172a", fontSize: "16px", fontWeight: "700" },
      },
      credits: { enabled: false },
      legend: { enabled: false },
      tooltip: {
        useHTML: true,
        formatter() {
          const members = groupedTargetPopulationLabels(this.point.custom?.members || []);
          const items = members.length
            ? `<ul>${members.map((label) => `<li>${escapeHtml(label)}</li>`).join("")}</ul>`
            : "<p>No shared target populations</p>";
          return `<strong>${escapeHtml(this.point.name)}</strong>${items}`;
        },
      },
      plotOptions: {
        venn: {
          borderColor: "#ffffff",
          borderWidth: 2,
          opacity: 0.72,
          dataLabels: {
            enabled: true,
            formatter() {
              return `${this.point.custom?.shortTitle || this.point.name}<br/>${this.point.value}`;
            },
            style: { color: "#0f172a", fontSize: "11px", fontWeight: "700", textOutline: "3px #ffffff" },
          },
        },
      },
      series: [{
        type: "venn",
        data: [
          {
            sets: ["affected"],
            value: affectedDisplay.length,
            name: "Hazard profiles’ target populations",
            color: "#0891b2",
            custom: { members: affectedDisplay, shortTitle: "Hazard profiles" },
          },
          {
            sets: ["mitigation"],
            value: mitigationDisplay.length,
            name: "Mitigation measure target populations",
            color: "#7c3aed",
            custom: { members: mitigationDisplay, shortTitle: "Mitigation measure" },
          },
          {
            sets: ["affected", "mitigation"],
            value: overlapDisplay.length,
            name: "Shared target populations",
            color: "#4f46e5",
            custom: { members: overlapDisplay, shortTitle: "Shared" },
          },
        ],
      }],
    });
  });

  root.querySelectorAll(".js-evaluation-radar-chart:not([data-chart-ready])").forEach((element) => {
    const labels = parseChartData(element, "labels");
    const categories = parseChartData(element, "categories");
    const values = parseChartData(element, "values").map((value) => Math.max(1, Math.min(10, Number(value) || 1)));
    const storedSeries = parseChartData(element, "series");
    if (!labels.length || labels.length !== values.length) return;
    const colors = ["#6d28d9", "#0891b2", "#ea580c", "#16a34a", "#db2777"];
    const radarSeries = (storedSeries.length
      ? storedSeries
      : [{ name: "Current mitigation", values, current: true }]
    ).map((item, index) => {
      const color = colors[index % colors.length];
      const itemValues = Array.isArray(item.values)
        ? item.values.map((value) =>
            value === null || value === undefined
              ? null
              : Math.max(1, Math.min(10, Number(value) || 1)),
          )
        : [];
      return {
        name: String(item.name || `Mitigation ${index + 1}`),
        data: itemValues,
        color,
        fillColor: Highcharts.color(color).setOpacity(item.current ? 0.16 : 0.035).get(),
        lineWidth: item.current ? 3 : 2,
        marker: { radius: item.current ? 4 : 3 },
      };
    }).filter((item) => item.data.length === labels.length);
    element.dataset.chartReady = "true";
    Highcharts.chart(element, {
      chart: {
        polar: true,
        type: "line",
        height: 600,
        backgroundColor: "transparent",
        spacing: [70, 112, 88, 112],
        events: {
          render() {
            renderRadarCategoryRing(this, categories);
            rotateRadarAxisLabels(this, labels.length);
          },
        },
      },
      title: { text: "Evaluation score profile", align: "center", style: { color: "#0f172a", fontSize: "16px", fontWeight: "700" } },
      credits: { enabled: false },
      legend: {
        enabled: radarSeries.length > 1,
        align: "center",
        verticalAlign: "bottom",
        layout: "horizontal",
        itemStyle: { color: "#334155", fontSize: "10px", fontWeight: "600" },
      },
      pane: { size: "50%" },
      xAxis: {
        categories: labels,
        tickmarkPlacement: "on",
        lineWidth: 0,
        labels: {
          distance: 16,
          style: { color: "#475569", fontSize: "11px", textOverflow: "none", width: "92px" },
        },
      },
      yAxis: { min: 0, max: 10, tickInterval: 2, gridLineInterpolation: "polygon", lineWidth: 0 },
      tooltip: {
        shared: true,
        pointFormatter() {
          return `<span style="color:${this.color}">●</span> ${escapeHtml(this.series.name)}: <b>${this.y} / 10</b><br>`;
        },
      },
      plotOptions: { series: { pointPlacement: "on", fillOpacity: 1 } },
      series: radarSeries,
    });
  });
}

function addMessage(role, text, isError = false, targetLog = chatLog) {
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
  const content = document.createElement("div");
  content.className = "bubble-content";
  if (role === "bot") {
    renderTrustedHtml(content, text);
    normalizePracticalConsiderationLists(content);
  } else {
    content.textContent = text;
  }
  bubble.appendChild(content);

  const timestamp = document.createElement("span");
  timestamp.className = "timestamp";
  timestamp.textContent = nowLabel();
  bubble.appendChild(timestamp);
  applyCollapsibleBubble(bubble);

  if (role === "user") {
    row.appendChild(bubble);
    row.appendChild(avatar);
  } else {
    row.appendChild(avatar);
    row.appendChild(bubble);
  }
  targetLog.appendChild(row);
  initializeHighcharts(content);
  syncCollapsibleMessages(targetLog);
  scrollToBottom(targetLog);
  return row;
}

async function typeServerMessage(row, html, targetLog = chatLog) {
  const bubble = row.querySelector(".bubble");
  const timestamp = bubble.querySelector(".timestamp");
  timestamp.remove();
  let content = bubble.querySelector(".bubble-content");
  if (!content) {
    content = document.createElement("div");
    content.className = "bubble-content";
    bubble.textContent = "";
    bubble.appendChild(content);
  }
  content.textContent = "";

  if (!typingEffectEnabled()) {
    renderTrustedHtml(content, html);
    normalizePracticalConsiderationLists(content);
    initializeHighcharts(content);
    bubble.appendChild(timestamp);
    applyCollapsibleBubble(bubble);
    syncCollapsibleMessages(targetLog);
    scrollToBottom(targetLog);
    return;
  }

  const template = document.createElement("template");
  renderTrustedHtml(template, html);

  async function typeNode(node, parent) {
    if (node.nodeType === Node.TEXT_NODE) {
      const textNode = document.createTextNode("");
      parent.appendChild(textNode);
      const text = node.textContent || "";
      for (const char of text) {
        textNode.textContent += char;
        scrollToBottom(targetLog);
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
    await typeNode(child, content);
  }
  normalizePracticalConsiderationLists(content);
  initializeHighcharts(content);
  bubble.appendChild(timestamp);
  applyCollapsibleBubble(bubble);
  syncCollapsibleMessages(targetLog);
  scrollToBottom(targetLog);
}

function normalizePracticalConsiderationLists(root) {
  if (!root) return;
  const parentLabels = new Set([
    "profile specific concerns",
    "implementation issues",
  ]);
  const childLabels = new Set([
    "affordability",
    "accessibility",
    "awareness engagement",
    "burden reduction",
    "cultural sensitivity",
    "data protection",
    "feasibility",
    "flexibility",
    "gender disparities",
    "political group considerations",
    "regulatory barriers",
    "scalability",
    "stakeholder involvement",
    "vulnerable groups",
  ]);
  const profileChildLabels = new Set([
    "cultural sensitivity",
    "gender disparities",
    "political group considerations",
    "vulnerable groups",
  ]);
  const implementationChildLabels = new Set(
    Array.from(childLabels).filter((label) => !profileChildLabels.has(label)),
  );

  root.querySelectorAll("ul").forEach((list) => {
    let activeParent = null;
    Array.from(list.children).forEach((item) => {
      if (item.tagName !== "LI") return;
      const ownText = Array.from(item.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE || node.nodeType === Node.ELEMENT_NODE)
        .map((node) => node.textContent || "")
        .join(" ")
        .split("\n")[0] || "";
      const label = ownText.split(":")[0] || ownText;
      const labelKey = normalizeForMatch(label);

      if (parentLabels.has(labelKey) || labelKey.startsWith("implementation issues")) {
        activeParent = item;
        return;
      }

      if (!childLabels.has(labelKey)) return;
      if (profileChildLabels.has(labelKey) || implementationChildLabels.has(labelKey)) {
        const expectedParentKey = profileChildLabels.has(labelKey)
          ? "profile specific concerns"
          : "implementation issues";
        const expectedParent = Array.from(list.children).find((candidate) => {
          if (candidate.tagName !== "LI") return false;
          const text = Array.from(candidate.childNodes)
            .filter((node) => node.nodeType === Node.TEXT_NODE || node.nodeType === Node.ELEMENT_NODE)
            .map((node) => node.textContent || "")
            .join(" ")
            .split("\n")[0] || "";
          const key = normalizeForMatch((text.split(":")[0] || text));
          return key === expectedParentKey || key.startsWith(expectedParentKey);
        });
        if (expectedParent) activeParent = expectedParent;
      }
      if (!activeParent) return;

      let nestedList = activeParent.querySelector(":scope > ul");
      if (!nestedList) {
        nestedList = document.createElement("ul");
        activeParent.appendChild(nestedList);
      }
      nestedList.appendChild(item);
    });
  });
}

function applyCollapsibleBubble(bubble) {
  const content = bubble?.querySelector(".bubble-content");
  if (!content) return;
  bubble.querySelector(".bubble-toggle")?.remove();
  const wordCount = (content.textContent || "").trim().split(/\s+/).filter(Boolean).length;
  if (wordCount <= collapsibleMessageWordLimit) {
    bubble.classList.remove("is-collapsible", "is-expanded");
    return;
  }
  bubble.classList.add("is-collapsible");
  bubble.classList.add("is-expanded");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "bubble-toggle";
  button.textContent = "Show less";
  button.addEventListener("click", () => {
    const expanded = !bubble.classList.contains("is-expanded");
    setCollapsibleBubbleExpanded(bubble, expanded);
    const row = bubble.closest(".message-row");
    row?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
  bubble.appendChild(button);
}

function renderValidationDetails(row, details) {
  if (!row || !details || typeof details !== "object") return;
  const content = row.querySelector(".bubble-content");
  if (!content) return;

  const panel = document.createElement("details");
  panel.className = `validation-details validation-details-${details.phase || "status"}`;
  panel.open = true;

  const summary = document.createElement("summary");
  summary.textContent = details.title || "Validation status";
  panel.appendChild(summary);

  const customHazardCards = Array.isArray(details.custom_hazard_grounding_status)
    ? details.custom_hazard_grounding_status
    : [];
  if (customHazardCards.length) {
    const grid = document.createElement("div");
    grid.className = "validation-dimension-grid custom-hazard-grounding-grid";
    customHazardCards.forEach((card) => {
      const item = document.createElement("div");
      item.className = "validation-dimension custom-hazard-grounding-card";

      const label = document.createElement("span");
      label.textContent = String(card?.title || "Status");
      const badge = document.createElement("strong");
      badge.className = `validation-badge validation-badge-${validationStatusClass(card?.status)}`;
      badge.textContent = String(card?.status || "INSUFFICIENT INFO");
      item.append(label, badge);

      if (card?.score !== null && card?.score !== undefined && card?.score !== "") {
        const scoreText = document.createElement("small");
        scoreText.textContent = `Score: ${formatValidationPercent(card.score)}/100`;
        item.appendChild(scoreText);
      }

      const reason = String(card?.reason || "").trim();
      if (reason) {
        const reasonText = document.createElement("p");
        reasonText.className = "validation-dimension-explanation";
        reasonText.textContent = reason;
        item.appendChild(reasonText);
      }

      const question = String(card?.clarification_question || "").trim();
      if (question) {
        const questionText = document.createElement("p");
        questionText.className = "validation-clarification-question";
        questionText.textContent = question;
        item.appendChild(questionText);
      }
      grid.appendChild(item);
    });
    panel.appendChild(grid);
  }

  const dimensions = details.dimensions && typeof details.dimensions === "object"
    ? details.dimensions
    : {};
  if (!customHazardCards.length && Object.keys(dimensions).length) {
    const grid = document.createElement("div");
    grid.className = "validation-dimension-grid";
    Object.entries(dimensions).forEach(([name, value]) => {
      const status = typeof value === "string" ? value : value?.status;
      const score = typeof value === "object" ? value?.support_score : null;
      const item = document.createElement("div");
      item.className = "validation-dimension";

      const label = document.createElement("span");
      label.textContent = validationLabel(name);
      const badge = document.createElement("strong");
      badge.className = `validation-badge validation-badge-${validationStatusClass(status)}`;
      badge.textContent = validationLabel(status || "unknown");
      item.append(label, badge);

      if (score !== null && score !== undefined) {
        const scoreText = document.createElement("small");
        scoreText.textContent = `Support score: ${formatValidationNumber(score)}`;
        item.appendChild(scoreText);
      }
      const explanation = typeof value === "object"
        ? String(value?.explanation || "").trim()
        : "";
      if (explanation) {
        const explanationText = document.createElement("p");
        explanationText.className = "validation-dimension-explanation";
        explanationText.textContent = explanation;
        item.appendChild(explanationText);
      }
      grid.appendChild(item);
    });
    panel.appendChild(grid);
  }

  appendValidationGroup(panel, "Signals", details.metrics, true);
  appendValidationGroup(panel, "Checks", details.checks, false);

  if (details.reason) {
    const reason = document.createElement("p");
    reason.className = "validation-reason";
    reason.textContent = details.reason;
    panel.appendChild(reason);
  }

  content.appendChild(panel);
  applyCollapsibleBubble(row.querySelector(".bubble"));
  syncCollapsibleMessages(row.parentElement);
}

function appendValidationGroup(panel, title, values, formatMetrics) {
  if (!values || typeof values !== "object") return;
  const entries = Object.entries(values).filter(([, value]) => value !== null && value !== undefined);
  if (!entries.length) return;
  const group = document.createElement("div");
  group.className = "validation-signal-group";
  const heading = document.createElement("h4");
  heading.textContent = title;
  group.appendChild(heading);
  entries.forEach(([name, value]) => {
    const row = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = validationLabel(name);
    const output = document.createElement("strong");
    output.textContent = formatMetrics ? formatValidationMetric(name, value) : validationLabel(value);
    row.append(label, output);
    group.appendChild(row);
  });
  panel.appendChild(group);
}

function validationLabel(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function validationStatusClass(status) {
  const normalized = String(status || "").toLowerCase();
  if (["clear", "supported", "confirmed", "ready"].includes(normalized)) return "pass";
  if (["contradicted", "rejected"].includes(normalized)) return "fail";
  return "pending";
}

function formatValidationNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : String(value);
}

function formatValidationPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.round(number)) : String(value);
}

function formatValidationMetric(name, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return validationLabel(value);
  if (name === "confidence_score") return `${Math.round(number)}/100`;
  if (["rubric_coverage", "retrieval_support", "verdict_stability"].includes(name)) {
    return `${Math.round(number * 100)}%`;
  }
  return String(number);
}

function addTyping(targetLog = chatLog) {
  const row = createElement("div", { className: "message-row bot" });
  row.dataset.typing = "true";
  const avatar = createElement("img", {
    className: "chat-avatar teacher-avatar",
    attrs: { src: teacherAvatarPath, alt: "Dr Transition" },
  });
  const typing = createElement(
    "span",
    { className: "typing", attrs: { "aria-label": "Dr Transition is typing" } },
    [createElement("span"), createElement("span"), createElement("span")],
  );
  row.append(avatar, createElement("div", { className: "bubble" }, [typing]));
  targetLog.appendChild(row);
  scrollToBottom(targetLog);
  return row;
}

function setLoading(value) {
  loading = value;
  messageInput.disabled = value;
  textareaInput.disabled = value;
  reasonInput.disabled = value;
  secondaryReasonInput.disabled = value;
  evidenceInput.disabled = value;
  evidenceFileInput.disabled = value;
  scoreInput.disabled = value;
  evaluationReasonInput.disabled = value;
  evaluationEvidenceInput.disabled = value;
  evaluationEvidenceFileInput.disabled = value;
  sendButton.disabled = value;
  micButton.disabled = value || !micSupported || appState.inputMode !== "text";
  optionTray.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.used === "true";
  });
}

function setInputMode(mode = "text", step = "", options = [], session = appState.currentSession) {
  const isNewHazardEntry = Boolean(
    step === "hazards"
    && session?.custom_hazard
    && !String(session.custom_hazard.text || "").trim()
    && !session.selected_hazard,
  );
  const effectiveMode = hasPendingCustomProfileReason(session) || isNewHazardEntry
    ? "textarea"
    : mode;
  appState.inputMode = effectiveMode;
  appState.currentOptions = options || [];
  syncTargetPopulationQuestion(step, appState.currentOptions);
  updateStageVisual(step, session || appState.currentSession, appState.currentOptions);
  const reasonEvidenceMode = ["reason_evidence", "reason_only", "evidence_only", "mitigation_measure"].includes(effectiveMode);
  const evaluationMode = effectiveMode === "evaluation_question";
  const textareaMode = effectiveMode === "textarea";
  micButton.disabled = !micSupported || reasonEvidenceMode || evaluationMode || textareaMode;
  const placeholder = placeholderForStep(step, options, session);
  messageInput.placeholder = placeholder;
  textareaInput.placeholder = placeholder;
  setReasonEvidencePlaceholders(step, effectiveMode);
  reasonEvidenceFields.classList.toggle("mitigation-mode", effectiveMode === "mitigation_measure");
  messageInputRow.hidden = reasonEvidenceMode || evaluationMode;
  messageInput.hidden = textareaMode;
  textareaInput.hidden = !textareaMode;
  reasonEvidenceFields.hidden = !reasonEvidenceMode;
  evaluationFields.hidden = !evaluationMode;

  if (effectiveMode === "reason_only") {
    evidenceInput.value = "";
    evidenceFileInput.value = "";
  }
  if (effectiveMode === "evidence_only") {
    reasonInput.value = "";
  }

  if (reasonEvidenceMode) {
    if (effectiveMode === "evidence_only") {
      evidenceInput.focus();
    } else {
      reasonInput.focus();
    }
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
    if (textareaMode) {
      messageInput.value = "";
      textareaInput.focus();
    } else {
      textareaInput.value = "";
      messageInput.focus();
    }
  }
}

function updateSessionCard(session) {
  appState.currentSession = session || {};
  targetPopulationQuestions = Array.isArray(session?.target_population_questions)
    ? session.target_population_questions
    : [];
  targetPopulationAnswers = Array.isArray(session?.target_population_answers)
    ? session.target_population_answers
    : [];
  [
    [sessionFields.country, session?.country],
    [sessionFields.region, session?.region],
    [sessionFields.sector, session?.sector],
  ].forEach(([field, value]) => {
    if (!field) return;
    field.textContent = value || "";
    if (field.parentElement) field.parentElement.hidden = !value;
  });
  const hasSession = Boolean(session?.country || session?.region || session?.sector);
  if (sessionEmpty) sessionEmpty.hidden = hasSession;
  document.querySelector(".stage-selection")?.classList.toggle("has-session", hasSession);
  updateNewSessionButton();
  renderSelectedHazardContext(session);
  updateStageVisual(appState.currentStep, appState.currentSession, appState.currentOptions);
}

function applyInputValues(values = {}) {
  if (!values || typeof values !== "object") return;
  if (typeof values.reason === "string") reasonInput.value = values.reason;
  if (typeof values.secondary_reason === "string") {
    secondaryReasonInput.value = values.secondary_reason;
  }
  if (typeof values.evidence_url === "string") evidenceInput.value = values.evidence_url;
}

function updateNewSessionButton() {
  if (!resetButton) return;
  resetButton.disabled = false;
  resetButton.title = "Start a new session";
}

function renderSelectedHazardContext(session = {}) {
  if (!selectedHazardContext || !selectedHazardName || !affectedProfileList) return;
  const hasMitigationReview = session.mitigation_review && typeof session.mitigation_review === "object";
  const mitigationContextSteps = new Set([
    "mitigation_target_population",
    "mitigation_target_population_review",
    "mitigation_review",
    "evaluation_question",
    "evaluation_complete",
    "system_inquiry_intro",
    "system_inquiry_observation",
    "system_inquiry_followup",
    "system_inquiry_complete",
  ]);
  const mitigationMeasure = String(session.mitigation_measure || "").trim();
  const showMitigationReviewPanel =
    (mitigationContextSteps.has(appState.currentStep) && mitigationMeasure)
    || (appState.currentStep === "complete" && hasMitigationReview)
    || (appState.currentStep === "mitigation" && hasMitigationReview);
  const hazard = String(session.selected_hazard || "").trim();
  const profileDetails = Array.isArray(session.affected_profile_details)
    ? session.affected_profile_details
        .map((profile) => ({
          name: String(profile?.name || profile?.profile || "").trim(),
          variableType: String(profile?.variable_type || "").trim().toLowerCase(),
          variableName: String(profile?.variable_name || profile?.variable || "").trim().toLowerCase(),
        }))
        .filter((profile) => profile.name)
    : [];
  const profiles = profileDetails.length
    ? profileDetails
    : (Array.isArray(session.affected_profiles)
        ? session.affected_profiles
            .map((profile) => ({ name: String(profile || "").trim(), variableType: "", variableName: "" }))
            .filter((profile) => profile.name)
        : []);

  selectedHazardContext.hidden = showMitigationReviewPanel ? !mitigationMeasure : !hazard;
  if (selectedContextLabel) {
    selectedContextLabel.textContent = showMitigationReviewPanel ? "Proposed mitigation measure" : "Selected hazard";
  }
  selectedHazardName.textContent = showMitigationReviewPanel ? mitigationMeasure : hazard;
  if (affectedProfileContext) affectedProfileContext.hidden = showMitigationReviewPanel;
  if (mitigationReviewContext) mitigationReviewContext.hidden = !showMitigationReviewPanel;
  if (showMitigationReviewPanel) {
    renderMitigationReviewContext(session);
    return;
  }
  clearElement(affectedProfileList);
  profiles.forEach((profile) => {
    const item = document.createElement("li");
    const icon = svgPathIconElement(
      "affected-profile-item-icon",
      "M15 8a3 3 0 10-6 0 3 3 0 006 0zM5 20a7 7 0 0114 0",
    );
    const label = document.createElement("span");
    label.textContent = profile.name;
    if (profile.variableType === "macro" || profile.variableName.startsWith("macro_")) {
      const typeLabel = document.createElement("span");
      typeLabel.className = "affected-profile-type-label";
      typeLabel.textContent = "macro";
      label.appendChild(typeLabel);
    }
    item.append(icon, label);
    affectedProfileList.appendChild(item);
  });
  if (affectedProfileEmpty) affectedProfileEmpty.hidden = profiles.length > 0;
}

function renderMitigationReviewContext(session = {}) {
  const review = session.mitigation_review && typeof session.mitigation_review === "object"
    ? session.mitigation_review
    : {};
  const profiles = Array.isArray(session.benefited_profiles)
    ? session.benefited_profiles.map((profile) => String(profile || "").trim()).filter(Boolean)
    : [];

  if (benefitedProfileList) {
    clearElement(benefitedProfileList);
    profiles.forEach((profile) => {
      const item = document.createElement("li");
      const icon = svgPathIconElement("affected-profile-item-icon", "M20 6L9 17l-5-5");
      const label = document.createElement("span");
      label.textContent = profile;
      item.append(icon, label);
      benefitedProfileList.appendChild(item);
    });
  }
  if (benefitedProfileEmpty) benefitedProfileEmpty.hidden = profiles.length > 0;
  if (mitigationConfidenceScore) {
    mitigationConfidenceScore.textContent =
      review.confidence_score === null || review.confidence_score === undefined
        ? "Not available"
        : formatValidationMetric("confidence_score", review.confidence_score);
  }
  const status = String(review.grounding_status || "").trim();
  const explanation = String(review.explanation || "").trim();
  const supportedDimensions = Array.isArray(review.supported_dimensions) ? review.supported_dimensions : [];
  /* if (mitigationGroundingStatus) {
    const positiveStatus =
      ["accepted", "clear", "grounded", "pass", "passed", "supported", "validated", "positive"].includes(status.toLowerCase())
      || supportedDimensions.length > 0;
    mitigationGroundingStatus.textContent = positiveStatus ? validationLabel(status || "Supported") : "Not available";
    mitigationGroundingStatus.hidden = !positiveStatus;
    mitigationGroundingStatus.title = explanation || "No grounding explanation available.";
    mitigationGroundingStatus.dataset.explanation = explanation;
    mitigationGroundingStatus.classList.toggle("expanded", false);
    mitigationGroundingStatus.setAttribute("aria-expanded", "false");
  } */
  if (mitigationSupportedDimensions) {
    const dimensions = supportedDimensions;
    clearElement(mitigationSupportedDimensions);
    dimensions.forEach((dimension) => {
      const name = String(dimension?.name || "").trim();
      if (!name) return;
      const item = document.createElement("li");
      item.textContent = validationLabel(name);
      const dimensionExplanation = String(dimension?.explanation || "").trim();
      if (dimensionExplanation) item.title = dimensionExplanation;
      mitigationSupportedDimensions.appendChild(item);
    });
    mitigationSupportedDimensions.hidden = dimensions.length === 0;
  }
  if (mitigationVerdictStability) {
    mitigationVerdictStability.textContent =
      review.verdict_stability === null || review.verdict_stability === undefined
        ? "Not available"
        : formatValidationMetric("verdict_stability", review.verdict_stability);
  }
  if (mitigationSupportCorpus) {
    mitigationSupportCorpus.textContent = review.support_corpus ? validationLabel(review.support_corpus) : "Not available";
  }
  if (mitigationLastNote) {
    mitigationLastNote.textContent = explanation || "No note available.";
  }
}

mitigationGroundingStatus?.addEventListener("click", () => {
  const explanation = mitigationGroundingStatus.dataset.explanation || "No grounding explanation available.";
  const expanded = mitigationGroundingStatus.classList.toggle("expanded");
  mitigationGroundingStatus.setAttribute("aria-expanded", String(expanded));
  mitigationGroundingStatus.textContent = expanded
    ? explanation
    : validationLabel(appState.currentSession?.mitigation_review?.grounding_status || "Supported");
});

function syncTargetPopulationQuestion(step, options = []) {
  if (step !== "target_population_question" && step !== "add_dgs") {
    currentTargetPopulationQuestion = null;
    return;
  }
  const optionLabels = new Set(
    (options || [])
      .map((option) => option.label)
      .filter((label) => !isTargetPopulationActionLabel(label)),
  );
  currentTargetPopulationQuestion =
    targetPopulationQuestions.find((question) =>
      (question.options || []).some((label) => optionLabels.has(label)),
    ) || null;
}

function disableOldOptions() {
  optionTray.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
    button.dataset.used = "true";
  });
  hideOptionTooltip();
}

function ensureOptionTooltip() {
  if (optionTooltipElement) return optionTooltipElement;
  optionTooltipElement = document.createElement("div");
  optionTooltipElement.className = "option-tooltip";
  optionTooltipElement.setAttribute("role", "tooltip");
  optionTooltipElement.hidden = true;
  document.body.appendChild(optionTooltipElement);
  return optionTooltipElement;
}

function showOptionTooltip(target) {
  const text = String(target?.dataset?.tooltip || "").trim();
  if (!text || target.disabled) return;
  const tooltip = ensureOptionTooltip();
  optionTooltipTarget = target;
  tooltip.textContent = text;
  tooltip.hidden = false;
  tooltip.classList.add("is-visible");
  positionOptionTooltip();
}

function hideOptionTooltip() {
  if (!optionTooltipElement) return;
  optionTooltipElement.classList.remove("is-visible");
  optionTooltipElement.hidden = true;
  optionTooltipTarget = null;
}

function positionOptionTooltip() {
  if (!optionTooltipElement || !optionTooltipTarget || optionTooltipElement.hidden) return;
  const targetRect = optionTooltipTarget.getBoundingClientRect();
  const tooltipRect = optionTooltipElement.getBoundingClientRect();
  const viewportPadding = 12;
  const gap = 9;
  let top = targetRect.top - tooltipRect.height - gap;
  if (top < viewportPadding) {
    top = targetRect.bottom + gap;
    optionTooltipElement.classList.add("is-below");
  } else {
    optionTooltipElement.classList.remove("is-below");
  }
  const centeredLeft = targetRect.left + targetRect.width / 2 - tooltipRect.width / 2;
  const left = Math.min(
    Math.max(centeredLeft, viewportPadding),
    window.innerWidth - tooltipRect.width - viewportPadding,
  );
  optionTooltipElement.style.left = `${left}px`;
  optionTooltipElement.style.top = `${top}px`;
}

function sourceCitationTooltipFor(target) {
  if (!target) return null;
  return target.querySelector(".source-citation-tooltip");
}

function showSourceCitationTooltip(target) {
  const tooltip = sourceCitationTooltipFor(target);
  if (!tooltip) return;
  sourceCitationTooltipTarget = target;
  tooltip.classList.add("is-viewport-positioned");
  positionSourceCitationTooltip();
}

function hideSourceCitationTooltip(target = sourceCitationTooltipTarget) {
  const tooltip = sourceCitationTooltipFor(target);
  if (tooltip) {
    tooltip.classList.remove("is-viewport-positioned", "is-below");
    tooltip.style.removeProperty("left");
    tooltip.style.removeProperty("top");
    tooltip.style.removeProperty("--source-tooltip-arrow-left");
  }
  if (!target || target === sourceCitationTooltipTarget) {
    sourceCitationTooltipTarget = null;
  }
}

function positionSourceCitationTooltip() {
  const target = sourceCitationTooltipTarget;
  const tooltip = sourceCitationTooltipFor(target);
  if (!target || !tooltip || !tooltip.classList.contains("is-viewport-positioned")) return;

  const targetRect = target.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const viewportPadding = 12;
  const gap = 10;
  const maxLeft = Math.max(viewportPadding, window.innerWidth - tooltipRect.width - viewportPadding);
  const centeredLeft = targetRect.left + targetRect.width / 2 - tooltipRect.width / 2;
  const left = Math.min(Math.max(centeredLeft, viewportPadding), maxLeft);
  let top = targetRect.top - tooltipRect.height - gap;

  if (top < viewportPadding) {
    top = targetRect.bottom + gap;
    tooltip.classList.add("is-below");
  } else {
    tooltip.classList.remove("is-below");
  }

  const maxTop = Math.max(viewportPadding, window.innerHeight - tooltipRect.height - viewportPadding);
  top = Math.min(Math.max(top, viewportPadding), maxTop);
  const arrowLeft = Math.min(
    Math.max(targetRect.left + targetRect.width / 2 - left, 12),
    tooltipRect.width - 12,
  );

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  tooltip.style.setProperty("--source-tooltip-arrow-left", `${arrowLeft}px`);
}

optionTray?.addEventListener("pointerover", (event) => {
  const target = event.target.closest("[data-tooltip]");
  if (!target || !optionTray.contains(target)) return;
  showOptionTooltip(target);
});

optionTray?.addEventListener("pointerout", (event) => {
  const target = event.target.closest("[data-tooltip]");
  if (!target || !optionTray.contains(target)) return;
  if (event.relatedTarget && target.contains(event.relatedTarget)) return;
  hideOptionTooltip();
});

optionTray?.addEventListener("focusin", (event) => {
  const target = event.target.closest("[data-tooltip]");
  if (target && optionTray.contains(target)) showOptionTooltip(target);
});

optionTray?.addEventListener("focusout", hideOptionTooltip);
optionTray?.addEventListener("click", hideOptionTooltip);
chatLog?.addEventListener("pointerover", (event) => {
  const target = event.target.closest(".source-citation");
  if (!target || !chatLog.contains(target)) return;
  showSourceCitationTooltip(target);
});

chatLog?.addEventListener("pointerout", (event) => {
  const target = event.target.closest(".source-citation");
  if (!target || !chatLog.contains(target)) return;
  if (event.relatedTarget && target.contains(event.relatedTarget)) return;
  hideSourceCitationTooltip(target);
});

chatLog?.addEventListener("focusin", (event) => {
  const target = event.target.closest(".source-citation");
  if (target && chatLog.contains(target)) showSourceCitationTooltip(target);
});

chatLog?.addEventListener("focusout", (event) => {
  const target = event.target.closest(".source-citation");
  if (target && chatLog.contains(target)) hideSourceCitationTooltip(target);
});

function positionVisibleTooltips() {
  positionOptionTooltip();
  positionSourceCitationTooltip();
}

window.addEventListener("resize", positionVisibleTooltips);
document.addEventListener("scroll", positionVisibleTooltips, true);

function renderOptions(options, otherOptions = []) {
  appState.currentOptions = options || [];
  appState.currentOtherOptions = otherOptions || [];
  if (appState.currentStep === "hazard_profile_selection" && hasListedHazardActions(appState.currentOptions)) {
    listedHazardOptions = [...appState.currentOptions];
  }
  clearElement(optionTray);
  appState.highlightedOptionLabel = "";
  if (appState.inputMode === "target_population_multi") {
    renderTargetPopulationOptions(appState.currentOptions);
    renderOtherOptionsMenu();
    return;
  }
  if (shouldCollapseHazardOptions(options)) {
    renderCollapsedHazardOptions(options);
  } else {
    options.forEach((option) => {
      const extraClass = isHazardOptionActionLabel(option.label)
        ? "hazard-action-option"
        : "";
      const button = createOptionButton(option.label, extraClass);
      if (extraClass) button.dataset.hazardAction = "true";
      optionTray.appendChild(button);
    });
  }
  renderOtherOptionsMenu();
  updateOptionHighlight();
}

function shouldCollapseHazardOptions(options = []) {
  if (appState.currentStep !== "hazard_profile_selection") return false;
  const hazardOptions = options.filter((option) => !isHazardOptionActionLabel(option.label));
  return hazardOptions.length > 3;
}

function renderCollapsedHazardOptions(options = []) {
  const hazardOptions = options.filter((option) => !isHazardOptionActionLabel(option.label));
  const actionOptions = options.filter((option) => isHazardOptionActionLabel(option.label));
  const visibleHazards = hazardOptions.slice(0, 3);
  const hiddenHazards = hazardOptions.slice(3);

  visibleHazards.forEach((option) => optionTray.appendChild(createOptionButton(option.label)));

  const showMore = document.createElement("button");
  showMore.type = "button";
  showMore.className = "option-pill show-more-options-toggle";
  showMore.textContent = "Show More";
  showMore.setAttribute("aria-label", "Show more hazards");
  showMore.setAttribute("data-tooltip", "Show more hazards");
  showMore.addEventListener("click", () => {
    pauseSpeech();
    showMore.remove();
    hiddenHazards.forEach((option) => {
      const button = createOptionButton(option.label);
      button.classList.add("hazard-extra-option");
      optionTray.insertBefore(button, optionTray.querySelector("[data-hazard-action='true']"));
    });
    updateOptionHighlight();
  });
  optionTray.appendChild(showMore);

  actionOptions.forEach((option) => {
    const button = createOptionButton(option.label, "hazard-action-option");
    button.dataset.hazardAction = "true";
    optionTray.appendChild(button);
  });
}

function isHazardOptionActionLabel(label = "") {
  return hazardOptionActionLabels.has(normalizeForMatch(label));
}

function hasListedHazardActions(options = []) {
  return options.some((option) => {
    const normalized = normalizeForMatch(option?.label || "");
    return (
      normalized === "show hazards added by experts"
      || normalized === "show co created hazards"
    );
  });
}

function hazardOptionObjectsFromLabels(labels = [], actionLabel = "Show listed hazards") {
  const seen = new Set();
  const options = labels
    .map((label) => String(label || "").trim())
    .filter(Boolean)
    .filter((label) => {
      const key = normalizeForMatch(label);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((label, index) => ({ id: index + 1, label }));
  if (actionLabel) {
    options.push({ id: options.length + 1, label: actionLabel });
  }
  return options;
}

function localHazardListForAction(label = "") {
  const normalized = normalizeForMatch(label);
  if (normalized === "show hazards added by experts") {
    return Array.isArray(appState.currentSession?.additional_hazards)
      ? appState.currentSession.additional_hazards
      : [];
  }
  if (normalized === "show co created hazards") {
    return Array.isArray(appState.currentSession?.custom_hazards)
      ? appState.currentSession.custom_hazards
      : [];
  }
  return [];
}

function handleLocalHazardAction(label = "") {
  if (appState.currentStep !== "hazard_profile_selection") return false;
  const normalized = normalizeForMatch(label);
  if (normalized === "show listed hazards" && listedHazardOptions.length) {
    renderOptions(listedHazardOptions, appState.currentOtherOptions);
    return true;
  }
  if (
    normalized !== "show hazards added by experts"
    && normalized !== "show co created hazards"
  ) {
    return false;
  }
  const labels = localHazardListForAction(label);
  if (!labels.length) return false;
  renderOptions(hazardOptionObjectsFromLabels(labels), appState.currentOtherOptions);
  return true;
}

function reportScopeForOption(label = "") {
  return reportOptionScopes.get(normalizeForMatch(label)) || "";
}

async function downloadMitigationReport(scope) {
  if (!sessionId || !scope) return;
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/report?scope=${encodeURIComponent(scope)}`,
  );
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  if (!response.ok) {
    let detail = "Could not generate the report.";
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_error) {
      // Keep the generic message when the server did not return JSON.
    }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = filenameMatch?.[1] || `dr-transition-report-${scope}.pdf`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderTargetPopulationOptions(options = []) {
  const previouslySelected = selectedTargetPopulationLabels(currentTargetPopulationQuestion?.id);
  const normalOptions = options.filter(
    (option) =>
      !isTargetPopulationActionLabel(option.label),
  );
  const actions = options.filter((option) =>
    isTargetPopulationActionLabel(option.label),
  );

  if (normalOptions.length) {
    const group = document.createElement("div");
    group.className = "target-option-group";
    normalOptions.forEach((option) => {
      const label = document.createElement("label");
      label.className = "target-option-check";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = option.label;
      checkbox.dataset.targetOption = "true";
      checkbox.checked = previouslySelected.has(normalizeForMatch(option.label));
      const span = document.createElement("span");
      span.textContent = option.label;
      label.setAttribute("data-tooltip", option.label);
      label.appendChild(checkbox);
      label.appendChild(span);
      group.appendChild(label);
    });
    optionTray.appendChild(group);

    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "option-pill";
    submit.textContent = "Submit selected";
    submit.setAttribute("aria-label", "Submit selected");
    submit.setAttribute("data-tooltip", "Submit selected");
    submit.addEventListener("click", () => {
      const selected = Array.from(optionTray.querySelectorAll("[data-target-option='true']:checked"))
        .map((input) => input.value)
        .filter(Boolean);
      if (!selected.length) {
        flashRequiredField(optionTray.querySelector("[data-target-option='true']"));
        return;
      }
      collapseExpandedMessages();
      disableOldOptions();
      addMessage("user", selected.join(", "));
      sendMessage(selected.join("\n"), false);
    });
    optionTray.appendChild(submit);
  }

  actions.forEach((option) => {
    const button = createOptionButton(option.label);
    optionTray.appendChild(button);
  });
}

function createOptionButton(label, extraClass = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = ["option-pill", extraClass].filter(Boolean).join(" ");
  button.textContent = label;
  button.setAttribute("aria-label", label);
  button.setAttribute("data-tooltip", label);
  button.addEventListener("click", () => {
    pauseSpeech();
    collapseExpandedMessages();
    const reportScope = reportScopeForOption(label);
    if (reportScope) {
      button.disabled = true;
      downloadMitigationReport(reportScope)
        .catch((error) => {
          console.error("Report download failed", error);
          addMessage("bot", error.message || "Could not generate the report.", true);
        })
        .finally(() => {
          button.disabled = false;
        });
      return;
    }
    if (label === "Dive deeper into statistical findings") {
      openStatsDeepDiveDialog();
      return;
    }
    if (isQuickSelectPopulationLabel(label)) {
      openTargetPopulationDialog();
      return;
    }
    if (handleLocalHazardAction(label)) {
      return;
    }
    disableOldOptions();
    sendMessage(label, true);
  });
  return button;
}

function renderOtherOptionsMenu() {
  const navOptions = appState.currentOtherOptions || [];
  if (!navOptions.length) return;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "option-pill other-options-toggle";
  toggle.textContent = "Other Options";
  toggle.dataset.otherToggle = "true";
  toggle.setAttribute("aria-label", "Other Options");
  toggle.setAttribute("data-tooltip", "Other Options");
  toggle.addEventListener("click", () => {
    pauseSpeech();
    const existingButtons = Array.from(optionTray.querySelectorAll("[data-other-nav='true']"));
    if (existingButtons.length) {
      existingButtons.forEach((button) => button.remove());
      toggle.classList.remove("is-open");
      return;
    }
    let insertAfter = toggle;
    navOptions.forEach((label) => {
      const button = createOptionButton(label, "other-option-pill");
      button.dataset.otherNav = "true";
      button.setAttribute("data-tooltip", label);
      optionTray.insertBefore(button, insertAfter.nextSibling);
      insertAfter = button;
    });
    toggle.classList.add("is-open");
  });

  optionTray.appendChild(toggle);
}

function setStatsDialogLoading(value) {
  statsDialogLoading = value;
  if (statsDialogInput) statsDialogInput.disabled = value;
  if (statsDialogSendButton) statsDialogSendButton.disabled = value;
}

function showStatsDeepDiveDialog() {
  if (!statsDeepDiveDialog) return;
  if (typeof statsDeepDiveDialog.showModal === "function") {
    if (!statsDeepDiveDialog.open) statsDeepDiveDialog.showModal();
  } else {
    statsDeepDiveDialog.removeAttribute("hidden");
  }
  statsDialogInput?.focus();
}

async function openStatsDeepDiveDialog(initialMessage = "", echoInitial = true) {
  showStatsDeepDiveDialog();
  const message = String(initialMessage || "").trim();
  if (statsDialogStarted) {
    if (message) await sendStatsDialogMessage(message, echoInitial);
    return;
  }
  statsDialogStarted = true;
  clearElement(statsDialogLog);
  const firstMessage = message
    || "Dive deeper into the statistical findings for the listed hazards. Summarise the most important results and affected groups.";
  await sendStatsDialogMessage(firstMessage, Boolean(message) ? echoInitial : false);
}

function closeStatsDeepDiveDialog() {
  pauseSpeech();
  if (!statsDeepDiveDialog) return;
  if (typeof statsDeepDiveDialog.close === "function") {
    statsDeepDiveDialog.close();
  } else {
    statsDeepDiveDialog.setAttribute("hidden", "");
  }
  messageInput.focus();
}

function showTargetPopulationDialog() {
  if (!targetPopulationDialog) return;
  if (typeof targetPopulationDialog.showModal === "function") {
    if (!targetPopulationDialog.open) targetPopulationDialog.showModal();
  } else {
    targetPopulationDialog.removeAttribute("hidden");
  }
}

function closeTargetPopulationDialog() {
  if (!targetPopulationDialog) return;
  if (typeof targetPopulationDialog.close === "function") {
    targetPopulationDialog.close();
  } else {
    targetPopulationDialog.setAttribute("hidden", "");
  }
}

function openMethodologyDialog() {
  if (!methodologyDialog) return;
  if (typeof methodologyDialog.showModal === "function") {
    if (!methodologyDialog.open) methodologyDialog.showModal();
  } else {
    methodologyDialog.removeAttribute("hidden");
  }
  methodologyFrame?.focus();
}

function closeMethodologyDialog() {
  if (!methodologyDialog) return;
  if (typeof methodologyDialog.close === "function") {
    methodologyDialog.close();
  } else {
    methodologyDialog.setAttribute("hidden", "");
  }
  messageInput?.focus();
}

function syncSurveyResultsZoom() {
  if (!surveyResultsImage) return;
  surveyResultsImage.style.width = `${surveyResultsZoom * 100}%`;
  surveyResultsImage.style.maxWidth = surveyResultsZoom > 1 ? "none" : "100%";
}

function setSurveyResultsZoom(value) {
  surveyResultsZoom = Math.min(3, Math.max(0.5, Number(value) || 1));
  syncSurveyResultsZoom();
}

function openSurveyResultsDialog() {
  if (!surveyResultsDialog) return;
  setSurveyResultsZoom(1);
  if (surveyResultsViewport) {
    surveyResultsViewport.scrollTop = 0;
    surveyResultsViewport.scrollLeft = 0;
  }
  if (typeof surveyResultsDialog.showModal === "function") {
    if (!surveyResultsDialog.open) surveyResultsDialog.showModal();
  } else {
    surveyResultsDialog.removeAttribute("hidden");
  }
  surveyResultsZoomIn?.focus();
}

function closeSurveyResultsDialog() {
  if (!surveyResultsDialog) return;
  if (typeof surveyResultsDialog.close === "function") {
    surveyResultsDialog.close();
  } else {
    surveyResultsDialog.setAttribute("hidden", "");
  }
  messageInput?.focus();
}

function syncPlatformUsersZoom() {
  if (!platformUsersImage) return;
  platformUsersImage.style.width = `${platformUsersZoom * 100}%`;
  platformUsersImage.style.maxWidth = platformUsersZoom > 1 ? "none" : "100%";
}

function setPlatformUsersZoom(value) {
  platformUsersZoom = Math.min(3, Math.max(0.5, Number(value) || 1));
  syncPlatformUsersZoom();
}

function openPlatformUsersDialog() {
  if (!platformUsersDialog) return;
  setPlatformUsersZoom(1);
  if (platformUsersViewport) {
    platformUsersViewport.scrollTop = 0;
    platformUsersViewport.scrollLeft = 0;
  }
  if (typeof platformUsersDialog.showModal === "function") {
    if (!platformUsersDialog.open) platformUsersDialog.showModal();
  } else {
    platformUsersDialog.removeAttribute("hidden");
  }
  platformUsersZoomIn?.focus();
}

function closePlatformUsersDialog() {
  if (!platformUsersDialog) return;
  if (typeof platformUsersDialog.close === "function") {
    platformUsersDialog.close();
  } else {
    platformUsersDialog.setAttribute("hidden", "");
  }
  messageInput?.focus();
}

function selectedTargetPopulationLabels(questionId) {
  const answer = [...targetPopulationAnswers].reverse().find(
    (item) => Number(item.question_id) === Number(questionId),
  );
  const selected = Array.isArray(answer?.selected)
    ? answer.selected
    : String(answer?.answer || "").split(",");
  return new Set(
    selected
      .map((label) => normalizeForMatch(label))
      .filter(Boolean),
  );
}

function openTargetPopulationDialog() {
  if (!targetPopulationDialogBody) return;
  const questions = targetPopulationQuestions.length
    ? targetPopulationQuestions
    : currentTargetPopulationQuestion
      ? [currentTargetPopulationQuestion]
      : [];
  clearElement(targetPopulationDialogBody);
  questions.forEach((question) => {
    const previouslySelected = selectedTargetPopulationLabels(question.id);
    const section = document.createElement("fieldset");
    section.className = "target-dialog-question";
    section.dataset.questionId = question.id;
    const legend = document.createElement("legend");
    legend.textContent = question.question || "Affected population group question";
    section.appendChild(legend);
    const optionGrid = document.createElement("div");
    optionGrid.className = "target-dialog-options";
    (question.options || []).forEach((option) => {
      const label = document.createElement("label");
      label.className = "target-option-check";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = option;
      checkbox.dataset.quickTargetOption = "true";
      checkbox.checked = previouslySelected.has(normalizeForMatch(option));
      const span = document.createElement("span");
      span.textContent = option;
      label.setAttribute("data-tooltip", option);
      label.appendChild(checkbox);
      label.appendChild(span);
      optionGrid.appendChild(label);
    });
    section.appendChild(optionGrid);
    targetPopulationDialogBody.appendChild(section);
  });
  showTargetPopulationDialog();
}

function targetPopulationBatchPayload() {
  return Array.from(targetPopulationDialogBody?.querySelectorAll(".target-dialog-question") || [])
    .map((section) => ({
      question_id: Number(section.dataset.questionId),
      answers: Array.from(section.querySelectorAll("[data-quick-target-option='true']:checked"))
        .map((input) => input.value)
        .filter(Boolean),
    }))
    .filter((item) => item.question_id && item.answers.length);
}

async function sendStatsDialogMessage(message, echoUser = true) {
  if (statsDialogLoading || !statsDialogLog) return;
  const cleanMessage = message.trim();
  if (!cleanMessage) {
    flashRequiredField(statsDialogInput);
    return;
  }
  collapseExpandedMessages(statsDialogLog);
  if (echoUser) addMessage("user", cleanMessage, false, statsDialogLog);

  const typing = addTyping(statsDialogLog);
  setStatsDialogLoading(true);

  try {
    const response = await csrfFetch("/api/stats-deep-dive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: cleanMessage,
        session_id: sessionId,
        validation_mode: currentValidationMode(),
        crowd_sourcing_enabled: crowdSourcingEnabled(),
      }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);

    const data = await response.json();
    sessionId = data.session_id;
    localStorage.setItem(sessionKey, sessionId);

    typing.remove();
    const botRow = addMessage("bot", "", data.error, statsDialogLog);
    speakServerMessage(data.bot_message, data.voice_summary);
    await typeServerMessage(botRow, data.bot_message, statsDialogLog);
    updateSessionCard(data.session);
    loadSessions();
  } catch (error) {
    typing.remove();
    console.error("Stats deep dive request failed", error);
  } finally {
    setStatsDialogLoading(false);
    statsDialogInput?.focus();
  }
}

function inputStateKey(id = sessionId) {
  return id ? `${inputStateKeyPrefix}${id}` : "";
}

function saveCurrentInputState() {
  const key = inputStateKey();
  if (!key) return;
  const state = {
    inputMode: appState.inputMode,
    message: messageInput.value,
    textareaMessage: textareaInput.value,
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
    if (state.inputMode && state.inputMode !== appState.inputMode) return;
    messageInput.value = state.message || "";
    textareaInput.value = state.textareaMessage || "";
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
    renderEmptyState(sessionsList, "Loading sessions...");
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
    renderEmptyState(sessionsList, "Could not load sessions.");
    console.error("Sessions request failed", error);
  }
}

function renderSessions(sessions) {
  if (!sessionsList) return;
  clearElement(sessionsList);
  if (!sessions.length) {
    renderEmptyState(sessionsList, "No saved sessions yet.");
    return;
  }

  sessions.forEach((item) => {
    const isCurrentSession = item.session_id === sessionId;
    const button = document.createElement("button");
    button.type = "button";
    button.className = isCurrentSession ? "session-item is-current" : "session-item";
    button.dataset.sessionId = item.session_id;
    if (isCurrentSession) {
      button.setAttribute("aria-current", "true");
    }
    const titleLine = document.createElement("span");
    titleLine.className = "session-title-line";
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = item.title || "New policy session";
    titleLine.appendChild(title);
    if (isCurrentSession) {
      const currentLabel = document.createElement("span");
      currentLabel.className = "session-current-label";
      currentLabel.textContent = "Current";
      titleLine.appendChild(currentLabel);
    }
    const updatedAt = document.createElement("small");
    updatedAt.textContent = formatSessionDate(item.updated_at);
    button.appendChild(titleLine);
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

function selectedFileInfo(input) {
  return Array.from(input?.files || []).map((file) => ({
    name: file.name,
    size: file.size,
    type: file.type || "",
    last_modified: file.lastModified ? new Date(file.lastModified).toISOString() : null,
  }));
}

function currentInputExportValues() {
  const savedState = inputStateKey() ? localStorage.getItem(inputStateKey()) : null;
  return {
    session_id: sessionId,
    step: appState.currentStep,
    input_mode: appState.inputMode,
    validation_mode: currentValidationMode(),
    crowd_sourcing_enabled: crowdSourcingEnabled(),
    visible_session: appState.currentSession,
    visible_options: appState.currentOptions,
    visible_other_options: appState.currentOtherOptions,
    text_message: messageInput?.value || "",
    textarea_message: textareaInput?.value || "",
    reason: reasonInput?.value || "",
    secondary_reason: secondaryReasonInput?.value || "",
    evidence_url: evidenceInput?.value || "",
    evidence_files: selectedFileInfo(evidenceFileInput),
    evaluation_score: scoreInput?.value || "",
    evaluation_reason: evaluationReasonInput?.value || "",
    evaluation_evidence_url: evaluationEvidenceInput?.value || "",
    evaluation_evidence_files: selectedFileInfo(evaluationEvidenceFileInput),
    stats_dialog_input: statsDialogInput?.value || "",
    saved_local_input_state: savedState,
  };
}

async function exportCurrentSession() {
  if (!exportSessionButton || !exportSessionStatus) return;
  if (!sessionId) {
    exportSessionStatus.textContent = "No active session to export.";
    exportSessionStatus.hidden = false;
    return;
  }
  exportSessionButton.disabled = true;
  exportSessionStatus.textContent = "Preparing export...";
  exportSessionStatus.hidden = false;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/export`);
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok || data.error) {
      throw new Error(data.detail || "Could not export session.");
    }
    data.client_input_values = currentInputExportValues();
    data.export_notes = {
      evidence_file_contents: "Browser export includes selected file names and metadata, not local file bytes.",
    };
    const filename = window.DrTransitionSessionExport.exportFilename(data.session?.title);
    exportSessionStatus.textContent = "Choose where to save the session export...";
    const saveMode = await window.DrTransitionSessionExport.saveJsonFile(filename, data);
    exportSessionStatus.textContent =
      saveMode === "saved"
        ? "Session export saved."
        : "Session export downloaded.";
  } catch (error) {
    if (error?.name === "AbortError") {
      exportSessionStatus.textContent = "Session export cancelled.";
    } else {
      exportSessionStatus.textContent = error.message || "Could not export session.";
    }
  } finally {
    exportSessionButton.disabled = false;
  }
}

function chooseSessionImportFile() {
  importSessionInput?.click();
}

async function importSessionFile() {
  if (!importSessionInput || !exportSessionStatus) return;
  const file = importSessionInput.files?.[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".json")) {
    exportSessionStatus.textContent = "Please choose an exported session JSON file.";
    exportSessionStatus.hidden = false;
    importSessionInput.value = "";
    return;
  }
  if (importSessionButton) importSessionButton.disabled = true;
  exportSessionStatus.textContent = "Importing session...";
  exportSessionStatus.hidden = false;
  try {
    const formData = new FormData();
    formData.append("file", file);
    const response = await csrfFetch("/api/sessions/import", {
      method: "POST",
      body: formData,
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok || data.error) {
      throw new Error(data.detail || "Could not import session.");
    }
    exportSessionStatus.textContent = `Imported session with ${Number(data.messages || 0)} message(s).`;
    await loadSessions();
    await restoreSession(data.session_id);
  } catch (error) {
    exportSessionStatus.textContent = error.message || "Could not import session.";
  } finally {
    importSessionInput.value = "";
    if (importSessionButton) importSessionButton.disabled = false;
  }
}

function showSyncStatus(message, isError = false) {
  if (!syncStatus) return;
  syncStatus.textContent = message;
  syncStatus.hidden = false;
  syncStatus.classList.toggle("success", !isError);
}

function syncSummary(data) {
  const pushedRows = Number(data?.pushed?.rows || 0);
  const pulled = data?.pulled || {};
  const inserted = Number(pulled.inserted || 0);
  const updated = Number(pulled.updated || 0);
  const skipped = Number(pulled.skipped || 0);
  const dirtyScopes = Array.isArray(pulled.knowledge_scopes_dirty)
    ? pulled.knowledge_scopes_dirty
    : [];
  const changes = [];
  if (inserted) changes.push(`${inserted} added`);
  if (updated) changes.push(`${updated} updated`);
  if (skipped) changes.push(`${skipped} skipped`);
  const pulledText = changes.length ? changes.join(", ") : "no inbound changes";
  const kbText = dirtyScopes.length ? ` KB index refresh: ${dirtyScopes.join(", ")}.` : "";
  return `Synced ${pushedRows} local row(s); ${pulledText}.${kbText}`;
}

function setSyncPanelVisible(visible) {
  if (syncSettingsPanel) syncSettingsPanel.hidden = !visible;
  if (!visible && syncStatus) {
    syncStatus.hidden = true;
    syncStatus.textContent = "";
  }
}

async function loadSyncStatus() {
  if (!syncStatus || !syncSettingsPanel) return;
  setSyncPanelVisible(false);
  try {
    const response = await fetch("/api/sync/client/status");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await response.json();
    setSyncPanelVisible(true);
    if (syncMyDataToggle) {
      syncMyDataToggle.disabled = false;
      syncMyDataToggle.checked = Boolean(data.user_data_sync?.enabled);
    }
    if (!data.enabled) {
      if (syncNowButton) syncNowButton.hidden = true;
      const userDataText = data.user_data_sync?.enabled
        ? "Sync my Data is on. It will apply when sync is enabled."
        : "Sync is disabled. Sync my Data is off.";
      showSyncStatus(userDataText, !data.user_data_sync?.enabled);
      return;
    }
    if (syncNowButton) syncNowButton.hidden = false;
    if (!data.configured) {
      if (syncNowButton) syncNowButton.disabled = true;
      const userDataText = data.user_data_sync?.enabled
        ? " Sync my Data is on for new data."
        : " Sync my Data is off.";
      showSyncStatus(`Sync is not configured. Add server URL and token in .env.${userDataText}`, true);
      return;
    }
    if (syncNowButton) syncNowButton.disabled = false;
    const interval = Number(data.interval_seconds || 0);
    const intervalText = interval > 0 ? ` Auto-sync every ${Math.round(interval / 60)} min.` : "";
    const userDataText = data.user_data_sync?.enabled
      ? " Sync my Data is on for new data."
      : " Sync my Data is off.";
    showSyncStatus(`Connected to ${data.server_url || "sync server"}.${intervalText}${userDataText}`);
  } catch (error) {
    if (syncNowButton) syncNowButton.disabled = true;
    if (syncMyDataToggle) syncMyDataToggle.disabled = true;
    setSyncPanelVisible(true);
    showSyncStatus("Could not read sync status.", true);
  }
}

async function updateSyncMyDataPreference() {
  if (!syncMyDataToggle) return;
  const enabled = syncMyDataToggle.checked;
  syncMyDataToggle.disabled = true;
  showSyncStatus(enabled ? "Enabling Sync my Data..." : "Disabling Sync my Data...");
  try {
    const response = await csrfFetch("/api/sync/client/user-data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok || data.error) {
      throw new Error(data.detail || "Could not update Sync my Data.");
    }
    syncMyDataToggle.checked = Boolean(data.user_data_sync?.enabled);
    await loadSyncStatus();
  } catch (error) {
    syncMyDataToggle.checked = !enabled;
    showSyncStatus(error.message || "Could not update Sync my Data.", true);
  } finally {
    syncMyDataToggle.disabled = false;
  }
}

async function runManualSync() {
  if (!syncNowButton) return;
  if (syncNowButton.hidden) return;
  syncNowButton.disabled = true;
  showSyncStatus("Syncing...");
  try {
    const response = await csrfFetch("/api/sync/client/run", { method: "POST" });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok || data.error) {
      throw new Error(data.detail || "Could not sync with the server.");
    }
    showSyncStatus(syncSummary(data));
    await loadSessions();
  } catch (error) {
    showSyncStatus(error.message || "Could not sync with the server.", true);
  } finally {
    syncNowButton.disabled = false;
  }
}

function showKnowledgeMessage(message, isError = true) {
  if (!knowledgeMessage) return;
  knowledgeMessage.textContent = message;
  knowledgeMessage.hidden = false;
  knowledgeMessage.classList.toggle("success", !isError);
}

function showSectorPromptMessage(message, isError = true) {
  if (!sectorPromptMessage) return;
  sectorPromptMessage.textContent = message;
  sectorPromptMessage.hidden = false;
  sectorPromptMessage.classList.toggle("success", !isError);
}

function knowledgeSvgIcon(pathData, className = "knowledge-inline-icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add(className);
  const paths = Array.isArray(pathData) ? pathData : [pathData];
  paths.forEach((pathValue) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathValue);
    svg.appendChild(path);
  });
  return svg;
}

function assignKnowledgeFiles(files) {
  if (!knowledgeFileInput || !files?.length) return;
  const transfer = new DataTransfer();
  Array.from(files).forEach((file) => transfer.items.add(file));
  knowledgeFileInput.files = transfer.files;
}

function resetKnowledgeProgress() {
  if (!knowledgeProgressSection || !knowledgeProgressList) return;
  clearElement(knowledgeProgressList);
  knowledgeProgressSection.hidden = true;
}

function addKnowledgeProgressRow(label, status = "Queued") {
  if (!knowledgeProgressSection || !knowledgeProgressList) return null;
  knowledgeProgressSection.hidden = false;
  const row = createElement("article", { className: "knowledge-progress-row" }, [
    knowledgeSvgIcon(["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6"]),
    createElement("div", { className: "knowledge-progress-main" }, [
      createElement("strong", { text: label, attrs: { title: label } }),
      createElement("small", { text: status }),
      createElement("div", { className: "knowledge-progress-track", attrs: { "aria-hidden": "true" } }, [
        createElement("span"),
      ]),
    ]),
  ]);
  knowledgeProgressList.appendChild(row);
  return row;
}

function updateKnowledgeProgressRow(row, status, percent, state = "") {
  if (!row) return;
  row.classList.toggle("done", state === "done");
  row.classList.toggle("failed", state === "failed");
  const statusElement = row.querySelector("small");
  const bar = row.querySelector(".knowledge-progress-track span");
  if (statusElement) statusElement.textContent = status;
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function uploadKnowledgeFile(file, row) {
  return new Promise((resolve) => {
    const formData = new FormData();
    formData.append("files", file);
    const csrfToken = cookieValue("dr_transition_csrf");
    if (csrfToken) {
      formData.append("csrf_token", csrfToken);
    }
    const request = new XMLHttpRequest();
    request.open("POST", "/api/knowledge/upload");
    if (csrfToken) {
      request.setRequestHeader("X-CSRF-Token", csrfToken);
    }
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) {
        updateKnowledgeProgressRow(row, "Uploading...", 12);
        return;
      }
      const percent = Math.round((event.loaded / event.total) * 70);
      updateKnowledgeProgressRow(row, `Uploading ${Math.max(1, percent)}%`, percent);
    });
    request.upload.addEventListener("load", () => {
      updateKnowledgeProgressRow(row, "Embedding and indexing...", 75);
    });
    request.addEventListener("load", () => {
      let data = { error: true, detail: "Could not ingest file." };
      try {
        data = JSON.parse(request.responseText || "{}");
      } catch (error) {
        console.error("Knowledge file response parse failed", error);
      }
      if (request.status >= 400) {
        data = {
          ...data,
          error: true,
          detail: data.detail || `Upload failed with status ${request.status}.`,
        };
      }
      resolve(data);
    });
    request.addEventListener("error", () => {
      resolve({ error: true, detail: "Upload failed before ingestion started." });
    });
    updateKnowledgeProgressRow(row, "Uploading...", 5);
    request.send(formData);
  });
}

async function openKnowledgeDialog() {
  if (!knowledgeDialog) return;
  if (typeof knowledgeDialog.showModal === "function") {
    knowledgeDialog.showModal();
  } else {
    knowledgeDialog.removeAttribute("hidden");
  }
  await loadKnowledgeDocuments();
}

function closeKnowledgeDialog() {
  if (!knowledgeDialog) return;
  if (typeof knowledgeDialog.close === "function") {
    knowledgeDialog.close();
  } else {
    knowledgeDialog.setAttribute("hidden", "");
  }
}

async function loadKnowledgeDocuments() {
  if (!knowledgeDocuments) return;
  renderEmptyState(knowledgeDocuments, "Loading documents...");
  try {
    const response = await fetch("/api/knowledge");
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    renderKnowledgeDocuments(data.documents || []);
  } catch (error) {
    if (knowledgeDocumentCount) knowledgeDocumentCount.textContent = "0";
    renderEmptyState(knowledgeDocuments, "Could not load documents.");
    console.error("Knowledge documents failed", error);
  }
}

function renderKnowledgeDocuments(documents) {
  if (!knowledgeDocuments) return;
  clearElement(knowledgeDocuments);
  if (knowledgeDocumentCount) knowledgeDocumentCount.textContent = String(documents.length || 0);
  if (!documents.length) {
    renderEmptyState(knowledgeDocuments, "No main knowledge documents yet.");
    return;
  }
  documents.forEach((documentItem) => {
    const title = String(documentItem.title || "");
    const sourceType = String(documentItem.source_type || "document").toUpperCase();
    const row = createElement("article", { className: "knowledge-item knowledge-document-row" }, [
      createElement("span", { className: "knowledge-document-icon" }, [
        knowledgeSvgIcon(["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6", "M8 13h8", "M8 17h5"], "knowledge-document-svg"),
      ]),
      createElement("div", { className: "knowledge-document-main" }, [
        createElement("strong", { text: title, attrs: { title } }),
        createElement("small", { text: sourceType }),
      ]),
    ]);
    if (canManageMainKnowledge) {
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "knowledge-delete-button";
      deleteButton.setAttribute("aria-label", `Delete ${title}`);
      deleteButton.title = "Delete document";
      deleteButton.appendChild(
        knowledgeSvgIcon(["M3 6h18", "M8 6V4h8v2", "M10 11v6", "M14 11v6", "M5 6l1 15h12l1-15"], "knowledge-delete-svg"),
      );
      deleteButton.addEventListener("click", () => deleteKnowledgeDocument(documentItem.id));
      row.appendChild(deleteButton);
    }
    knowledgeDocuments.appendChild(row);
  });
}

function renderKnowledgeResults(results) {
  if (!knowledgeResults) return;
  clearElement(knowledgeResults);
  if (!results.length) {
    renderEmptyState(knowledgeResults, "No matching chunks found.");
    return;
  }
  results.forEach((result) => {
    const sourceParts = [result.source_type || "document"];
    if (result.page_number) sourceParts.push(`page ${result.page_number}`);
    const row = createElement("article", { className: "knowledge-item" }, [
      knowledgeSvgIcon(["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6"], "knowledge-result-svg"),
      createElement("div", { className: "knowledge-result-main" }, [
        createElement("strong", { text: result.title || "" }),
        createElement("small", { text: `${sourceParts.join(" - ")} - Score: ${result.score}` }),
        createElement("p", { text: result.content || "" }),
      ]),
    ]);
    knowledgeResults.appendChild(row);
  });
}

function renderSectorPromptResults(results) {
  if (!sectorPromptResults) return;
  clearElement(sectorPromptResults);
  if (!results.length) {
    renderEmptyState(sectorPromptResults, "No matching sector prompt chunks found.");
    return;
  }
  results.forEach((result) => {
    const sourceParts = [result.source_type || "sector_prompt"];
    if (result.source_uri) sourceParts.push(result.source_uri.replace("sector-prompt://", ""));
    const scoreLabel =
      result.score === null || typeof result.score === "undefined"
        ? "lexical/DB"
        : result.score;
    const row = createElement("article", { className: "knowledge-item" }, [
      knowledgeSvgIcon(["M12 3 20 7.5 12 12 4 7.5 12 3Z", "M4 12l8 4.5 8-4.5", "M4 16.5 12 21l8-4.5"], "knowledge-result-svg"),
      createElement("div", { className: "knowledge-result-main" }, [
        createElement("strong", { text: result.title || "Sector prompt" }),
        createElement("small", { text: `${sourceParts.join(" - ")} - Score: ${scoreLabel}` }),
        createElement("p", { text: result.content || "" }),
      ]),
    ]);
    sectorPromptResults.appendChild(row);
  });
}

function sectorPromptReindexDetail(data) {
  const indexed = Array.isArray(data.indexed) ? data.indexed.length : 0;
  const skipped = Array.isArray(data.skipped) ? data.skipped.length : 0;
  const failures = Array.isArray(data.failures) ? data.failures.length : 0;
  const cleanup = data.cleanup || {};
  const lexicalOnly = Array.isArray(data.indexed)
    ? data.indexed.filter((item) => item && item.vector_indexed === false).length
    : 0;
  const parts = [
    `${Number(cleanup.deleted_documents || 0)} old documents removed`,
    `${Number(cleanup.deleted_chunks || 0)} old chunks removed`,
    cleanup.reset_faiss
      ? "old FAISS reset"
      : cleanup.removed_faiss
        ? "old FAISS removed"
        : "old FAISS removal failed",
    `${indexed} indexed`,
    `${skipped} already current`,
  ];
  if (lexicalOnly) parts.push(`${lexicalOnly} lexical-only`);
  if (failures) parts.push(`${failures} failed`);
  if (cleanup.faiss_error) parts.push(`FAISS error: ${cleanup.faiss_error}`);
  return `Sector prompts reindexed: ${parts.join(", ")}.`;
}

async function reindexSectorPrompts() {
  if (!sectorPromptReindexButton) return;
  sectorPromptReindexButton.disabled = true;
  showSectorPromptMessage("Reindexing sector prompts...", false);
  try {
    const response = await csrfFetch("/api/sector-prompts/reindex", { method: "POST" });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    showSectorPromptMessage(
      data.detail || sectorPromptReindexDetail(data),
      Boolean(data.error),
    );
  } catch (error) {
    console.error("Sector prompt reindex failed", error);
    showSectorPromptMessage("Could not reindex sector prompts.");
  } finally {
    sectorPromptReindexButton.disabled = false;
  }
}

async function searchSectorPrompts() {
  const query = sectorPromptSearchInput?.value.trim() || "";
  const sector = sectorPromptSectorInput?.value || "";
  if (!query) {
    flashRequiredField(sectorPromptSearchInput);
    return;
  }
  if (sectorPromptResults) {
    renderEmptyState(sectorPromptResults, "Searching sector prompts...");
  }
  try {
    const response = await csrfFetch("/api/sector-prompts/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sector, query }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) {
      showSectorPromptMessage(data.detail || "Could not search sector prompts.");
      renderSectorPromptResults([]);
      return;
    }
    showSectorPromptMessage(`Found ${(data.results || []).length} sector prompt chunks.`, false);
    renderSectorPromptResults(data.results || []);
  } catch (error) {
    console.error("Sector prompt search failed", error);
    showSectorPromptMessage("Could not search sector prompts.");
    renderSectorPromptResults([]);
  }
}

async function openPromptLibraryDialog() {
  if (!promptLibraryDialog) return;
  if (typeof promptLibraryDialog.showModal === "function") {
    promptLibraryDialog.showModal();
  } else {
    promptLibraryDialog.removeAttribute("hidden");
  }
  await loadPromptSourceSetting();
  await loadPrompts();
  promptSearchInput?.focus();
}

function closePromptLibraryDialog() {
  if (!promptLibraryDialog) return;
  if (typeof promptLibraryDialog.close === "function") {
    promptLibraryDialog.close();
  } else {
    promptLibraryDialog.setAttribute("hidden", "");
  }
}

function showPromptEditorMessage(message, isError = true) {
  if (!promptEditorMessage) return;
  promptEditorMessage.textContent = message;
  promptEditorMessage.hidden = false;
  promptEditorMessage.classList.toggle("success", !isError);
}

function showPromptSourceMessage(message, isError = true) {
  if (!promptSourceMessage) return;
  promptSourceMessage.textContent = message;
  promptSourceMessage.hidden = false;
  promptSourceMessage.classList.toggle("success", !isError);
}

async function loadPromptSourceSetting() {
  if (!promptSourceSelect) return;
  promptSourceSelect.disabled = true;
  try {
    const response = await fetch("/api/settings/prompt-source");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Could not load prompt source.");
    promptSourceSelect.value = data.prompt_source || "auto";
    if (promptSourceMessage) promptSourceMessage.hidden = true;
  } catch (error) {
    console.error("Prompt source load failed", error);
    showPromptSourceMessage("Could not load prompt source.");
  } finally {
    promptSourceSelect.disabled = false;
  }
}

async function updatePromptSourceSetting() {
  if (!promptSourceSelect || promptSourceLoading) return;
  const promptSource = promptSourceSelect.value;
  promptSourceLoading = true;
  promptSourceSelect.disabled = true;
  showPromptSourceMessage("Updating prompt source...", false);
  try {
    const response = await csrfFetch("/api/settings/prompt-source", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_source: promptSource }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Could not update prompt source.");
    promptSourceSelect.value = data.prompt_source || promptSource;
    showPromptSourceMessage(data.detail || "Prompt source updated.", false);
    await loadPrompts();
  } catch (error) {
    console.error("Prompt source update failed", error);
    showPromptSourceMessage(error.message || "Could not update prompt source.");
    await loadPromptSourceSetting();
  } finally {
    promptSourceLoading = false;
    promptSourceSelect.disabled = false;
  }
}

async function loadPrompts() {
  if (!promptList) return;
  renderEmptyState(promptList, "Loading prompts...");
  try {
    const response = await fetch("/api/prompts");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    promptRows = data.prompts || [];
    renderPromptList();
    configurePromptMutationControls();
  } catch (error) {
    console.error("Prompt list failed", error);
    promptRows = [];
    if (promptCatalogueCount) promptCatalogueCount.textContent = "0";
    renderEmptyState(promptList, "Could not load prompts.");
  }
}

function configurePromptMutationControls() {
  if (newPromptButton) newPromptButton.disabled = !canManagePrompts;
}

function renderPromptList() {
  if (!promptList) return;
  const filter = (promptSearchInput?.value || "").trim().toLowerCase();
  const rows = promptRows.filter((prompt) => {
    const haystack = [
      prompt.prompt_key,
      prompt.display_name,
      prompt.category,
      prompt.model,
      prompt.content_preview,
    ].join(" ").toLowerCase();
    return !filter || haystack.includes(filter);
  });
  if (promptCatalogueCount) promptCatalogueCount.textContent = String(rows.length);
  clearElement(promptList);
  if (!rows.length) {
    renderEmptyState(promptList, "No prompts match this filter.");
    return;
  }
  rows.forEach((prompt) => {
    const row = createElement("button", {
      className: "knowledge-item prompt-list-item",
      text: "",
      attrs: { type: "button" },
    }, [
      createElement("span", { className: "prompt-list-icon" }, [
        promptSvgIcon(["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6", "M8 13h8", "M8 17h5"], "prompt-document-svg"),
      ]),
      createElement("span", { className: "prompt-list-main" }, [
        createElement("span", { className: "prompt-list-title", text: prompt.display_name || prompt.prompt_key }),
        createElement("span", { className: "prompt-list-meta" }, [
          createElement("span", { text: prompt.category || "prompt" }),
          ...(prompt.model ? [createElement("span", { text: prompt.model })] : []),
          ...(prompt.updated_at ? [createElement("span", { text: promptUpdatedLabel(prompt.updated_at) })] : []),
        ]),
        createElement("span", { className: "prompt-list-preview", text: prompt.content_preview || "" }),
      ]),
      promptSvgIcon("m9 18 6-6-6-6", "prompt-chevron-svg"),
    ]);
    row.classList.toggle("active", !creatingPrompt && prompt.id === selectedPromptId);
    row.addEventListener("click", () => loadPromptDetail(prompt.id));
    promptList.appendChild(row);
  });
}

function promptMetaLabel(prompt) {
  return [prompt.category || "prompt", prompt.model || "", prompt.updated_at ? promptUpdatedLabel(prompt.updated_at) : ""]
    .filter(Boolean)
    .join(" - ");
}

function promptUpdatedLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function promptSvgIcon(pathData, className = "prompt-inline-icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add(className);
  const paths = Array.isArray(pathData) ? pathData : [pathData];
  paths.forEach((pathValue) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathValue);
    svg.appendChild(path);
  });
  return svg;
}

async function loadPromptDetail(promptId) {
  if (!promptId) return;
  creatingPrompt = false;
  selectedPromptId = promptId;
  renderPromptList();
  if (promptContentInput) {
    promptContentInput.disabled = true;
    promptContentInput.value = "Loading prompt...";
  }
  if (savePromptButton) savePromptButton.disabled = true;
  try {
    const response = await fetch(`/api/prompts/${encodeURIComponent(promptId)}`);
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error || !data.prompt) throw new Error(data.detail || "Prompt not found.");
    renderPromptEditor(data.prompt);
  } catch (error) {
    console.error("Prompt detail failed", error);
    showPromptEditorMessage("Could not load prompt.");
  }
}

function renderPromptEditor(prompt) {
  creatingPrompt = false;
  selectedPromptId = prompt.id;
  if (promptEditorTitle) promptEditorTitle.textContent = prompt.display_name || prompt.prompt_key;
  if (promptEditorMeta) promptEditorMeta.textContent = promptMetaLabel(prompt);
  if (promptKeyField) promptKeyField.hidden = false;
  if (promptKeyInput) {
    promptKeyInput.value = prompt.prompt_key || "";
    promptKeyInput.disabled = false;
    promptKeyInput.readOnly = true;
  }
  if (promptContentInput) {
    promptContentInput.value = prompt.content || "";
    promptContentInput.disabled = !canManagePrompts;
    if (canManagePrompts) promptContentInput.focus();
  }
  if (savePromptButton) savePromptButton.disabled = !canManagePrompts;
  if (promptEditorMessage) {
    promptEditorMessage.hidden = true;
  }
  renderPromptList();
}

function startNewPrompt() {
  if (!canManagePrompts) {
    showPromptEditorMessage("Prompts are managed on the sync server and synced to this client.");
    return;
  }
  creatingPrompt = true;
  selectedPromptId = "";
  if (promptEditorTitle) promptEditorTitle.textContent = "New prompt";
  if (promptEditorMeta) promptEditorMeta.textContent = "Create a database-backed prompt row";
  if (promptKeyField) promptKeyField.hidden = false;
  if (promptKeyInput) {
    promptKeyInput.value = "";
    promptKeyInput.disabled = false;
    promptKeyInput.readOnly = false;
  }
  if (promptContentInput) {
    promptContentInput.value = "";
    promptContentInput.disabled = false;
  }
  if (savePromptButton) savePromptButton.disabled = false;
  if (promptEditorMessage) promptEditorMessage.hidden = true;
  renderPromptList();
  promptKeyInput?.focus();
}

async function saveSelectedPrompt() {
  if (!canManagePrompts) {
    showPromptEditorMessage("Prompts are managed on the sync server and synced to this client.");
    return;
  }
  if (!selectedPromptId || !promptContentInput) return;
  const content = promptContentInput.value.trim();
  if (!content) {
    flashRequiredField(promptContentInput);
    return;
  }
  if (savePromptButton) savePromptButton.disabled = true;
  showPromptEditorMessage("Saving prompt...", false);
  try {
    const response = await csrfFetch(`/api/prompts/${encodeURIComponent(selectedPromptId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Could not save prompt.");
    const index = promptRows.findIndex((prompt) => prompt.id === selectedPromptId);
    if (index >= 0) promptRows[index] = data.prompt;
    renderPromptEditor(data.prompt);
    showPromptEditorMessage("Prompt saved.", false);
  } catch (error) {
    console.error("Prompt save failed", error);
    showPromptEditorMessage(error.message || "Could not save prompt.");
  } finally {
    if (savePromptButton) savePromptButton.disabled = false;
  }
}

async function createPrompt() {
  if (!canManagePrompts) {
    showPromptEditorMessage("Prompts are managed on the sync server and synced to this client.");
    return;
  }
  if (!promptKeyInput || !promptContentInput) return;
  const promptKey = promptKeyInput.value.trim();
  const content = promptContentInput.value.trim();
  if (!promptKey) {
    flashRequiredField(promptKeyInput);
    return;
  }
  if (!content) {
    flashRequiredField(promptContentInput);
    return;
  }
  if (savePromptButton) savePromptButton.disabled = true;
  showPromptEditorMessage("Creating prompt...", false);
  try {
    const response = await csrfFetch("/api/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_key: promptKey, content }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    if (data.error) throw new Error(data.detail || "Could not create prompt.");
    promptRows = [data.prompt, ...promptRows.filter((prompt) => prompt.id !== data.prompt.id)];
    renderPromptEditor(data.prompt);
    showPromptEditorMessage("Prompt created.", false);
    await loadPrompts();
    await loadPromptDetail(data.prompt.id);
  } catch (error) {
    console.error("Prompt create failed", error);
    showPromptEditorMessage(error.message || "Could not create prompt.");
  } finally {
    if (savePromptButton) savePromptButton.disabled = false;
  }
}

async function deleteKnowledgeDocument(documentId) {
  const response = await csrfFetch(`/api/knowledge/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });
  const data = await response.json();
  showKnowledgeMessage(data.deleted ? "Document deleted." : "Could not delete document.", !data.deleted);
  await loadKnowledgeDocuments();
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
    const response = await csrfFetch(`/api/sessions/${encodeURIComponent(targetSessionId)}`, {
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
  let shouldStartFresh = false;
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
    clearElement(chatLog);
    clearElement(optionTray);
    statsDialogStarted = false;
    clearElement(statsDialogLog);
    (data.messages || []).forEach((message) => {
      addMessage(message.role, message.content, Boolean(message.is_error));
    });
    updateSessionCard(data.session || {});
    setInputMode(data.input_mode || "text", data.step, data.options || [], data.session);
    applyInputValues(data.input_values);
    renderOptions(data.options || [], data.other_options || []);
    applySavedInputState();
    sessionsPanel.hidden = true;
  } catch (error) {
    console.error("Session restore failed", error);
    if (nextSessionId === sessionId) {
      localStorage.removeItem(sessionKey);
      sessionId = null;
      clearElement(chatLog);
      clearElement(optionTray);
      shouldStartFresh = true;
    }
  } finally {
    setLoading(false);
  }
  if (shouldStartFresh) {
    sendMessage("", false);
  } else {
    scheduleAutoConversationTurn();
  }
}

function autoConversationEnabled() {
  return Boolean(autoConversationToggle?.checked);
}

function stopAutoConversation() {
  if (autoConversationTimer) {
    clearTimeout(autoConversationTimer);
    autoConversationTimer = null;
  }
}

function scheduleAutoConversationTurn(delay = 900) {
  stopAutoConversation();
  if (!autoConversationEnabled() || loading || !sessionId) return;
  if (autoConversationTurns >= autoConversationTurnLimit) {
    if (autoConversationToggle) autoConversationToggle.checked = false;
    localStorage.setItem(autoConversationKey, "false");
    console.warn("Auto conversation stopped after reaching the turn limit.");
    return;
  }
  autoConversationTimer = window.setTimeout(requestAutoConversationTurn, delay);
}

async function requestAutoConversationTurn() {
  autoConversationTimer = null;
  if (!autoConversationEnabled() || loading || !sessionId) return;

  try {
    const response = await csrfFetch("/api/auto-user-message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "",
        session_id: sessionId,
        validation_mode: currentValidationMode(),
        crowd_sourcing_enabled: crowdSourcingEnabled(),
      }),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    let nextMessage = String(data.message || "").trim();
    if (data.error || !nextMessage) {
      console.warn(data.detail || "Auto conversation could not generate a message.");
      scheduleAutoConversationTurn(1800);
      return;
    }
    nextMessage = normalizeAutoConversationMessage(nextMessage);
    autoConversationTurns += 1;
    sendMessage(nextMessage, true);
  } catch (error) {
    console.error("Auto conversation failed", error);
    scheduleAutoConversationTurn(1800);
  }
}

function normalizeAutoConversationMessage(message) {
  const fieldModes = new Set([
    "mitigation_measure",
    "reason_evidence",
    "reason_only",
    "evidence_only",
    "textarea",
    "evaluation_question",
  ]);
  if (!fieldModes.has(appState.inputMode)) return message;
  const optionLabels = [...appState.currentOptions, ...appState.currentOtherOptions].map((option) =>
    typeof option === "string" ? option : option.label,
  );
  const isOption = optionLabels.some((label) => normalizeForMatch(label) === normalizeForMatch(message));
  if (!isOption) {
    if (
      (appState.inputMode === "reason_evidence" || appState.inputMode === "reason_only") &&
      !/^reason\s*:/i.test(message) &&
      !/^evidence\s*:/i.test(message)
    ) {
      return `Reason: ${message}`;
    }
    if (
      appState.inputMode === "mitigation_measure" &&
      !/^mitigation(?:\s+measure)?\s*:/i.test(message)
    ) {
      return `Mitigation measure: ${message}`;
    }
    if (appState.inputMode === "evaluation_question" && !/^score\s*:/i.test(message)) {
      return `Score: 7\nReason: ${message}`;
    }
    return message;
  }
  if (appState.inputMode === "mitigation_measure") {
    return "Mitigation measure: Provide targeted financial support and advisory services for affected groups.";
  }
  if (appState.inputMode === "reason_evidence" || appState.inputMode === "reason_only") {
    return "Reason: This reduces the hazard by lowering costs, improving access, and supporting affected groups through the transition.";
  }
  if (appState.inputMode === "evidence_only") {
    return "Evidence: Published policy evaluation or statistical evidence supporting the hazard.";
  }
  if (appState.inputMode === "textarea") {
    return "The cost coverage applies to the affected target groups by paying or reimbursing upfront adaptation costs directly for them, with guidance and implementation support so they can use the measure in practice.";
  }
  if (appState.inputMode === "evaluation_question") {
    return "Score: 7\nReason: The mitigation is relevant and feasible, but it needs stronger targeting and monitoring.";
  }
  return message;
}

async function sendMessage(message = "", echoUser = false, extras = {}) {
  if (loading) return;
  stopAutoConversation();
  const cleanMessage = message.trim();
  if (echoUser && cleanMessage) addMessage("user", cleanMessage);
  clearCurrentInputState();

  const typing = addTyping();
  setLoading(true);
  let shouldScheduleAuto = false;

  try {
    const hasEvidenceFile = extras.evidenceFile instanceof File && extras.evidenceFile.size > 0;
    const hasEvidenceUrl = Boolean(extras.evidenceUrl);
    const useMultipart = hasEvidenceFile || hasEvidenceUrl;
    const response = await csrfFetch("/api/chat", {
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
              validation_mode: currentValidationMode(),
              crowd_sourcing_enabled: crowdSourcingEnabled(),
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
    appState.currentStep = data.step;
    if (data.step === "stats_deep_dive_dialog") {
      setInputMode(data.input_mode || "text", appState.currentStep, data.options || [], data.session);
      updateSessionCard(data.session);
      renderOptions(data.options || [], data.other_options || []);
      await openStatsDeepDiveDialog(data.input_values?.stats_question || "", true);
      loadSessions();
      return;
    }
    const botRow = addMessage("bot", "", data.error);
    speakServerMessage(data.bot_message, data.voice_summary);
    await typeServerMessage(botRow, data.bot_message);
    renderValidationDetails(botRow, data.validation_details);
    updateSessionCard(data.session);
    setInputMode(data.input_mode || "text", data.step, data.options || [], data.session);
    applyInputValues(data.input_values);
    renderOptions(data.options || [], data.other_options || []);
    loadSessions();
    shouldScheduleAuto = true;
  } catch (error) {
    typing.remove();
    console.error("Chat request failed", error);
  } finally {
    setLoading(false);
    if (["reason_evidence", "reason_only", "mitigation_measure"].includes(appState.inputMode)) {
      reasonInput.focus();
    } else if (appState.inputMode === "evidence_only") {
      evidenceInput.focus();
    } else if (appState.inputMode === "evaluation_question") {
      scoreInput.focus();
    } else if (appState.inputMode === "textarea") {
      textareaInput.focus();
    } else {
      messageInput.focus();
    }
    if (shouldScheduleAuto) {
      scheduleAutoConversationTurn();
    }
  }
}

function buildChatFormData(message, extras = {}) {
  const formData = new FormData();
  formData.append("message", message);
  formData.append("validation_mode", currentValidationMode());
  formData.append("crowd_sourcing_enabled", String(crowdSourcingEnabled()));
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

  if (["reason_evidence", "reason_only", "evidence_only", "mitigation_measure"].includes(appState.inputMode)) {
    const primaryValue = reasonInput.value.trim();
    const evidenceUrl = evidenceInput.value.trim();
    const evidenceFile = evidenceFileInput.files[0];

    if (appState.inputMode === "mitigation_measure") {
      if (!primaryValue) {
        flashRequiredField(reasonInput);
        return;
      }
      const value = `Mitigation measure: ${primaryValue}`;
      reasonInput.value = "";
      collapseExpandedMessages();
      addMessage("user", value);
      sendMessage(value, false);
      return;
    }

    if (appState.inputMode === "evidence_only") {
      if (!evidenceUrl && !(evidenceFile instanceof File && evidenceFile.size > 0)) {
        flashRequiredField(evidenceInput);
        return;
      }
      const value = evidenceSummary(evidenceUrl, evidenceFile).join("\n");
      evidenceInput.value = "";
      evidenceFileInput.value = "";
      collapseExpandedMessages();
      addMessage("user", value);
      sendMessage("", false, { evidenceUrl, evidenceFile });
      return;
    }

    const reasonOptional = appState.currentStep === "socio_demographic_review";
    if (!primaryValue && !reasonOptional) {
      flashRequiredField(reasonInput);
      return;
    }
    const reasonLine = primaryValue ? [`Reason: ${primaryValue}`] : [];
    const value = [...reasonLine, ...evidenceSummary(evidenceUrl, evidenceFile)].join("\n") || "No reason provided";
    reasonInput.value = "";
    evidenceInput.value = "";
    evidenceFileInput.value = "";
    collapseExpandedMessages();
    addMessage("user", value);
    sendMessage(primaryValue ? `Reason: ${primaryValue}` : "", false, {
      evidenceUrl: appState.inputMode === "reason_only" ? "" : evidenceUrl,
      evidenceFile: appState.inputMode === "reason_only" ? null : evidenceFile,
    });
    return;
  }

  if (appState.inputMode === "evaluation_question") {
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
    collapseExpandedMessages();
    addMessage("user", value);
    sendMessage(lines.filter((line) => !line.startsWith("Evidence ")).join("\n"), false, {
      evidenceUrl,
      evidenceFile,
    });
    return;
  }

  const freeTextInput = appState.inputMode === "textarea" ? textareaInput : messageInput;
  const value = freeTextInput.value.trim();
  if (!value) {
    flashRequiredField(freeTextInput);
    return;
  }
  freeTextInput.value = "";
  appState.highlightedOptionLabel = "";
  disableOldOptions();
  collapseExpandedMessages();
  addMessage("user", value);
  sendMessage(value, false);
});

messageInput.addEventListener("input", updateOptionHighlight);
textareaInput.addEventListener("input", updateOptionHighlight);
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

typingEffectToggle?.addEventListener("change", () => {
  localStorage.setItem(typingEffectKey, String(typingEffectToggle.checked));
});

validationModeToggle?.addEventListener("change", () => {
  localStorage.setItem(validationModeKey, validationModeToggle.checked ? "strict" : "easy");
  configureValidationModeControl();
});

crowdSourcingToggle?.addEventListener("change", () => {
  localStorage.setItem(crowdSourcingKey, String(crowdSourcingToggle.checked));
});

autoConversationToggle?.addEventListener("change", () => {
  localStorage.setItem(autoConversationKey, String(autoConversationToggle.checked));
  autoConversationTurns = 0;
  if (autoConversationToggle.checked) {
    scheduleAutoConversationTurn(300);
  } else {
    stopAutoConversation();
  }
});

voicePreferenceSelect?.addEventListener("change", () => {
  localStorage.setItem(voicePreferenceKey, voicePreferenceSelect.value);
  updateVoicePreferenceDisplay();
});

voiceLanguageSelect?.addEventListener("change", () => {
  localStorage.setItem(voiceLanguageKey, voiceLanguageSelect.value);
  localStorage.setItem(voicePreferenceKey, "auto");
  populateVoicePreferenceControls();
});

speechRateInput?.addEventListener("input", () => {
  localStorage.setItem(voiceRateKey, speechRateInput.value);
  updateVoicePreferenceDisplay();
});

speechVolumeInput?.addEventListener("input", () => {
  localStorage.setItem(voiceVolumeKey, speechVolumeInput.value);
  updateVoicePreferenceDisplay();
});

settingsButton?.addEventListener("click", () => {
  if (settingsDrawer?.hidden) {
    openSettingsDrawer();
    loadSyncStatus();
  } else {
    closeSettingsDrawer();
  }
});

closeSettingsButton?.addEventListener("click", closeSettingsDrawer);
voicePreferenceButton?.addEventListener("click", () => {
  closeSettingsDrawer();
  openVoicePreferenceDialog();
});
closeVoicePreferenceButton?.addEventListener("click", closeVoicePreferenceDialog);
previewVoiceButton?.addEventListener("click", previewSelectedVoice);
voicePreferenceDialog?.addEventListener("click", (event) => {
  if (event.target === voicePreferenceDialog) {
    closeVoicePreferenceDialog();
  }
});
exportSessionButton?.addEventListener("click", exportCurrentSession);
importSessionButton?.addEventListener("click", chooseSessionImportFile);
importSessionInput?.addEventListener("change", importSessionFile);
syncNowButton?.addEventListener("click", runManualSync);
syncMyDataToggle?.addEventListener("change", updateSyncMyDataPreference);

document.addEventListener("click", (event) => {
  if (
    settingsDrawer?.hidden === false &&
    !settingsDrawer.contains(event.target) &&
    !settingsButton?.contains(event.target)
  ) {
    closeSettingsDrawer();
  }
});

floatingStatsButton?.addEventListener("click", () => {
  pauseSpeech();
  openStatsDeepDiveDialog();
});

micButton?.addEventListener("click", () => {
  if (!recognition || appState.inputMode !== "text") return;
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
  stopAutoConversation();
  autoConversationTurns = 0;
  closeStatsDeepDiveDialog();
  clearCurrentInputState();
  localStorage.removeItem(sessionKey);
  sessionId = null;
  statsDialogStarted = false;
  clearElement(statsDialogLog);
  clearElement(chatLog);
  clearElement(optionTray);
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

knowledgeButton?.addEventListener("click", () => {
  closeSettingsDrawer();
  openKnowledgeDialog();
});
promptsButton?.addEventListener("click", () => {
  closeSettingsDrawer();
  openPromptLibraryDialog();
});
closeKnowledgeButton?.addEventListener("click", closeKnowledgeDialog);
closePromptLibraryButton?.addEventListener("click", closePromptLibraryDialog);

knowledgeDropzone?.addEventListener("dragover", (event) => {
  event.preventDefault();
  knowledgeDropzone.classList.add("is-dragging");
});

knowledgeDropzone?.addEventListener("dragleave", () => {
  knowledgeDropzone.classList.remove("is-dragging");
});

knowledgeDropzone?.addEventListener("drop", (event) => {
  event.preventDefault();
  knowledgeDropzone.classList.remove("is-dragging");
  assignKnowledgeFiles(event.dataTransfer?.files);
  if (knowledgeFileInput?.files?.length) {
    knowledgeUploadForm?.requestSubmit();
  }
});

knowledgeUploadForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = Array.from(knowledgeFileInput.files || []);
  if (!files.length) return;
  resetKnowledgeProgress();
  const rows = files.map((file) => addKnowledgeProgressRow(file.name));
  let totalChunks = 0;
  let completed = 0;
  const failures = [];
  for (const [index, file] of files.entries()) {
    const row = rows[index];
    updateKnowledgeProgressRow(row, "Uploading...", 5);
    const data = await uploadKnowledgeFile(file, row);
    const failed = data.error || Boolean(data.failures?.length);
    if (failed) {
      const detail = data.detail || data.failures?.[0]?.detail || "Could not ingest file.";
      failures.push({ source: file.name, detail });
      updateKnowledgeProgressRow(row, detail, 100, "failed");
      continue;
    }
    totalChunks += Number(data.chunks || 0);
    completed += 1;
    updateKnowledgeProgressRow(row, `Done · ${data.chunks || 0} chunks`, 100, "done");
  }
  showKnowledgeMessage(
    completed
      ? `Ingested ${completed} file(s) into ${totalChunks} chunks${failures.length ? `; ${failures.length} failed.` : "."}`
      : "No files were ingested.",
    failures.length > 0 || completed === 0
  );
  knowledgeFileInput.value = "";
  await loadKnowledgeDocuments();
});

knowledgeUrlForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const urls = knowledgeUrlInput.value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
  if (!urls.length) return;
  resetKnowledgeProgress();
  const rows = urls.map((url) => addKnowledgeProgressRow(url));
  let totalChunks = 0;
  let completed = 0;
  const failures = [];
  for (const [index, url] of urls.entries()) {
    const row = rows[index];
    updateKnowledgeProgressRow(row, "Fetching and ingesting...", 35);
    const response = await csrfFetch("/api/knowledge/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls: [url] }),
    });
    const data = await response.json();
    const failed = data.error || Boolean(data.failures?.length);
    if (failed) {
      const detail = data.detail || data.failures?.[0]?.detail || "Could not ingest URL.";
      failures.push({ source: url, detail });
      updateKnowledgeProgressRow(row, detail, 100, "failed");
      continue;
    }
    totalChunks += Number(data.chunks || 0);
    completed += 1;
    updateKnowledgeProgressRow(row, `Done · ${data.chunks || 0} chunks`, 100, "done");
  }
  showKnowledgeMessage(
    completed
      ? `Ingested ${completed} URL(s) into ${totalChunks} chunks${failures.length ? `; ${failures.length} failed.` : "."}`
      : "No URLs were ingested.",
    failures.length > 0 || completed === 0
  );
  knowledgeUrlInput.value = "";
  await loadKnowledgeDocuments();
});

knowledgeSearchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = knowledgeSearchInput.value.trim();
  if (!query) return;
  const response = await csrfFetch("/api/knowledge/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  const data = await response.json();
  renderKnowledgeResults(data.results || []);
});

sectorPromptReindexButton?.addEventListener("click", reindexSectorPrompts);

sectorPromptSearchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await searchSectorPrompts();
});

refreshPromptsButton?.addEventListener("click", loadPrompts);
newPromptButton?.addEventListener("click", startNewPrompt);
promptSourceSelect?.addEventListener("change", updatePromptSourceSetting);
promptSearchInput?.addEventListener("input", renderPromptList);
promptEditorForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (creatingPrompt) {
    await createPrompt();
  } else {
    await saveSelectedPrompt();
  }
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

closeStatsDialogButton?.addEventListener("click", closeStatsDeepDiveDialog);

statsDeepDiveDialog?.addEventListener("close", () => {
  pauseSpeech();
  messageInput.focus();
});

statsDialogForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = statsDialogInput.value.trim();
  if (!value) return;
  statsDialogInput.value = "";
  sendStatsDialogMessage(value, true);
});

closeTargetPopulationButton?.addEventListener("click", closeTargetPopulationDialog);
cancelTargetPopulationButton?.addEventListener("click", closeTargetPopulationDialog);
closeMethodologyButton?.addEventListener("click", closeMethodologyDialog);
methodologyDialog?.addEventListener("click", (event) => {
  if (event.target === methodologyDialog) closeMethodologyDialog();
});
closeSurveyResultsButton?.addEventListener("click", closeSurveyResultsDialog);
surveyResultsDialog?.addEventListener("click", (event) => {
  if (event.target === surveyResultsDialog) closeSurveyResultsDialog();
});
surveyResultsZoomIn?.addEventListener("click", () => setSurveyResultsZoom(surveyResultsZoom + 0.25));
surveyResultsZoomOut?.addEventListener("click", () => setSurveyResultsZoom(surveyResultsZoom - 0.25));
surveyResultsZoomReset?.addEventListener("click", () => setSurveyResultsZoom(1));
closePlatformUsersButton?.addEventListener("click", closePlatformUsersDialog);
platformUsersDialog?.addEventListener("click", (event) => {
  if (event.target === platformUsersDialog) closePlatformUsersDialog();
});
platformUsersZoomIn?.addEventListener("click", () => setPlatformUsersZoom(platformUsersZoom + 0.25));
platformUsersZoomOut?.addEventListener("click", () => setPlatformUsersZoom(platformUsersZoom - 0.25));
platformUsersZoomReset?.addEventListener("click", () => setPlatformUsersZoom(1));
targetAllGeneralPopulationButton?.addEventListener("click", () => {
  targetPopulationDialogBody
    ?.querySelectorAll("[data-quick-target-option='true']")
    .forEach((input) => {
      input.checked = true;
    });
});
resetTargetPopulationButton?.addEventListener("click", () => {
  targetPopulationDialogBody
    ?.querySelectorAll("[data-quick-target-option='true']")
    .forEach((input) => {
      input.checked = false;
    });
});

targetPopulationForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const payload = targetPopulationBatchPayload();
  if (!payload.length) return;
  closeTargetPopulationDialog();
  disableOldOptions();
  addMessage("user", "Quick Select Affected Population Group");
  sendMessage(`TARGET_POPULATION_BATCH: ${JSON.stringify(payload)}`, false);
});

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
    const response = await csrfFetch("/api/profile/password", {
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
  configureTypingEffectControl();
  configureValidationModeControl();
  configureCrowdSourcingControl();
  configureMic();
  configureWorkspaceResizer();
  clearCurrentInputState();
  loadSessions();
  loadSyncStatus();
  if (sessionId) {
    restoreSession(sessionId);
  } else {
    sendMessage("", false);
  }
  window.setTimeout(startUiTourIfNeeded, 350);
});

const uiTourSteps = [
  { target: ".stage-visual-panel", title: "Follow your analysis roadmap", text: "This left panel shows your selected context, visual progress, and the journey from country selection through evaluation." },
  { target: ".chat-card", title: "Work with the guided chat", text: "Use this workspace to answer questions, review evidence, and move through each stage of the policy analysis." },
  { target: "#resetButton", title: "Start a new session", text: "Begin a fresh analysis at any time. Your existing sessions remain available in Manage Sessions." },
  { target: "#sessionsButton", title: "Manage your sessions", text: "Save, rename, and return to previous analysis sessions whenever you need to continue your work." },
  { target: "#settingsButton", title: "Personalize the workspace", text: "Settings contains voice, typing, validation, data, and other workspace preferences." },
  { target: "#profileButton", title: "Manage your profile", text: "Open your profile to review account details or change your password." },
];
let uiTourIndex = 0;
let uiTourTarget = null;

function uiTourStorageKey() {
  return `dr_transition_ui_tour_v2_completed_${document.body.dataset.tourUserId || "unknown"}`;
}

function closeUiTour(markCompleted = true) {
  uiTourTarget?.classList.remove("ui-tour-target");
  uiTourTarget = null;
  if (markCompleted) localStorage.setItem(uiTourStorageKey(), "true");
  if (uiTour) {
    uiTour.hidden = true;
    uiTour.setAttribute("aria-hidden", "true");
  }
}

function renderUiTourStep() {
  const step = uiTourSteps[uiTourIndex];
  uiTourTarget?.classList.remove("ui-tour-target");
  uiTourTarget = document.querySelector(step.target);
  if (!uiTourTarget) return;
  uiTourTarget.classList.add("ui-tour-target");
  uiTourStep.textContent = `Step ${uiTourIndex + 1} of ${uiTourSteps.length}`;
  uiTourTitle.textContent = step.title;
  uiTourText.textContent = step.text;
  uiTourBack.hidden = uiTourIndex === 0;
  uiTourNext.textContent = uiTourIndex === uiTourSteps.length - 1 ? "Finish" : "Next";
  const targetRect = uiTourTarget.getBoundingClientRect();
  const cardRect = uiTourCard.getBoundingClientRect();
  const left = Math.min(Math.max(16, targetRect.left), window.innerWidth - cardRect.width - 16);
  const above = targetRect.bottom + cardRect.height + 20 > window.innerHeight;
  const top = above ? targetRect.top - cardRect.height - 16 : targetRect.bottom + 16;
  uiTourCard.style.left = `${left}px`;
  uiTourCard.style.top = `${Math.max(16, top)}px`;
}

function startUiTourIfNeeded() {
  if (!uiTour || localStorage.getItem(uiTourStorageKey()) === "true") return;
  uiTour.hidden = false;
  uiTour.setAttribute("aria-hidden", "false");
  uiTourIndex = 0;
  renderUiTourStep();
  uiTourNext.focus();
}

uiTourNext?.addEventListener("click", () => {
  if (uiTourIndex === uiTourSteps.length - 1) return closeUiTour();
  uiTourIndex += 1;
  renderUiTourStep();
});
uiTourBack?.addEventListener("click", () => {
  if (uiTourIndex > 0) {
    uiTourIndex -= 1;
    renderUiTourStep();
  }
});
uiTourSkip?.addEventListener("click", () => closeUiTour());
window.addEventListener("resize", () => {
  if (uiTour && !uiTour.hidden) renderUiTourStep();
});



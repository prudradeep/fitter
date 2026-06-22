const sessionKey = "dr_transition_session_id";
const inputStateKeyPrefix = "dr_transition_input_state_";
const voiceEnabledKey = "dr_transition_voice_enabled";
const voicePreferenceKey = "dr_transition_voice_preference";
const typingEffectKey = "dr_transition_typing_effect_enabled";
const autoConversationKey = "dr_transition_auto_conversation_enabled";
const teacherAvatarPath = "/static/img/teacher.png";
const collapsibleMessageWordLimit = 100;

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
const voicePreferenceSelect = document.querySelector("#voicePreferenceSelect");
const voiceAnalyzerElement = document.querySelector("#voiceAnalyzer");
const sessionsButton = document.querySelector("#sessionsButton");
const sessionsPanel = document.querySelector("#sessionsPanel");
const closeSessionsButton = document.querySelector("#closeSessionsButton");
const sessionsList = document.querySelector("#sessionsList");
const knowledgeButton = document.querySelector("#knowledgeButton");
const knowledgeDialog = document.querySelector("#knowledgeDialog");
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
const knowledgeResults = document.querySelector("#knowledgeResults");
const sectorPromptReindexButton = document.querySelector("#sectorPromptReindexButton");
const sectorPromptSearchForm = document.querySelector("#sectorPromptSearchForm");
const sectorPromptSectorInput = document.querySelector("#sectorPromptSectorInput");
const sectorPromptSearchInput = document.querySelector("#sectorPromptSearchInput");
const sectorPromptMessage = document.querySelector("#sectorPromptMessage");
const sectorPromptResults = document.querySelector("#sectorPromptResults");
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

let sessionId = localStorage.getItem(sessionKey);
let loading = false;
let statsDialogLoading = false;
let statsDialogStarted = false;
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
let currentStep = "";
let currentSession = {};
let currentOptions = [];
let currentOtherOptions = [];
let currentTargetPopulationQuestion = null;
let targetPopulationQuestions = [];
let targetPopulationAnswers = [];
let autoConversationTimer = null;
let autoConversationTurns = 0;
const autoConversationTurnLimit = 80;
let renderedVisualKey = "";
let renderedStageCardsKey = "";
let stageVisualRenderId = 0;
const mapTopologyCache = new Map();

const defaultPlaceholder = "Type a country, region, or sector...";
const panelWidthKey = "dr_transition_visual_panel_width";
const defaultVisualPanelPercent = 43;
const visualPanelMinPercent = 30;
const visualPanelMaxPercent = 62;
const coverageCountries = stageCoverageRows
  .filter((row) => row.code)
  .map((row) => ({
    code: row.code,
    name: row.coverage,
    mapPath: row.map_path,
    sectors: row.sectors || "Not configured",
    hazards: Number(row.hazards) || 0,
    analyses: Number(row.analyses) || 0,
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

function stageKeyForStep(step = "") {
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
    ].includes(step)
  ) {
    return "hazards";
  }
  if (step.startsWith("mitigation") || step === "mitigation") return "mitigation";
  if (step.startsWith("evaluation") || step === "complete") return "evaluation";
  return "country";
}

function updateStageVisual(step = "", session = {}, options = currentOptions) {
  currentStep = step;
  currentOptions = options || [];
  renderSelectedHazardContext(session);
  const key = stageKeyForStep(step);
  const visual = stageVisuals[key] || stageVisuals.country;
  if (stageVisualTitle) stageVisualTitle.textContent = visual.title;
  if (stageVisualText) {
    stageVisualText.textContent =
      key === "sector" ? sectorStageText(session, currentOptions) : visual.text;
  }
  if (stageProgressFill) {
    const percent = (visual.index / Math.max(1, stageSteps.length - 1)) * 100;
    stageProgressFill.style.width = `${percent}%`;
  }
  stageSteps.forEach((item, index) => {
    item.classList.toggle("active", index <= visual.index);
    item.classList.toggle("current", item.dataset.stageKey === key);
  });
  document.body.dataset.analysisStage = key;
  renderDynamicStageVisual(key, currentSession, currentOptions);
  updateFloatingStatsButton();
}

function sectorStageText(session = {}, options = currentOptions) {
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
  const hasSector = Boolean(currentSession?.sector);
  const canOpen = hasSector && !["country", "national_scope", "region", "sector"].includes(currentStep);
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
  const response = await fetch(`https://code.highcharts.com/mapdata/${path}`);
  if (!response.ok) throw new Error(`Map data failed with status ${response.status}`);
  const topology = await response.json();
  mapTopologyCache.set(path, topology);
  return topology;
}

async function renderDynamicStageVisual(key, session = {}, options = currentOptions) {
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
  if (!stageMap || !window.Highcharts || !europeMapPath) {
    showStageMap();
    return;
  }
  const visualKey = "country-map";
  if (renderedVisualKey === visualKey) {
    showStageMap();
    return;
  }
  renderedVisualKey = visualKey;
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
  } catch (error) {
    console.error("Country stage map failed", error);
    showStageMap();
  }
}

async function renderRegionMap(country, region, { keepCards = false } = {}) {
  const countryMapPath = countryMapData.get(country);
  if (!stageMap || !window.Highcharts || !countryMapPath) {
    showStageMap();
    return;
  }
  const visualKey = `region-map-${country}-${region || ""}`;
  if (renderedVisualKey === visualKey) {
    showStageMap({ keepIcons: keepCards });
    return;
  }
  renderedVisualKey = visualKey;
  const renderId = ++stageVisualRenderId;
  showStageMap({ keepIcons: keepCards });

  try {
    const topology = await fetchMapTopology(countryMapPath);
    if (renderId !== stageVisualRenderId) return;
    const selectedRegion = normalizeRegionForMapMatch(region || "");
    const data = topology.features.map((feature) => {
      const name = feature.properties.name || feature.properties.NAME_1 || "";
      const selected = selectedRegion && normalizeRegionForMapMatch(name) === selectedRegion;
      return {
        "hc-key": feature.properties["hc-key"],
        value: selected ? 1 : 0,
        color: selected ? "#6d22c7" : "#c7ccd3",
        name,
      };
    });

    Highcharts.mapChart(stageMap, {
      chart: mapChartOptions(topology),
      title: { text: null },
      credits: { enabled: false },
      legend: { enabled: false },
      mapNavigation: mapNavigationOptions(),
      tooltip: { pointFormat: "{point.name}" },
      plotOptions: {
        map: {
          borderColor: "#7a8493",
          borderWidth: 0.55,
          states: { hover: { color: "#7428d2" } },
        },
      },
      series: [{ name: "Region", data, joinBy: "hc-key", nullColor: "#c7ccd3" }],
    });
    window.requestAnimationFrame(resizeStageChart);
  } catch (error) {
    console.error("Region stage map failed", error);
    showStageMap();
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
  const percentage = Number(value);
  return Number.isFinite(percentage) ? `${percentage.toFixed(1)}%` : "—";
}

function populationTrend(regionalValue, nationalValue) {
  const regional = Number(regionalValue);
  const national = Number(nationalValue);
  if (!Number.isFinite(regional) || !Number.isFinite(national)) return "";
  const difference = regional - national;
  if (Math.abs(difference) < 0.05) {
    return '<span class="population-trend is-equal" title="Equal to national" aria-label="equal to national">•</span>';
  }
  const higher = difference > 0;
  return `<span class="population-trend ${higher ? "is-up" : "is-down"}" title="${higher ? "Higher" : "Lower"} than national" aria-label="${higher ? "higher" : "lower"} than national">${higher ? "↑" : "↓"}</span>`;
}

function renderHazardPopulationTable(session = {}) {
  const hazards = Array.isArray(session.top_hazards) ? session.top_hazards.slice(0, 3) : [];
  const counts = [
    ["Hazards", session.hazard_count],
    ["Unique profiles", session.affected_profile_count],
    ["Mitigation measures", session.mitigation_measure_count],
  ];
  const countCards = counts
    .map(
      ([label, value]) => `
        <article>
          <strong>${Number(value) || 0}</strong>
          <span>${label}</span>
        </article>
      `,
    )
    .join("");
  const rows = hazards
    .map(
      (hazard, index) => `
        <tr>
          <td><span>${index + 1}</span>${escapeHtml(hazard.hazard || "Hazard")}</td>
          <td>${populationPercentage(hazard.regional_population_pct)}${populationTrend(hazard.regional_population_pct, hazard.national_population_pct)}</td>
          <td>${populationPercentage(hazard.national_population_pct)}</td>
        </tr>
      `,
    )
    .join("");
  stageIconGrid.innerHTML = `
    ${
      session.selected_hazard
        ? ""
        : `<div class="stage-hazard-summary" aria-label="Sector analysis totals">${countCards}</div>`
    }
    <section class="stage-hazard-table" aria-label="Top three hazard population comparison">
      <div class="stage-hazard-table-heading">
        <div>
          <span>Population comparison</span>
          <h3>Top 3 hazards</h3>
        </div>
        <small>Average across mapped affected profiles</small>
      </div>
      <div class="stage-hazard-table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Hazard</th>
              <th scope="col">Regional</th>
              <th scope="col">National</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStageIcons(key, session = {}, options = currentOptions, { keepMap = false } = {}) {
  if (!stageIconGrid) return;
  const topHazardKey = (session.top_hazards || [])
    .map((hazard) => `${hazard.hazard}:${hazard.regional_population_pct}:${hazard.national_population_pct}`)
    .join("|");
  const visualKey = `icons-${key}-${session.country || ""}-${session.selected_hazard || ""}-${session.hazard_count || 0}-${session.affected_profile_count || 0}-${session.mitigation_measure_count || 0}-${topHazardKey}-${options.map((option) => option.label).join("|")}`;
  if (renderedStageCardsKey === visualKey) return;
  renderedStageCardsKey = visualKey;
  if (!keepMap) stageVisualRenderId += 1;
  showStageIcons({ keepMap });

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

  stageIconGrid.innerHTML = items
    .map(
      (item, index) => `
        <article class="stage-icon-card${item.stat ? " stage-stat-card" : ""}" style="--stage-card-index: ${index}">
          ${
            item.stat
              ? ""
              : `<span class="stage-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="${item.icon}"></path>
                  </svg>
                </span>`
          }
          <p${item.stat ? ' class="stage-stat-value"' : ""}>${item.text}</p>
          <h3>${item.title}</h3>
        </article>
      `,
    )
    .join("");
}

function plainTextFromHtml(html) {
  const element = document.createElement("div");
  element.innerHTML = html;
  return element.textContent.replace(/\s+/g, " ").trim();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
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

function typingEffectEnabled() {
  return typingEffectToggle ? typingEffectToggle.checked : true;
}

function configureTypingEffectControl() {
  if (!typingEffectToggle) return;
  const saved = localStorage.getItem(typingEffectKey);
  typingEffectToggle.checked = saved === null ? true : saved === "true";
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
  if (autoConversationToggle) {
    autoConversationToggle.checked = localStorage.getItem(autoConversationKey) === "true";
  }
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
    if (step === "mitigation_clarity") {
      return "Answer all clarification questions...";
    }
    if (step === "mitigation_review") {
      return "Ask about this mitigation, or move to next step...";
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
    mitigation: "Ask a mitigation question or continue the plan...",
    mitigation_clarity: "Answer all clarification questions...",
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
    secondaryReasonInput.closest("label").hidden = true;
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
    primaryInputLabel.textContent = "Reason (optional)";
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

  root.querySelectorAll(".js-evaluation-radar-chart:not([data-chart-ready])").forEach((element) => {
    const labels = parseChartData(element, "labels");
    const categories = parseChartData(element, "categories");
    const values = parseChartData(element, "values").map((value) => Math.max(1, Math.min(10, Number(value) || 1)));
    if (!labels.length || labels.length !== values.length) return;
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
      legend: { enabled: false },
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
      tooltip: { pointFormatter() { return `<span style="color:${this.color}">●</span> ${categories[this.index] || "Evaluation"}<br><b>${this.y} / 10</b>`; } },
      plotOptions: { series: { pointPlacement: "on", color: "#6d28d9", fillColor: "rgba(109, 40, 217, 0.18)", fillOpacity: 0.2, lineWidth: 3, marker: { radius: 4 } } },
      series: [{ name: "Score", data: values }],
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
    content.innerHTML = text;
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
    content.innerHTML = html;
    initializeHighcharts(content);
    bubble.appendChild(timestamp);
    applyCollapsibleBubble(bubble);
    syncCollapsibleMessages(targetLog);
    scrollToBottom(targetLog);
    return;
  }

  const template = document.createElement("template");
  template.innerHTML = html;

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
  initializeHighcharts(content);
  bubble.appendChild(timestamp);
  applyCollapsibleBubble(bubble);
  syncCollapsibleMessages(targetLog);
  scrollToBottom(targetLog);
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

  const dimensions = details.dimensions && typeof details.dimensions === "object"
    ? details.dimensions
    : {};
  if (Object.keys(dimensions).length) {
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
  if (["clear", "supported"].includes(normalized)) return "pass";
  if (["contradicted"].includes(normalized)) return "fail";
  return "pending";
}

function formatValidationNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : String(value);
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
  micButton.disabled = value || !micSupported || inputMode !== "text";
  optionTray.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.used === "true";
  });
}

function setInputMode(mode = "text", step = "", options = []) {
  inputMode = mode;
  currentOptions = options || [];
  syncTargetPopulationQuestion(step, currentOptions);
  updateStageVisual(step, currentSession, currentOptions);
  const reasonEvidenceMode = mode === "reason_evidence" || mode === "mitigation_measure";
  const evaluationMode = mode === "evaluation_question";
  const textareaMode = mode === "textarea";
  micButton.disabled = !micSupported || reasonEvidenceMode || evaluationMode || textareaMode;
  const placeholder = placeholderForStep(step, options);
  messageInput.placeholder = placeholder;
  textareaInput.placeholder = placeholder;
  setReasonEvidencePlaceholders(step, mode);
  reasonEvidenceFields.classList.toggle("mitigation-mode", mode === "mitigation_measure");
  messageInputRow.hidden = reasonEvidenceMode || evaluationMode;
  messageInput.hidden = textareaMode;
  textareaInput.hidden = !textareaMode;
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
  currentSession = session || {};
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
  updateStageVisual(currentStep, currentSession, currentOptions);
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
  const showMitigationReviewPanel =
    currentStep === "mitigation_review"
    || currentStep === "evaluation_question"
    || (currentStep === "mitigation" && hasMitigationReview);
  const hazard = String(session.selected_hazard || "").trim();
  const mitigationMeasure = String(session.mitigation_measure || "").trim();
  const profiles = Array.isArray(session.affected_profiles)
    ? session.affected_profiles.map((profile) => String(profile || "").trim()).filter(Boolean)
    : [];

  selectedHazardContext.hidden = showMitigationReviewPanel ? !mitigationMeasure : !hazard;
  if (selectedContextLabel) {
    selectedContextLabel.textContent = showMitigationReviewPanel ? "Mitigation Measure" : "Selected hazard";
  }
  selectedHazardName.textContent = showMitigationReviewPanel ? mitigationMeasure : hazard;
  if (affectedProfileContext) affectedProfileContext.hidden = showMitigationReviewPanel;
  if (mitigationReviewContext) mitigationReviewContext.hidden = !showMitigationReviewPanel;
  if (showMitigationReviewPanel) {
    renderMitigationReviewContext(session);
    return;
  }
  affectedProfileList.innerHTML = "";
  profiles.forEach((profile) => {
    const item = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "affected-profile-item-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = `
      <svg viewBox="0 0 24 24">
        <path d="M15 8a3 3 0 10-6 0 3 3 0 006 0zM5 20a7 7 0 0114 0"></path>
      </svg>
    `;
    const label = document.createElement("span");
    label.textContent = profile;
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
    benefitedProfileList.innerHTML = "";
    profiles.forEach((profile) => {
      const item = document.createElement("li");
      const icon = document.createElement("span");
      icon.className = "affected-profile-item-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = `
        <svg viewBox="0 0 24 24">
          <path d="M20 6L9 17l-5-5"></path>
        </svg>
      `;
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
    mitigationSupportedDimensions.innerHTML = "";
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
    : validationLabel(currentSession?.mitigation_review?.grounding_status || "Supported");
});

function syncTargetPopulationQuestion(step, options = []) {
  if (step !== "target_population_question" && step !== "add_dgs") {
    currentTargetPopulationQuestion = null;
    return;
  }
  const optionLabels = new Set(
    (options || [])
      .map((option) => option.label)
      .filter((label) => !["Skip", "Skip all", "Quick Select Target Population"].includes(label)),
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
}

function renderOptions(options, otherOptions = []) {
  currentOptions = options || [];
  currentOtherOptions = otherOptions || [];
  optionTray.innerHTML = "";
  highlightedOptionLabel = "";
  if (inputMode === "target_population_multi") {
    renderTargetPopulationOptions(currentOptions);
    renderOtherOptionsMenu();
    return;
  }
  options.forEach((option) => optionTray.appendChild(createOptionButton(option.label)));
  renderOtherOptionsMenu();
  updateOptionHighlight();
}

function renderTargetPopulationOptions(options = []) {
  const previouslySelected = selectedTargetPopulationLabels(currentTargetPopulationQuestion?.id);
  const normalOptions = options.filter(
    (option) =>
      !["Skip", "Skip all", "Quick Select Target Population"].includes(option.label),
  );
  const actions = options.filter((option) =>
    ["Skip", "Skip all", "Quick Select Target Population"].includes(option.label),
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
      label.appendChild(checkbox);
      label.appendChild(span);
      group.appendChild(label);
    });
    optionTray.appendChild(group);

    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "option-pill";
    submit.textContent = "Submit selected";
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
  button.addEventListener("click", () => {
    pauseSpeech();
    collapseExpandedMessages();
    if (label === "Dive deeper into statistical findings") {
      openStatsDeepDiveDialog();
      return;
    }
    if (label === "Quick Select Target Population") {
      openTargetPopulationDialog();
      return;
    }
    disableOldOptions();
    sendMessage(label, true);
  });
  return button;
}

function renderOtherOptionsMenu() {
  const navOptions = currentOtherOptions || [];
  if (!navOptions.length) return;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "option-pill other-options-toggle";
  toggle.textContent = "Other Options";
  toggle.dataset.otherToggle = "true";

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

async function openStatsDeepDiveDialog() {
  showStatsDeepDiveDialog();
  if (statsDialogStarted) return;
  statsDialogStarted = true;
  if (statsDialogLog) statsDialogLog.innerHTML = "";
  await sendStatsDialogMessage(
    "Dive deeper into the statistical findings for the listed hazards. Summarise the most important results and affected groups.",
    false,
  );
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
  targetPopulationDialogBody.innerHTML = "";
  questions.forEach((question) => {
    const previouslySelected = selectedTargetPopulationLabels(question.id);
    const section = document.createElement("fieldset");
    section.className = "target-dialog-question";
    section.dataset.questionId = question.id;
    const legend = document.createElement("legend");
    legend.textContent = question.question || "Target population question";
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
    const response = await fetch("/api/stats-deep-dive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: cleanMessage,
        session_id: sessionId,
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
    speakServerMessage(data.bot_message);
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
    inputMode,
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
    if (state.inputMode && state.inputMode !== inputMode) return;
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

function resetKnowledgeProgress() {
  if (!knowledgeProgressSection || !knowledgeProgressList) return;
  knowledgeProgressList.innerHTML = "";
  knowledgeProgressSection.hidden = true;
}

function addKnowledgeProgressRow(label, status = "Queued") {
  if (!knowledgeProgressSection || !knowledgeProgressList) return null;
  knowledgeProgressSection.hidden = false;
  const row = document.createElement("article");
  row.className = "knowledge-progress-row";
  row.innerHTML = `
    <strong title="${escapeHtml(label)}">${escapeHtml(label)}</strong>
    <small>${escapeHtml(status)}</small>
    <div class="knowledge-progress-track" aria-hidden="true"><span></span></div>
  `;
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
    const request = new XMLHttpRequest();
    request.open("POST", "/api/knowledge/upload");
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
  knowledgeDocuments.innerHTML = `<p class="sessions-empty">Loading documents...</p>`;
  try {
    const response = await fetch("/api/knowledge");
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    const data = await response.json();
    renderKnowledgeDocuments(data.documents || []);
  } catch (error) {
    knowledgeDocuments.innerHTML = `<p class="sessions-empty">Could not load documents.</p>`;
    console.error("Knowledge documents failed", error);
  }
}

function renderKnowledgeDocuments(documents) {
  if (!knowledgeDocuments) return;
  knowledgeDocuments.innerHTML = "";
  if (!documents.length) {
    knowledgeDocuments.innerHTML = `<p class="sessions-empty">No knowledge documents yet.</p>`;
    return;
  }
  documents.forEach((documentItem) => {
    const row = document.createElement("article");
    row.className = "knowledge-item knowledge-document-row";
    row.innerHTML = `
      <div class="knowledge-document-main">
        <strong title="${escapeHtml(documentItem.title)}">${escapeHtml(documentItem.title)}</strong>
        <small>${escapeHtml(documentItem.source_type || "document")}</small>
      </div>
    `;
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "knowledge-delete-button";
    deleteButton.textContent = "×";
    deleteButton.setAttribute("aria-label", `Delete ${documentItem.title}`);
    deleteButton.title = "Delete document";
    deleteButton.addEventListener("click", () => deleteKnowledgeDocument(documentItem.id));
    row.appendChild(deleteButton);
    knowledgeDocuments.appendChild(row);
  });
}

function renderKnowledgeResults(results) {
  if (!knowledgeResults) return;
  knowledgeResults.innerHTML = "";
  if (!results.length) {
    knowledgeResults.innerHTML = `<p class="sessions-empty">No matching chunks found.</p>`;
    return;
  }
  results.forEach((result) => {
    const row = document.createElement("article");
    row.className = "knowledge-item";
    const sourceParts = [result.source_type || "document"];
    if (result.page_number) sourceParts.push(`page ${result.page_number}`);
    row.innerHTML = `
      <strong>${escapeHtml(result.title)}</strong>
      <small>${escapeHtml(sourceParts.join(" · "))} · Score: ${escapeHtml(result.score)}</small>
      <p>${escapeHtml(result.content)}</p>
    `;
    knowledgeResults.appendChild(row);
  });
}

function renderSectorPromptResults(results) {
  if (!sectorPromptResults) return;
  sectorPromptResults.innerHTML = "";
  if (!results.length) {
    sectorPromptResults.innerHTML = `<p class="sessions-empty">No matching sector prompt chunks found.</p>`;
    return;
  }
  results.forEach((result) => {
    const row = document.createElement("article");
    row.className = "knowledge-item";
    const sourceParts = [result.source_type || "sector_prompt"];
    if (result.source_uri) sourceParts.push(result.source_uri.replace("sector-prompt://", ""));
    const scoreLabel =
      result.score === null || typeof result.score === "undefined"
        ? "lexical/DB"
        : escapeHtml(result.score);
    row.innerHTML = `
      <strong>${escapeHtml(result.title || "Sector prompt")}</strong>
      <small>${escapeHtml(sourceParts.join(" · "))} · Score: ${scoreLabel}</small>
      <p>${escapeHtml(result.content || "")}</p>
    `;
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
    const response = await fetch("/api/sector-prompts/reindex", { method: "POST" });
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
    sectorPromptResults.innerHTML = `<p class="sessions-empty">Searching sector prompts...</p>`;
  }
  try {
    const response = await fetch("/api/sector-prompts/search", {
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

async function deleteKnowledgeDocument(documentId) {
  const response = await fetch(`/api/knowledge/${encodeURIComponent(documentId)}`, {
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
    chatLog.innerHTML = "";
    optionTray.innerHTML = "";
    statsDialogStarted = false;
    if (statsDialogLog) statsDialogLog.innerHTML = "";
    (data.messages || []).forEach((message) => {
      addMessage(message.role, message.content, Boolean(message.is_error));
    });
    updateSessionCard(data.session || {});
    setInputMode(data.input_mode || "text", data.step, data.options || []);
    applyInputValues(data.input_values);
    renderOptions(data.options || [], data.other_options || []);
    applySavedInputState();
    sessionsPanel.hidden = true;
  } catch (error) {
    console.error("Session restore failed", error);
    if (nextSessionId === sessionId) {
      localStorage.removeItem(sessionKey);
      sessionId = null;
      chatLog.innerHTML = "";
      optionTray.innerHTML = "";
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
    const response = await fetch("/api/auto-user-message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "", session_id: sessionId }),
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
    "textarea",
    "evaluation_question",
  ]);
  if (!fieldModes.has(inputMode)) return message;
  const optionLabels = [...currentOptions, ...currentOtherOptions].map((option) =>
    typeof option === "string" ? option : option.label,
  );
  const isOption = optionLabels.some((label) => normalizeForMatch(label) === normalizeForMatch(message));
  if (!isOption) {
    if (
      inputMode === "reason_evidence" &&
      !/^reason\s*:/i.test(message) &&
      !/^evidence\s*:/i.test(message)
    ) {
      return `Reason: ${message}`;
    }
    if (
      inputMode === "mitigation_measure" &&
      !/^mitigation(?:\s+measure)?\s*:/i.test(message)
    ) {
      return `Mitigation measure: ${message}`;
    }
    if (inputMode === "evaluation_question" && !/^score\s*:/i.test(message)) {
      return `Score: 7\nReason: ${message}`;
    }
    return message;
  }
  if (inputMode === "mitigation_measure") {
    return "Mitigation measure: Provide targeted financial support and advisory services for affected groups.";
  }
  if (inputMode === "reason_evidence") {
    return "Reason: This reduces the hazard by lowering costs, improving access, and supporting affected groups through the transition.";
  }
  if (inputMode === "textarea") {
    return "The cost coverage applies to the affected target groups by paying or reimbursing upfront adaptation costs directly for them, with guidance and implementation support so they can use the measure in practice.";
  }
  if (inputMode === "evaluation_question") {
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
    currentStep = data.step;
    if (data.step === "stats_deep_dive_dialog") {
      updateSessionCard(data.session);
      setInputMode(data.input_mode || "text", currentStep, data.options || []);
      renderOptions(data.options || [], data.other_options || []);
      await openStatsDeepDiveDialog();
      loadSessions();
      return;
    }
    const botRow = addMessage("bot", "", data.error);
    speakServerMessage(data.bot_message);
    await typeServerMessage(botRow, data.bot_message);
    renderValidationDetails(botRow, data.validation_details);
    updateSessionCard(data.session);
    setInputMode(data.input_mode || "text", data.step, data.options || []);
    applyInputValues(data.input_values);
    renderOptions(data.options || [], data.other_options || []);
    loadSessions();
    shouldScheduleAuto = true;
  } catch (error) {
    typing.remove();
    console.error("Chat request failed", error);
  } finally {
    setLoading(false);
    if (inputMode === "reason_evidence" || inputMode === "mitigation_measure") {
      reasonInput.focus();
    } else if (inputMode === "evaluation_question") {
      scoreInput.focus();
    } else if (inputMode === "textarea") {
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

  if (inputMode === "reason_evidence" || inputMode === "mitigation_measure") {
    const primaryValue = reasonInput.value.trim();
    const evidenceUrl = evidenceInput.value.trim();
    const evidenceFile = evidenceFileInput.files[0];

    if (inputMode === "mitigation_measure") {
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

    const reasonOptional = currentStep === "socio_demographic_review";
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
    sendMessage(primaryValue ? `Reason: ${primaryValue}` : "", false, { evidenceUrl, evidenceFile });
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
    collapseExpandedMessages();
    addMessage("user", value);
    sendMessage(lines.filter((line) => !line.startsWith("Evidence ")).join("\n"), false, {
      evidenceUrl,
      evidenceFile,
    });
    return;
  }

  const freeTextInput = inputMode === "textarea" ? textareaInput : messageInput;
  const value = freeTextInput.value.trim();
  if (!value) {
    flashRequiredField(freeTextInput);
    return;
  }
  freeTextInput.value = "";
  highlightedOptionLabel = "";
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
});

settingsButton?.addEventListener("click", () => {
  if (settingsDrawer?.hidden) {
    openSettingsDrawer();
  } else {
    closeSettingsDrawer();
  }
});

closeSettingsButton?.addEventListener("click", closeSettingsDrawer);

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
  stopAutoConversation();
  autoConversationTurns = 0;
  closeStatsDeepDiveDialog();
  clearCurrentInputState();
  localStorage.removeItem(sessionKey);
  sessionId = null;
  statsDialogStarted = false;
  if (statsDialogLog) statsDialogLog.innerHTML = "";
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

knowledgeButton?.addEventListener("click", () => {
  closeSettingsDrawer();
  openKnowledgeDialog();
});
closeKnowledgeButton?.addEventListener("click", closeKnowledgeDialog);

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
    const response = await fetch("/api/knowledge/url", {
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
  const response = await fetch("/api/knowledge/search", {
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
targetAllGeneralPopulationButton?.addEventListener("click", () => {
  targetPopulationDialogBody
    ?.querySelectorAll("[data-quick-target-option='true']")
    .forEach((input) => {
      input.checked = true;
    });
});

targetPopulationForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const payload = targetPopulationBatchPayload();
  if (!payload.length) return;
  closeTargetPopulationDialog();
  disableOldOptions();
  addMessage("user", "Quick Select Target Population");
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
  configureTypingEffectControl();
  configureMic();
  configureWorkspaceResizer();
  clearCurrentInputState();
  loadSessions();
  if (sessionId) {
    restoreSession(sessionId);
  } else {
    sendMessage("", false);
  }
});

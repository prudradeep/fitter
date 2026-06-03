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
const sessionEmpty = document.querySelector("#sessionEmpty");
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
let renderedVisualKey = "";
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
    title: "Select the transition sector",
    text: "Transport, housing, and energy pathways change which hazards and profiles matter most.",
  },
  hazards: {
    index: 3,
    title: "Identify social hazards",
    text: "This stage captures risks, negative impacts, and evidence for the selected policy context.",
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

function sessionHasCountry(session = currentSession) {
  return Boolean(session?.country);
}

function updateStageVisual(step = "", session = {}, options = currentOptions) {
  currentStep = step;
  currentOptions = options || [];
  const key = stageKeyForStep(step);
  const visual = stageVisuals[key] || stageVisuals.country;
  if (stageVisualTitle) stageVisualTitle.textContent = visual.title;
  if (stageVisualText) stageVisualText.textContent = visual.text;
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
}

function showStageMap() {
  if (stageMap) {
    stageMap.hidden = false;
    restartStageAnimation(stageMap);
  }
  if (stageIconGrid) stageIconGrid.hidden = true;
}

function showStageIcons() {
  if (stageMap) stageMap.hidden = true;
  if (stageIconGrid) {
    stageIconGrid.hidden = false;
    restartStageAnimation(stageIconGrid);
  }
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
  if (key === "country") {
    await renderCountrySelectionMap();
    return;
  }
  if (key === "region") {
    await renderRegionMap(session.country, session.region);
    return;
  }
  renderStageIcons(key, session, options);
}

async function renderCountrySelectionMap() {
  if (!stageMap || !window.Highcharts || !europeMapPath) {
    renderStageIcons("country");
    return;
  }
  const visualKey = "country-map";
  if (renderedVisualKey === visualKey) return;
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
    renderStageIcons("country");
  }
}

async function renderRegionMap(country, region) {
  const countryMapPath = countryMapData.get(country);
  if (!stageMap || !window.Highcharts || !countryMapPath) {
    renderStageIcons("region");
    return;
  }
  const visualKey = `region-map-${country}-${region || ""}`;
  if (renderedVisualKey === visualKey) return;
  renderedVisualKey = visualKey;
  const renderId = ++stageVisualRenderId;
  showStageMap();

  try {
    const topology = await fetchMapTopology(countryMapPath);
    if (renderId !== stageVisualRenderId) return;
    const selectedRegion = normalizeForMatch(region || "");
    const data = topology.features.map((feature) => {
      const name = feature.properties.name || feature.properties.NAME_1 || "";
      const selected = selectedRegion && normalizeForMatch(name) === selectedRegion;
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
          states: { hover: { color: "#6d22c7" } },
        },
      },
      series: [{ name: "Region", data, joinBy: "hc-key", nullColor: "#c7ccd3" }],
    });
  } catch (error) {
    console.error("Region stage map failed", error);
    renderStageIcons("region");
  }
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
    text: `${label} is available for ${session.country || "the selected country"}.`,
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

function renderStageIcons(key, session = {}, options = currentOptions) {
  if (!stageIconGrid) return;
  const visualKey = `icons-${key}-${session.country || ""}-${session.hazard_count || 0}-${session.affected_profile_count || 0}-${session.mitigation_measure_count || 0}-${options.map((option) => option.label).join("|")}`;
  if (renderedVisualKey === visualKey) return;
  renderedVisualKey = visualKey;
  stageVisualRenderId += 1;
  showStageIcons();

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
        <article class="stage-icon-card" style="--stage-card-index: ${index}">
          <span class="stage-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="${item.icon}"></path>
            </svg>
          </span>
          <h3>${item.title}</h3>
          <p${item.stat ? ' class="stage-stat-value"' : ""}>${item.text}</p>
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

function scrollToBottom(targetLog = chatLog) {
  if (!targetLog) return;
  targetLog.scrollTop = targetLog.scrollHeight;
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
  targetLog.appendChild(row);
  scrollToBottom(targetLog);
  return row;
}

async function typeServerMessage(row, html, targetLog = chatLog) {
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
    await typeNode(child, bubble);
  }
  bubble.appendChild(timestamp);
  scrollToBottom(targetLog);
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
  currentSession = session || {};
  targetPopulationQuestions = Array.isArray(session?.target_population_questions)
    ? session.target_population_questions
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
  updateStageVisual(currentStep, currentSession, currentOptions);
}

function syncTargetPopulationQuestion(step, options = []) {
  if (step !== "target_population_question") {
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
      if (!selected.length) return;
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

function openTargetPopulationDialog() {
  if (!targetPopulationDialogBody) return;
  const questions = targetPopulationQuestions.length
    ? targetPopulationQuestions
    : currentTargetPopulationQuestion
      ? [currentTargetPopulationQuestion]
      : [];
  targetPopulationDialogBody.innerHTML = "";
  questions.forEach((question) => {
    const section = document.createElement("fieldset");
    section.className = "target-dialog-question";
    section.dataset.questionId = question.id;
    const legend = document.createElement("legend");
    legend.textContent = question.question || "Target population question";
    section.appendChild(legend);
    (question.options || []).forEach((option) => {
      const label = document.createElement("label");
      label.className = "target-option-check";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = option;
      checkbox.dataset.quickTargetOption = "true";
      const span = document.createElement("span");
      span.textContent = option;
      label.appendChild(checkbox);
      label.appendChild(span);
      section.appendChild(label);
    });
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
  if (!cleanMessage) return;
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

function showKnowledgeMessage(message, isError = true) {
  if (!knowledgeMessage) return;
  knowledgeMessage.textContent = message;
  knowledgeMessage.hidden = false;
  knowledgeMessage.classList.toggle("success", !isError);
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
    updateSessionCard(data.session);
    setInputMode(data.input_mode || "text", data.step, data.options || []);
    renderOptions(data.options || [], data.other_options || []);
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
  closeStatsDeepDiveDialog();
  clearCurrentInputState();
  if (!sessionHasCountry()) {
    messageInput.value = "";
    updateOptionHighlight();
    if (sessionId) {
      await restoreSession(sessionId);
    } else {
      await sendMessage("", false);
    }
    return;
  }

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

knowledgeButton?.addEventListener("click", openKnowledgeDialog);
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

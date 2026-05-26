document.querySelectorAll("[data-coverage-map]").forEach(async (container) => {
  if (!window.Highcharts) return;

  const coverage = JSON.parse(container.dataset.coverage || "[]");
  const coverageByCode = new Map(coverage.map((item) => [item.code, item]));
  const activeCodes = new Set(coverage.map((item) => item.code));
  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");

  try {
    const response = await fetch("https://code.highcharts.com/mapdata/custom/europe.geo.json");
    const topology = await response.json();
    /* const mainCountries = [
      "Germany",
      "Hungary",
      "Ireland",
      "Italy",
      "Portugal",
      "Spain"
    ];

    const nearbyCountries = [
      "France",
      "Switzerland",
      "Austria",
      "Slovenia",
      "Croatia",
      "Slovakia",
      "Czech Republic",
      "Belgium",
      "Netherlands",
      "Luxembourg"
    ];

    const visibleCountries = [...mainCountries, ...nearbyCountries];
    topology.features = topology.features.filter((feature) => visibleCountries.includes(feature.properties.name)); */
    const data = topology.features.map((feature) => {
      const code = feature.properties["iso-a2"];
      const item = coverageByCode.get(code);
      const enabledCountry = activeCodes.has(code);
      return {
        "hc-key": feature.properties["hc-key"],
        value: enabledCountry ? 1 : 0,
        color: enabledCountry ? "#4d4d4d" : "#c7ccd3",
        states: {
          hover: {
            color: enabledCountry ? "#6d22c7" : "#c7ccd3",
            borderColor: enabledCountry ? "#4d5563" : "#7a8493",
          },
        },
        country: item?.country || feature.properties.name,
        sectors: item?.sectors || "Not configured",
        hazards: item?.hazards ?? 0,
        analyses: item?.analyses ?? 0,
        enabledCountry,
      };
    });
    const activeData = data.filter((point) => point.enabledCountry);

    Highcharts.mapChart(container, {
      chart: {
        map: topology,
      },
      title: { text: null },
      credits: { enabled: false },
      legend: { enabled: false },
      mapNavigation: { enabled: false, enableMouseWheelZoom: false },
      tooltip: {
        useHTML: true,
        borderWidth: 0,
        padding: 0,
        shadow: false,
        backgroundColor: "transparent",
        formatter() {
          if (!this.point.enabledCountry) return false;
          const country = escapeHtml(this.point.country);
          const sectors = escapeHtml(this.point.sectors);
          return `
            <div class="map-tooltip-card">
              <div class="map-tooltip-title">
                <span aria-hidden="true"></span>
                <strong>${country}</strong>
              </div>
              <div class="map-tooltip-rule"></div>
              <div class="map-tooltip-section">
                <small>SECTORS ANALYSED</small>
                <p>${sectors.replace(/, /g, " / ")}</p>
              </div>
              <dl class="map-tooltip-stats">
                <div>
                  <dt>Hazards</dt>
                  <dd>${this.point.hazards}</dd>
                </div>
              </dl>
              <div class="map-tooltip-accent"></div>
              <div class="map-tooltip-total">
                <em>Analyses so far</em>
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
          allAreas: true,
          states: {
            hover: { color: "#c7ccd3", borderColor: "#7a8493" },
          },
        },
      },
      series: [
        {
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
          data: activeData,
          joinBy: "hc-key",
          color: "#4d4d4d",
          nullColor: "transparent",
          borderColor: "#6d22c7",
          states: {
            hover: {
              color: "#6d22c7",
              borderColor: "#4d5563",
            },
          },
        },
      ],
    });
  } catch (error) {
    container.textContent = "Map unavailable";
    console.error("Coverage map failed", error);
  }
});

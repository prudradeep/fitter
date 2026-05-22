document.querySelectorAll("[data-coverage-map]").forEach(async (container) => {
  if (!window.Highcharts) return;

  const coverage = JSON.parse(container.dataset.coverage || "[]");
  const coverageByCode = new Map(coverage.map((item) => [item.code, item]));
  const activeCodes = new Set(coverage.map((item) => item.code));

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
      return {
        "hc-key": feature.properties["hc-key"],
        value: activeCodes.has(code) ? 1 : 0,
        country: item?.country || feature.properties.name,
        sectors: item?.sectors || "Not configured",
        enabledCountry: activeCodes.has(code),
      };
    });

    Highcharts.mapChart(container, {
      chart: {
        map: topology,
       
      },
      title: { text: null },
      credits: { enabled: false },
      legend: { enabled: false },
      mapNavigation: { enabled: false, enableMouseWheelZoom: true, },
      tooltip: {
        useHTML: true,
        formatter() {
          if (!this.point.enabledCountry) return false;
          return `
            <div class="map-tooltip">
              <strong>${this.point.country}</strong>
              <span>${this.point.sectors}</span>
            </div>
          `;
        },
      },
      colorAxis: {
        dataClasses: [
          { from: 0, to: 0, color: "#c7ccd3" },
          { from: 1, to: 1, color: "#4d4d4d" },
        ],
      },
      plotOptions: {
        map: {
          borderColor: "#768294",
          borderWidth: 0.45,
          states: {
            hover: { color: "#8b3ff2" },
          },
        },
      },
      series: [
        {
          data,
          joinBy: "hc-key",
          nullColor: "#c7ccd3",
        },
      ],
    });
  } catch (error) {
    container.textContent = "Map unavailable";
    console.error("Coverage map failed", error);
  }
});

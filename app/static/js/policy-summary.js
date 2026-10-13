const policySummarySections = new Map([
  ["policy details", document.querySelector(".policy-items")],
  ["policy objectives", document.querySelector(".works-items")],
  ["intended benefits", document.querySelector(".benefit-items")],
  ["target actors/beneficiaries", document.querySelector(".people-items")],
]);

function parsePolicySummary(markdown) {
  const sections = new Map();
  let currentSection = "";
  let currentItem = null;
  for (const line of String(markdown || "").split(/\r?\n/)) {
    const heading = line.match(/^###\s+(.+?)\s*$/);
    if (heading) {
      currentSection = heading[1].trim().toLowerCase();
      currentItem = null;
      if (!sections.has(currentSection)) sections.set(currentSection, []);
      continue;
    }
    const bullet = line.match(/^\s*-\s+\*\*(.+?):\*\*\s*(.*)$/);
    if (bullet && currentSection) {
      currentItem = { label: bullet[1].trim(), text: bullet[2].trim() };
      sections.get(currentSection).push(currentItem);
    } else if (/^\s*-\s+\S/.test(line) && currentSection) {
      currentItem = { label: "", text: line.replace(/^\s*-\s+/, "").trim() };
      sections.get(currentSection).push(currentItem);
    } else if (currentItem && line.trim()) {
      currentItem.text += ` ${line.trim()}`;
    }
  }
  if (!sections.has("target actors/beneficiaries")) {
    const legacyGroups = sections.get("target socio demographic groups")
      || sections.get("socio-demographic groups benefited");
    if (legacyGroups) sections.set("target actors/beneficiaries", legacyGroups);
  }
  if (!sections.has("policy objectives") && sections.has("original policy objectives")) {
    sections.set("policy objectives", sections.get("original policy objectives"));
  }
  return sections;
}

function policyPointTitle(label, text) {
  if (label && label !== "Document excerpt") return label;
  const words = String(text || "").replace(/\*\*/g, "").trim().split(/\s+/);
  return words.slice(0, 7).join(" ") + (words.length > 7 ? "…" : "");
}

function renderPolicySummary(policy, summary, sources = {}) {
  const title = String(policy || "").trim();
  const content = String(summary || "").trim();
  document.querySelector(".hero h1").textContent = title || "Add a new policy";
  document.title = title ? `${title} - Policy Summary` : "Policy Summary";
  document.querySelector(".policy-badge").textContent = title ? "Selected Policy" : "New Policy";

  const parsed = parsePolicySummary(content);
  const hasSummary = [...policySummarySections.keys()].some((key) => parsed.get(key)?.length);
  document.body.classList.toggle("has-summary", hasSummary);
  document.querySelector("#summaryStatus").hidden = hasSummary;
  for (const [heading, container] of policySummarySections) {
    const cards = (parsed.get(heading) || []).map(({ label, text }, index) => {
      const card = document.createElement("div");
      card.className = "item";
      const showLabel = heading !== "policy objectives" || (label && label.toLowerCase() !== "original objective");
      if (showLabel) {
        const labelElement = document.createElement("strong");
        labelElement.textContent = policyPointTitle(label, text);
        card.append(labelElement);
      }
      if (heading === "policy objectives" && text) {
        const description = document.createElement("p");
        description.textContent = text;
        card.append(description);
      }
      const sourceHeading = heading === "policy objectives" && !sources[heading]
        ? "original policy objectives"
        : heading === "target actors/beneficiaries" && !sources[heading]
          ? (sources["target socio demographic groups"]
            ? "target socio demographic groups"
            : "socio-demographic groups benefited")
          : heading;
      const rows = sources[sourceHeading]?.[index];
      if (Array.isArray(rows) && rows.length) {
        const source = document.createElement("button");
        source.type = "button";
        source.className = "policy-source-tag";
        source.textContent = "Source";
        source.setAttribute("aria-label", `Show source context for ${policyPointTitle(label, text)}`);
        source.addEventListener("click", () => {
          window.parent.postMessage({ type: "policy-summary:source", rows }, window.location.origin);
        });
        card.append(source);
      }
      return card;
    });
    container.replaceChildren(...cards);
    container.closest(".section").hidden = !cards.length;
  }
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.source !== window.parent) return;
  if (event.data?.type !== "policy-summary:update") return;
  renderPolicySummary(event.data.policy, event.data.summary, event.data.sources);
});

renderPolicySummary("", "");

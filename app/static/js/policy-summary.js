const policySummarySections = new Map([
  ["policy details", document.querySelector(".policy-items")],
  ["mechanisms", document.querySelector(".works-items")],
  ["intended benefits", document.querySelector(".benefit-items")],
  ["socio-demographic groups benefited", document.querySelector(".people-items")],
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
  return sections;
}

function policyPointTitle(label, text) {
  if (label && label !== "Document excerpt") return label;
  const words = String(text || "").replace(/\*\*/g, "").trim().split(/\s+/);
  return words.slice(0, 7).join(" ") + (words.length > 7 ? "…" : "");
}

function renderPolicySummary(policy, summary) {
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
    const cards = (parsed.get(heading) || []).map(({ label, text }) => {
      const card = document.createElement("div");
      card.className = "item";
      const labelElement = document.createElement("strong");
      labelElement.textContent = policyPointTitle(label, text);
      card.append(labelElement);
      return card;
    });
    container.replaceChildren(...cards);
    container.closest(".section").hidden = !cards.length;
  }
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.source !== window.parent) return;
  if (event.data?.type !== "policy-summary:update") return;
  renderPolicySummary(event.data.policy, event.data.summary);
});

renderPolicySummary("", "");

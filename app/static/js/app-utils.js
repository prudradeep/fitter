function plainTextFromHtml(html) {
  const element = document.createElement("div");
  element.innerHTML = html;
  return element.textContent.replace(/\s+/g, " ").trim();
}

function voiceSummaryFromHtml(html) {
  const text = plainTextFromHtml(html);
  if (!text) return "";

  const closing = "";
  const cleaned = text
    .replace(/\b(Continue|Back|Skip|Yes|No|Add more|Finish)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return closing;

  const sentences = cleaned
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence.length >= 18 && sentence !== closing);
  const summarySentences = sentences.length
    ? sentences.slice(0, 2)
    : [cleaned.slice(0, 220).trim()];

  return [...summarySentences, closing].join(" ");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
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

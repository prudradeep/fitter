function plainTextFromHtml(html) {
  const element = document.createElement("div");
  element.innerHTML = html;
  return element.textContent.replace(/\s+/g, " ").trim();
}

function clearElement(element) {
  if (element) element.replaceChildren();
}

function renderTrustedHtml(element, html) {
  if (!element) return;
  // Use only for server-sanitized chat HTML or escaped template fragments.
  element.innerHTML = html;
}

function trustedHtmlFragment(html) {
  const template = document.createElement("template");
  renderTrustedHtml(template, html);
  return template.content;
}

function createElement(tagName, options = {}, children = []) {
  const element = document.createElement(tagName);
  if (options.className) element.className = options.className;
  if (options.text !== undefined) element.textContent = options.text;
  if (options.html !== undefined) renderTrustedHtml(element, options.html);
  Object.entries(options.attrs || {}).forEach(([name, value]) => {
    if (value !== null && value !== undefined) {
      element.setAttribute(name, String(value));
    }
  });
  children.forEach((child) => {
    element.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return element;
}

function renderEmptyState(container, message, className = "sessions-empty") {
  if (!container) return;
  clearElement(container);
  container.appendChild(createElement("p", { className, text: message }));
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

function cookieValue(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || "";
}

function localStorageValue(name) {
  try {
    return localStorage.getItem(name) || "";
  } catch (_error) {
    return "";
  }
}

function setDrTransitionBackendBaseUrl(value) {
  const clean = String(value || "").trim().replace(/\/+$/, "");
  window.DrTransitionBackendBaseUrl = clean;
  if (clean) {
    try {
      localStorage.setItem("dr_transition_backend_base_url", clean);
    } catch (_error) {
      // Runtime config remains in memory when localStorage is unavailable.
    }
  }
}

function hostedBackendBaseUrl() {
  return String(
    window.DrTransitionBackendBaseUrl ||
    localStorageValue("dr_transition_backend_base_url") ||
    ""
  ).replace(/\/+$/, "");
}

function hostedApiUrl(url) {
  const value = String(url || "");
  if (/^https?:\/\//i.test(value)) return value;
  if (!value.startsWith("/api/") && !value.startsWith("/health/")) return value;
  const baseUrl = hostedBackendBaseUrl();
  return baseUrl ? `${baseUrl}${value}` : value;
}

function authHeaders(headers = {}) {
  const token = localStorageValue("dr_transition_auth_token");
  return token && !headers.Authorization ? { ...headers, Authorization: `Bearer ${token}` } : { ...headers };
}

function csrfHeaders(headers = {}) {
  const token = cookieValue("dr_transition_csrf") || localStorageValue("dr_transition_csrf");
  return token ? { ...headers, "X-CSRF-Token": token } : { ...headers };
}

function apiFetch(url, options = {}) {
  return fetch(hostedApiUrl(url), {
    ...options,
    credentials: "include",
    headers: authHeaders(options.headers || {}),
  });
}

function csrfFetch(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    return apiFetch(url, options);
  }
  return apiFetch(url, {
    ...options,
    headers: csrfHeaders(options.headers || {}),
  });
}

window.DrTransitionAPI = {
  apiFetch,
  csrfFetch,
  hostedApiUrl,
  hostedBackendBaseUrl,
  setBackendBaseUrl: setDrTransitionBackendBaseUrl,
};

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

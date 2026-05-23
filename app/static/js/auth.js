const authModal = document.querySelector("#authModal");
const startAnalysisButton = document.querySelector("#startAnalysisButton");
const closeAuthModalButton = document.querySelector(".auth-modal-close");
const authTabs = Array.from(document.querySelectorAll("[data-auth-tab]"));
const authPanels = Array.from(document.querySelectorAll(".auth-tab-panel"));
const signupForm = document.querySelector("#signupForm");
const steps = Array.from(document.querySelectorAll(".signup-step"));
const indicators = Array.from(document.querySelectorAll("[data-step-indicator]"));
const nextButton = document.querySelector("[data-next-step]");
const prevButton = document.querySelector("[data-prev-step]");
const passwordInput = document.querySelector("#signupPassword");
const confirmPasswordInput = document.querySelector("#confirmPassword");
const passwordRules = {
  length: document.querySelector('[data-rule="length"]'),
  upper: document.querySelector('[data-rule="upper"]'),
  lower: document.querySelector('[data-rule="lower"]'),
  number: document.querySelector('[data-rule="number"]'),
  symbol: document.querySelector('[data-rule="symbol"]'),
  match: document.querySelector('[data-rule="match"]'),
};

function setAuthMode(mode) {
  const activeMode = mode === "signup" ? "signup" : "login";
  authModal?.setAttribute("data-auth-mode", activeMode);
  authTabs.forEach((tab) => {
    const selected = tab.dataset.authTab === activeMode;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  authPanels.forEach((panel) => {
    panel.hidden = panel.id !== `${activeMode}Panel`;
  });
}

function openAuthModal(mode = authModal?.dataset.authMode || "login") {
  setAuthMode(mode);
  if (typeof authModal?.showModal === "function") {
    authModal.showModal();
  } else {
    authModal?.setAttribute("open", "");
  }
}

function showStep(stepNumber) {
  steps.forEach((step) => {
    step.hidden = step.dataset.step !== String(stepNumber);
  });
  indicators.forEach((indicator) => {
    indicator.classList.toggle("active", indicator.dataset.stepIndicator === String(stepNumber));
  });
}

function validatePasswordRules() {
  const password = passwordInput?.value || "";
  const confirmPassword = confirmPasswordInput?.value || "";
  const checks = {
    length: password.length >= 8,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    number: /\d/.test(password),
    symbol: /[^A-Za-z0-9]/.test(password),
    match: Boolean(password) && password === confirmPassword,
  };

  Object.entries(checks).forEach(([rule, passed]) => {
    passwordRules[rule]?.classList.toggle("valid", passed);
  });
  confirmPasswordInput?.setCustomValidity(checks.match ? "" : "Passwords do not match.");
  return Object.values(checks).every(Boolean);
}

startAnalysisButton?.addEventListener("click", () => openAuthModal("login"));
closeAuthModalButton?.addEventListener("click", () => authModal?.close());
authModal?.addEventListener("click", (event) => {
  if (event.target === authModal) authModal.close();
});
authTabs.forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.authTab));
});

nextButton?.addEventListener("click", () => {
  const firstStep = signupForm?.querySelector('[data-step="1"]');
  const fields = Array.from(firstStep?.querySelectorAll("input, select") || []);
  const valid = fields.every((field) => field.reportValidity()) && validatePasswordRules();
  if (valid) showStep(2);
});

prevButton?.addEventListener("click", () => {
  showStep(1);
});

signupForm?.addEventListener("submit", (event) => {
  const secondStep = signupForm.querySelector('[data-step="2"]');
  const fields = Array.from(secondStep?.querySelectorAll("input, select") || []);
  const valid = fields.every((field) => field.reportValidity()) && validatePasswordRules();
  if (!valid) {
    event.preventDefault();
    showStep(fields.some((field) => !field.validity.valid) ? 2 : 1);
  }
});

passwordInput?.addEventListener("input", validatePasswordRules);
confirmPasswordInput?.addEventListener("input", validatePasswordRules);
validatePasswordRules();
showStep(signupForm?.dataset.initialStep || 1);
setAuthMode(authModal?.dataset.authMode || "login");
if (authModal?.dataset.authOpen === "true") {
  openAuthModal(authModal.dataset.authMode || "login");
}

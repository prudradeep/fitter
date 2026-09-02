const authModal = document.querySelector("#authModal");
const startAnalysisButton = document.querySelector("#startAnalysisButton");
const aboutProjectModal = document.querySelector("#aboutProjectModal");
const aboutProjectButton = document.querySelector("#aboutProjectButton");
const closeAuthModalButton = document.querySelector(".auth-modal-close");
const closeAboutProjectModalButton = document.querySelector(".about-project-modal-close");
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

function openAboutProjectModal() {
  if (typeof aboutProjectModal?.showModal === "function") {
    aboutProjectModal.showModal();
  } else {
    aboutProjectModal?.setAttribute("open", "");
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

function stepFields(stepNumber) {
  const step = signupForm?.querySelector(`[data-step="${stepNumber}"]`);
  return Array.from(step?.querySelectorAll("input, select") || []);
}

function validateFields(fields) {
  return fields.every((field) => field.reportValidity());
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
aboutProjectButton?.addEventListener("click", openAboutProjectModal);
closeAuthModalButton?.addEventListener("click", () => authModal?.close());
closeAboutProjectModalButton?.addEventListener("click", () => aboutProjectModal?.close());
authModal?.addEventListener("click", (event) => {
  if (event.target === authModal) authModal.close();
});
aboutProjectModal?.addEventListener("click", (event) => {
  if (event.target === aboutProjectModal) aboutProjectModal.close();
});
authTabs.forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.authTab));
});

nextButton?.addEventListener("click", () => {
  const valid = validateFields(stepFields(1)) && validatePasswordRules();
  if (valid) showStep(2);
});

prevButton?.addEventListener("click", () => {
  showStep(1);
});

signupForm?.addEventListener("submit", (event) => {
  const firstStepFields = stepFields(1);
  const secondStepFields = stepFields(2);
  const firstStepValid = validateFields(firstStepFields) && validatePasswordRules();
  const secondStepValid = validateFields(secondStepFields);
  const valid = firstStepValid && secondStepValid;
  if (!valid) {
    event.preventDefault();
    showStep(firstStepValid ? 2 : 1);
    return;
  }
  steps.forEach((step) => {
    step.hidden = false;
  });
});

passwordInput?.addEventListener("input", validatePasswordRules);
confirmPasswordInput?.addEventListener("input", validatePasswordRules);
validatePasswordRules();
showStep(signupForm?.dataset.initialStep || 1);
setAuthMode(authModal?.dataset.authMode || "login");
if (authModal?.dataset.authOpen === "true") {
  openAuthModal(authModal.dataset.authMode || "login");
}

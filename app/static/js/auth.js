const authModal = document.querySelector("#authModal");
const authLanguageSelect = document.querySelector("#authLanguageSelect");
const authLanguageDisclaimer = document.querySelector("#authLanguageDisclaimer");
const startAnalysisButton = document.querySelector("#startAnalysisButton");
const aboutProjectModal = document.querySelector("#aboutProjectModal");
const aboutProjectButton = document.querySelector("#aboutProjectButton");
const aboutProjectFrame = document.querySelector(".about-project-frame");
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

const authPageTranslations = {
  de: {
  "Language disclaimer:": "Sprachhinweis:",
  'Please note that the platform only translates the final chat outputs. The entire AI processing takes place in "English". There may be errors in real-time translation as a result': 'Bitte beachten Sie, dass die Plattform nur die endgültigen Chat-Ausgaben übersetzt. Die gesamte KI-Verarbeitung erfolgt auf Englisch. Daher kann es bei der Übersetzung in Echtzeit zu Fehlern kommen.',
  "AI-Powered Analysis": "KI-gestützte Analyse",
  "Your Region, Your Data + FITTER EU Research": "Ihre Region, Ihre Daten + FITTER-EU-Forschung",
  "A roadmap to": "Ein Fahrplan für",
  "JUST Transition": "gerechten Wandel",
  "Fitter FairImpact Platform helps you identify:": "Die Fitter FairImpact Platform hilft Ihnen dabei, Folgendes zu erkennen:",
  "Hazards and Negative Impacts of Twin transition policy objectives": "Gefahren und negative Auswirkungen der politischen Ziele der doppelten Transformation",
  "The Socio demographic profiles affected by the policies in your region": "Die sozio-demografischen Gruppen, die von den Maßnahmen in Ihrer Region betroffen sind",
  "Mitigation Plans to counter the Hazards and corresponding Negative Impacts": "Maßnahmenpläne gegen Gefahren und die damit verbundenen negativen Auswirkungen",
  "Start Analysis": "Analyse starten",
  "About the Project": "Über das Projekt",
  "Close about the project dialog": "Dialog „Über das Projekt“ schließen",
  "Close login dialog": "Anmeldedialog schließen",
  "Continue your Twin-Transition policy analysis.": "Setzen Sie Ihre Analyse zur Politik der doppelten Transformation fort.",
  "Login": "Anmelden",
  "Sign up": "Registrieren",
  "Email": "E-Mail",
  "Password": "Passwort",
  "Name": "Name",
  "Confirm password": "Passwort bestätigen",
  "At least 8 characters": "Mindestens 8 Zeichen",
  "One uppercase letter": "Ein Großbuchstabe",
  "One lowercase letter": "Ein Kleinbuchstabe",
  "One number": "Eine Zahl",
  "One symbol": "Ein Sonderzeichen",
  "Passwords match": "Passwörter stimmen überein",
  "Next": "Weiter",
  "Designation": "Berufsbezeichnung",
  "Organisation type": "Organisationstyp",
  "Select type": "Typ auswählen",
  "Public sector": "Öffentlicher Sektor",
  "Private sector": "Privater Sektor",
  "Research": "Forschung",
  "Civil society": "Zivilgesellschaft",
  "Other": "Sonstiges",
  "Organisation name": "Name der Organisation",
  "Back": "Zurück",
  "Create account": "Konto erstellen",
  "Account": "Konto",
  "Organisation": "Organisation",
  "SIGNED IN": "ANGEMELDET",
  "Sign out": "Abmelden",
  "Display language": "Anzeigesprache",
  "European coverage map": "Karte der europäischen Abdeckung",
  "Platform overview": "Plattformübersicht",
  "Authentication options": "Anmeldeoptionen",
  "Signup progress": "Registrierungsfortschritt",
  "Fitter FairImpact Platform logo": "Logo der Fitter FairImpact Platform",
  "Passwords do not match.": "Die Passwörter stimmen nicht überein.",
  },
  es: {
    "Language disclaimer:": "Aviso sobre el idioma:",
    'Please note that the platform only translates the final chat outputs. The entire AI processing takes place in "English". There may be errors in real-time translation as a result': 'Tenga en cuenta que la plataforma solo traduce las respuestas finales del chat. Todo el procesamiento de IA se realiza en inglés. Por ello, puede haber errores en la traducción en tiempo real.',
    "AI-Powered Analysis": "Análisis con inteligencia artificial",
    "Your Region, Your Data + FITTER EU Research": "Tu región, tus datos + investigación de FITTER EU",
    "A roadmap to": "Una hoja de ruta hacia",
    "JUST Transition": "una transición justa",
    "Fitter FairImpact Platform helps you identify:": "La plataforma Fitter FairImpact te ayuda a identificar:",
    "Hazards and Negative Impacts of Twin transition policy objectives": "Los riesgos y efectos negativos de los objetivos políticos de la doble transición",
    "The Socio demographic profiles affected by the policies in your region": "Los perfiles sociodemográficos afectados por las políticas de tu región",
    "Mitigation Plans to counter the Hazards and corresponding Negative Impacts": "Planes de mitigación para contrarrestar los riesgos y sus efectos negativos",
    "Start Analysis": "Iniciar análisis", "About the Project": "Sobre el proyecto",
    "Close about the project dialog": "Cerrar el diálogo sobre el proyecto", "Close login dialog": "Cerrar el diálogo de inicio de sesión",
    "Continue your Twin-Transition policy analysis.": "Continúa tu análisis de políticas de doble transición.",
    "Login": "Iniciar sesión", "Sign up": "Registrarse", "Email": "Correo electrónico", "Password": "Contraseña",
    "Name": "Nombre", "Confirm password": "Confirmar contraseña", "At least 8 characters": "Al menos 8 caracteres",
    "One uppercase letter": "Una letra mayúscula", "One lowercase letter": "Una letra minúscula", "One number": "Un número",
    "One symbol": "Un símbolo", "Passwords match": "Las contraseñas coinciden", "Next": "Siguiente",
    "Designation": "Cargo", "Organisation type": "Tipo de organización", "Select type": "Selecciona un tipo",
    "Public sector": "Sector público", "Private sector": "Sector privado", "Research": "Investigación",
    "Civil society": "Sociedad civil", "Other": "Otro", "Organisation name": "Nombre de la organización",
    "Back": "Atrás", "Create account": "Crear cuenta", "Account": "Cuenta", "Organisation": "Organización",
    "SIGNED IN": "SESIÓN INICIADA", "Sign out": "Cerrar sesión",
    "Display language": "Idioma de visualización", "European coverage map": "Mapa de cobertura europea",
    "Platform overview": "Descripción de la plataforma", "Authentication options": "Opciones de autenticación",
    "Signup progress": "Progreso del registro", "Fitter FairImpact Platform logo": "Logotipo de Fitter FairImpact Platform",
    "Passwords do not match.": "Las contraseñas no coinciden.",
  },
  pt: {
    "Language disclaimer:": "Aviso sobre o idioma:",
    'Please note that the platform only translates the final chat outputs. The entire AI processing takes place in "English". There may be errors in real-time translation as a result': 'Tenha em atenção que a plataforma só traduz as respostas finais do chat. Todo o processamento de IA é feito em inglês. Por isso, podem ocorrer erros na tradução em tempo real.',
    "AI-Powered Analysis": "Análise com inteligência artificial",
    "Your Region, Your Data + FITTER EU Research": "A sua região, os seus dados + investigação FITTER EU",
    "A roadmap to": "Um roteiro para",
    "JUST Transition": "uma transição justa",
    "Fitter FairImpact Platform helps you identify:": "A plataforma Fitter FairImpact ajuda a identificar:",
    "Hazards and Negative Impacts of Twin transition policy objectives": "Os riscos e impactos negativos dos objetivos políticos da dupla transição",
    "The Socio demographic profiles affected by the policies in your region": "Os perfis sociodemográficos afetados pelas políticas da sua região",
    "Mitigation Plans to counter the Hazards and corresponding Negative Impacts": "Planos de mitigação para combater os riscos e os respetivos impactos negativos",
    "Start Analysis": "Iniciar análise", "About the Project": "Sobre o projeto",
    "Close about the project dialog": "Fechar o diálogo sobre o projeto", "Close login dialog": "Fechar o diálogo de início de sessão",
    "Continue your Twin-Transition policy analysis.": "Continue a sua análise das políticas de dupla transição.",
    "Login": "Iniciar sessão", "Sign up": "Registar", "Email": "E-mail", "Password": "Palavra-passe",
    "Name": "Nome", "Confirm password": "Confirmar palavra-passe", "At least 8 characters": "Pelo menos 8 caracteres",
    "One uppercase letter": "Uma letra maiúscula", "One lowercase letter": "Uma letra minúscula", "One number": "Um número",
    "One symbol": "Um símbolo", "Passwords match": "As palavras-passe coincidem", "Next": "Seguinte",
    "Designation": "Cargo", "Organisation type": "Tipo de organização", "Select type": "Selecionar tipo",
    "Public sector": "Setor público", "Private sector": "Setor privado", "Research": "Investigação",
    "Civil society": "Sociedade civil", "Other": "Outro", "Organisation name": "Nome da organização",
    "Back": "Voltar", "Create account": "Criar conta", "Account": "Conta", "Organisation": "Organização",
    "SIGNED IN": "SESSÃO INICIADA", "Sign out": "Terminar sessão",
    "Display language": "Idioma de apresentação", "European coverage map": "Mapa da cobertura europeia",
    "Platform overview": "Apresentação da plataforma", "Authentication options": "Opções de autenticação",
    "Signup progress": "Progresso do registo", "Fitter FairImpact Platform logo": "Logótipo da Fitter FairImpact Platform",
    "Passwords do not match.": "As palavras-passe não coincidem.",
  },
  it: {
    "Language disclaimer:": "Avviso sulla lingua:",
    'Please note that the platform only translates the final chat outputs. The entire AI processing takes place in "English". There may be errors in real-time translation as a result': 'La piattaforma traduce solo le risposte finali della chat. L’intero processo di IA avviene in inglese. Di conseguenza, la traduzione in tempo reale potrebbe contenere errori.',
    "AI-Powered Analysis": "Analisi basata sull’IA",
    "Your Region, Your Data + FITTER EU Research": "La tua regione, i tuoi dati + ricerca FITTER EU",
    "A roadmap to": "Un percorso verso",
    "JUST Transition": "una transizione giusta",
    "Fitter FairImpact Platform helps you identify:": "La piattaforma Fitter FairImpact ti aiuta a individuare:",
    "Hazards and Negative Impacts of Twin transition policy objectives": "I rischi e gli impatti negativi degli obiettivi politici della doppia transizione",
    "The Socio demographic profiles affected by the policies in your region": "I profili sociodemografici interessati dalle politiche della tua regione",
    "Mitigation Plans to counter the Hazards and corresponding Negative Impacts": "Piani di mitigazione per contrastare i rischi e i relativi impatti negativi",
    "Start Analysis": "Avvia analisi", "About the Project": "Informazioni sul progetto",
    "Close about the project dialog": "Chiudi la finestra sul progetto", "Close login dialog": "Chiudi la finestra di accesso",
    "Continue your Twin-Transition policy analysis.": "Continua l’analisi delle politiche per la doppia transizione.",
    "Login": "Accedi", "Sign up": "Registrati", "Email": "E-mail", "Password": "Password",
    "Name": "Nome", "Confirm password": "Conferma password", "At least 8 characters": "Almeno 8 caratteri",
    "One uppercase letter": "Una lettera maiuscola", "One lowercase letter": "Una lettera minuscola", "One number": "Un numero",
    "One symbol": "Un simbolo", "Passwords match": "Le password corrispondono", "Next": "Avanti",
    "Designation": "Qualifica", "Organisation type": "Tipo di organizzazione", "Select type": "Seleziona il tipo",
    "Public sector": "Settore pubblico", "Private sector": "Settore privato", "Research": "Ricerca",
    "Civil society": "Società civile", "Other": "Altro", "Organisation name": "Nome dell’organizzazione",
    "Back": "Indietro", "Create account": "Crea account", "Account": "Account", "Organisation": "Organizzazione",
    "SIGNED IN": "ACCESSO EFFETTUATO", "Sign out": "Esci",
    "Display language": "Lingua di visualizzazione", "European coverage map": "Mappa della copertura europea",
    "Platform overview": "Panoramica della piattaforma", "Authentication options": "Opzioni di autenticazione",
    "Signup progress": "Avanzamento della registrazione", "Fitter FairImpact Platform logo": "Logo di Fitter FairImpact Platform",
    "Passwords do not match.": "Le password non corrispondono.",
  },
  hu: {
    "Language disclaimer:": "Nyelvi tájékoztató:",
    'Please note that the platform only translates the final chat outputs. The entire AI processing takes place in "English". There may be errors in real-time translation as a result': 'A platform csak a végső csevegési válaszokat fordítja le. A teljes MI-feldolgozás angolul történik, ezért a valós idejű fordítás hibákat tartalmazhat.',
    "AI-Powered Analysis": "Mesterséges intelligenciával támogatott elemzés",
    "Your Region, Your Data + FITTER EU Research": "Az Ön régiója, az Ön adatai + FITTER EU-kutatás",
    "A roadmap to": "Útmutató a",
    "JUST Transition": "igazságos átmenethez",
    "Fitter FairImpact Platform helps you identify:": "A Fitter FairImpact Platform segít azonosítani:",
    "Hazards and Negative Impacts of Twin transition policy objectives": "A kettős átállás szakpolitikai céljainak kockázatait és negatív hatásait",
    "The Socio demographic profiles affected by the policies in your region": "Az Ön régiójában az intézkedések által érintett társadalmi-demográfiai csoportokat",
    "Mitigation Plans to counter the Hazards and corresponding Negative Impacts": "A kockázatok és a kapcsolódó negatív hatások ellensúlyozására szolgáló intézkedési terveket",
    "Start Analysis": "Elemzés indítása", "About the Project": "A projektről",
    "Close about the project dialog": "A projektről szóló párbeszédablak bezárása", "Close login dialog": "Bejelentkezési ablak bezárása",
    "Continue your Twin-Transition policy analysis.": "Folytassa a kettős átállásra vonatkozó szakpolitikai elemzést.",
    "Login": "Bejelentkezés", "Sign up": "Regisztráció", "Email": "E-mail-cím", "Password": "Jelszó",
    "Name": "Név", "Confirm password": "Jelszó megerősítése", "At least 8 characters": "Legalább 8 karakter",
    "One uppercase letter": "Egy nagybetű", "One lowercase letter": "Egy kisbetű", "One number": "Egy szám",
    "One symbol": "Egy szimbólum", "Passwords match": "A jelszavak egyeznek", "Next": "Tovább",
    "Designation": "Beosztás", "Organisation type": "Szervezet típusa", "Select type": "Válasszon típust",
    "Public sector": "Közszféra", "Private sector": "Magánszektor", "Research": "Kutatás",
    "Civil society": "Civil társadalom", "Other": "Egyéb", "Organisation name": "Szervezet neve",
    "Back": "Vissza", "Create account": "Fiók létrehozása", "Account": "Fiók", "Organisation": "Szervezet",
    "SIGNED IN": "BEJELENTKEZVE", "Sign out": "Kijelentkezés",
    "Display language": "Megjelenítés nyelve", "European coverage map": "Európai lefedettségi térkép",
    "Platform overview": "A platform áttekintése", "Authentication options": "Hitelesítési lehetőségek",
    "Signup progress": "Regisztráció állapota", "Fitter FairImpact Platform logo": "A Fitter FairImpact Platform logója",
    "Passwords do not match.": "A jelszavak nem egyeznek.",
  },
};

function translateAuthPage(language) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest("script,style,textarea,input")) continue;
    if (node.__authSource === undefined) node.__authSource = node.data;
    const source = node.__authSource;
    const trimmed = source.trim();
    if (!trimmed) continue;
    const translated = authPageTranslations[language]?.[trimmed];
    if (translated) {
      const leading = source.match(/^\s*/)?.[0] || "";
      const trailing = source.match(/\s*$/)?.[0] || "";
      node.data = `${leading}${translated}${trailing}`;
    } else {
      node.data = source;
    }
  }
  document.querySelectorAll("[aria-label], [title], [alt]").forEach((element) => {
    ["aria-label", "title", "alt"].forEach((attribute) => {
      const current = element.getAttribute(attribute);
      if (current === null) return;
      const sourceKey = `__auth${attribute}`;
      if (element[sourceKey] === undefined) element[sourceKey] = current;
      const source = element[sourceKey];
      element.setAttribute(attribute, authPageTranslations[language]?.[source] || source);
    });
  });
}

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
  const language = localStorage.getItem("dr_transition_display_language") || "en";
  const supportedLanguages = new Set(["de", "es", "pt", "it", "hu"]);
  if (supportedLanguages.has(language) && aboutProjectFrame) {
    const url = new URL(aboutProjectFrame.getAttribute("src") || "", window.location.href);
    url.pathname = url.pathname.replace(/\.[a-z]{2}(?=\.html$)/i, "");
    url.pathname = url.pathname.replace(/\.html$/i, `.${language}.html`);
    aboutProjectFrame.src = `${url.pathname}${url.search}${url.hash}`;
  }
  if (typeof aboutProjectModal?.showModal === "function") {
    aboutProjectModal.showModal();
  } else {
    aboutProjectModal?.setAttribute("open", "");
  }
}

if (authLanguageSelect) {
  const languageKey = "dr_transition_display_language";
  const supportedLanguages = new Set(["en", "de", "es", "pt", "it", "hu"]);
  let savedLanguage = "en";
  try {
    savedLanguage = localStorage.getItem(languageKey) || "en";
  } catch (_) {}
  if (!supportedLanguages.has(savedLanguage)) savedLanguage = "en";
  authLanguageSelect.value = savedLanguage;
  document.documentElement.lang = savedLanguage;
  translateAuthPage(savedLanguage);
  if (authLanguageDisclaimer) authLanguageDisclaimer.hidden = savedLanguage === "en";
  authLanguageSelect.addEventListener("change", () => {
    const language = supportedLanguages.has(authLanguageSelect.value) ? authLanguageSelect.value : "en";
    authLanguageSelect.value = language;
    document.documentElement.lang = language;
    translateAuthPage(language);
    if (authLanguageDisclaimer) authLanguageDisclaimer.hidden = language === "en";
    try {
      localStorage.setItem(languageKey, language);
    } catch (_) {}
  });
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
  const language = authLanguageSelect?.value || "en";
  const mismatchMessage = authPageTranslations[language]?.["Passwords do not match."] || "Passwords do not match.";
  confirmPasswordInput?.setCustomValidity(checks.match ? "" : mismatchMessage);
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

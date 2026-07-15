use std::{
    collections::HashMap,
    env, fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

use anyhow::{Context, Result};
use kb_store::{BatchResult, DeleteBatch, KnowledgeDocument, KnowledgeStore, ScopeManifest, StoreStatus, UpsertBatch};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

mod kb_store;

#[derive(Debug, Deserialize)]
struct DesktopConfig {
    backend: BackendConfig,
    ollama: OllamaConfig,
}

#[derive(Debug, Deserialize)]
struct BackendConfig {
    #[serde(rename = "baseUrl")]
    base_url: String,
    #[serde(rename = "healthUrl")]
    health_url: String,
    #[serde(rename = "authCheckUrl")]
    auth_check_url: String,
}

#[derive(Debug, Deserialize)]
struct OllamaConfig {
    #[serde(rename = "baseUrl")]
    base_url: String,
    #[serde(rename = "selectedModel")]
    selected_model: String,
    #[serde(rename = "embeddingModel")]
    embedding_model: String,
}

#[derive(Debug, Clone, Serialize)]
struct RuntimeDiagnostics {
    ready: bool,
    title: String,
    summary: String,
    checks: Vec<RuntimeCheck>,
    logs_dir: String,
}

#[derive(Debug, Clone, Serialize)]
struct RuntimeCheck {
    name: String,
    ok: bool,
    detail: String,
    action: String,
}

#[derive(Debug, Clone, Serialize)]
struct EffectiveRuntimeConfig {
    backend: EffectiveBackendConfig,
    ollama: EffectiveOllamaConfig,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct EffectiveBackendConfig {
    base_url: String,
    health_url: String,
    auth_check_url: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct EffectiveOllamaConfig {
    base_url: String,
    chat_model: String,
    embedding_model: String,
}

#[derive(Debug, Clone, Deserialize)]
struct OllamaChatMessage {
    role: String,
    content: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct OllamaModelStatus {
    base_url: String,
    chat_model: String,
    embedding_model: String,
    ollama_reachable: bool,
    chat_model_installed: bool,
    embedding_model_installed: bool,
    models: Vec<String>,
}

struct RuntimeState {
    diagnostics: Mutex<Option<RuntimeDiagnostics>>,
    knowledge_store: KnowledgeStore,
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            kb_store_status,
            kb_scope_manifest,
            kb_list_documents,
            kb_get_document,
            kb_upsert_documents,
            kb_delete_documents,
            kb_clear_temporary,
            runtime_config,
            ollama_embed_texts,
            ollama_chat,
            ollama_model_status
        ])
        .manage(RuntimeState {
            diagnostics: Mutex::new(None),
            knowledge_store: KnowledgeStore::new(knowledge_store_dir()),
        })
        .setup(|app| {
            let install_dir = install_dir()?;
            let config = load_config(&install_dir)?;
            let log_dir = log_dir()?;
            fs::create_dir_all(&log_dir)?;

            let state = app.state::<RuntimeState>();
            state.knowledge_store.init()?;
            let env_config = RuntimeEnv::load();
            let preflight = check_runtime(&config, &env_config, &log_dir);
            if !preflight.ready {
                set_diagnostics(&state, preflight);
                open_diagnostics_window(app)?;
                return Ok(());
            }

            let backend_base_url = backend_base_url(&config, &env_config);
            let url = url::Url::parse(backend_base_url).context("backend URL should be valid")?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Dr Transition")
                .inner_size(1280.0, 820.0)
                .min_inner_size(1024.0, 700.0)
                .build()?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let _ = window.label();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Dr Transition");
}

#[tauri::command]
fn runtime_status(state: tauri::State<'_, RuntimeState>) -> Option<RuntimeDiagnostics> {
    state
        .diagnostics
        .lock()
        .expect("runtime lock poisoned")
        .clone()
}

#[tauri::command]
fn kb_store_status(state: tauri::State<'_, RuntimeState>) -> Result<StoreStatus, String> {
    state.knowledge_store.status().map_err(error_text)
}

#[tauri::command]
fn kb_scope_manifest(
    state: tauri::State<'_, RuntimeState>,
    scope: String,
    session_id: Option<String>,
) -> Result<ScopeManifest, String> {
    state
        .knowledge_store
        .manifest(&scope, session_id.as_deref())
        .map_err(error_text)
}

#[tauri::command]
fn kb_list_documents(
    state: tauri::State<'_, RuntimeState>,
    scope: String,
    session_id: Option<String>,
) -> Result<Vec<KnowledgeDocument>, String> {
    state
        .knowledge_store
        .list_documents(&scope, session_id.as_deref())
        .map_err(error_text)
}

#[tauri::command]
fn kb_get_document(
    state: tauri::State<'_, RuntimeState>,
    scope: String,
    document_id: String,
    session_id: Option<String>,
) -> Result<Option<KnowledgeDocument>, String> {
    state
        .knowledge_store
        .get_document(&scope, session_id.as_deref(), &document_id)
        .map_err(error_text)
}

#[tauri::command]
fn kb_upsert_documents(state: tauri::State<'_, RuntimeState>, batch: UpsertBatch) -> Result<BatchResult, String> {
    state.knowledge_store.upsert_documents(batch).map_err(error_text)
}

#[tauri::command]
fn kb_delete_documents(state: tauri::State<'_, RuntimeState>, batch: DeleteBatch) -> Result<BatchResult, String> {
    state.knowledge_store.delete_documents(batch).map_err(error_text)
}

#[tauri::command]
fn kb_clear_temporary(state: tauri::State<'_, RuntimeState>, session_id: String) -> Result<(), String> {
    state.knowledge_store.clear_temporary(&session_id).map_err(error_text)
}

#[tauri::command]
fn runtime_config() -> Result<EffectiveRuntimeConfig, String> {
    effective_runtime_config().map_err(error_text)
}

#[tauri::command]
fn ollama_embed_texts(texts: Vec<String>) -> Result<Vec<Vec<f32>>, String> {
    let config = effective_runtime_config().map_err(error_text)?;
    let mut embeddings = Vec::with_capacity(texts.len());
    for text in texts {
        embeddings.push(
            ollama_embedding(
                &config.ollama.base_url,
                &config.ollama.embedding_model,
                &text,
            )
            .map_err(error_text)?,
        );
    }
    Ok(embeddings)
}

#[tauri::command]
fn ollama_chat(messages: Vec<OllamaChatMessage>, options: Option<Value>) -> Result<String, String> {
    let config = effective_runtime_config().map_err(error_text)?;
    ollama_chat_completion(
        &config.ollama.base_url,
        &config.ollama.chat_model,
        messages,
        options,
    )
    .map_err(error_text)
}

#[tauri::command]
fn ollama_model_status() -> Result<OllamaModelStatus, String> {
    let config = effective_runtime_config().map_err(error_text)?;
    let models = ollama_models(&config.ollama.base_url).unwrap_or_default();
    Ok(OllamaModelStatus {
        base_url: config.ollama.base_url.clone(),
        chat_model: config.ollama.chat_model.clone(),
        embedding_model: config.ollama.embedding_model.clone(),
        ollama_reachable: !models.is_empty() || health_ok(&config.ollama.base_url),
        chat_model_installed: model_present(&models, &config.ollama.chat_model),
        embedding_model_installed: model_present(&models, &config.ollama.embedding_model),
        models,
    })
}

fn install_dir() -> Result<PathBuf> {
    let exe = env::current_exe().context("failed to locate current executable")?;
    exe.parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| anyhow::anyhow!("failed to resolve installation directory"))
}

fn load_config(install_dir: &Path) -> Result<DesktopConfig> {
    let config_path = install_dir.join("config").join("default.config.json");
    let raw = fs::read_to_string(&config_path)
        .with_context(|| format!("failed to read {}", config_path.display()))?;
    serde_json::from_str(&raw).context("failed to parse desktop configuration")
}

fn effective_runtime_config() -> Result<EffectiveRuntimeConfig> {
    let install_dir = install_dir()?;
    let config = load_config(&install_dir)?;
    let env_config = RuntimeEnv::load();
    let backend_base = backend_base_url(&config, &env_config).to_string();
    let backend_url_overridden = backend_base != config.backend.base_url;
    let derived_health_url = join_url(&backend_base, "health/ready");
    let derived_auth_check_url = join_url(&backend_base, "api/sessions");
    let health_url = env_config
        .get("DR_TRANSITION_BACKEND_HEALTH_URL")
        .or_else(|| env_config.get("BACKEND_HEALTH_URL"))
        .map(ToString::to_string)
        .unwrap_or_else(|| {
            if backend_url_overridden {
                derived_health_url
            } else {
                config.backend.health_url
            }
        });
    let auth_check_url = env_config
        .get("DR_TRANSITION_BACKEND_AUTH_CHECK_URL")
        .or_else(|| env_config.get("BACKEND_AUTH_CHECK_URL"))
        .map(ToString::to_string)
        .unwrap_or_else(|| {
            if backend_url_overridden {
                derived_auth_check_url
            } else {
                config.backend.auth_check_url
            }
        });
    Ok(EffectiveRuntimeConfig {
        backend: EffectiveBackendConfig {
            base_url: backend_base,
            health_url,
            auth_check_url,
        },
        ollama: EffectiveOllamaConfig {
            base_url: env_config
                .get("OLLAMA_BASE_URL")
                .unwrap_or(&config.ollama.base_url)
                .to_string(),
            chat_model: env_config
                .get("OLLAMA_MODEL")
                .unwrap_or(&config.ollama.selected_model)
                .to_string(),
            embedding_model: env_config
                .get("OLLAMA_EMBEDDING_MODEL")
                .unwrap_or(&config.ollama.embedding_model)
                .to_string(),
        },
    })
}

fn log_dir() -> Result<PathBuf> {
    let base = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(env::temp_dir);
    Ok(base.join("DrTransition").join("logs"))
}

fn knowledge_store_dir() -> PathBuf {
    let base = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(env::temp_dir);
    base.join("DrTransition").join("knowledge")
}

fn error_text(error: anyhow::Error) -> String {
    error.to_string()
}

#[derive(Debug, Default)]
struct RuntimeEnv {
    values: HashMap<String, String>,
    paths: Vec<PathBuf>,
}

impl RuntimeEnv {
    fn load() -> Self {
        let paths = env_file_paths();
        let mut values = HashMap::new();
        for path in &paths {
            if let Ok(raw) = fs::read_to_string(path) {
                for line in raw.lines() {
                    if let Some((key, value)) = parse_env_line(line) {
                        values.insert(key, value);
                    }
                }
            }
        }
        Self { values, paths }
    }

    fn has_file(&self) -> bool {
        !self.paths.is_empty()
    }

    fn get(&self, key: &str) -> Option<&str> {
        self.values.get(key).map(String::as_str)
    }
}

fn env_file_paths() -> Vec<PathBuf> {
    [
        env::current_dir().ok().map(|path| path.join(".env")),
        env_path("PROGRAMDATA"),
        env_path("LOCALAPPDATA"),
    ]
    .into_iter()
    .flatten()
    .filter(|path| path.exists())
    .collect()
}

fn env_path(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .map(PathBuf::from)
        .map(|base| base.join("DrTransition").join(".env"))
}

fn parse_env_line(line: &str) -> Option<(String, String)> {
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.starts_with('#') {
        return None;
    }
    let (key, value) = trimmed.split_once('=')?;
    let key = key.trim();
    if key.is_empty() {
        return None;
    }
    Some((key.to_string(), unquote_env_value(value.trim())))
}

fn unquote_env_value(value: &str) -> String {
    if value.len() >= 2 {
        let bytes = value.as_bytes();
        let first = bytes[0];
        let last = bytes[value.len() - 1];
        if (first == b'"' && last == b'"') || (first == b'\'' && last == b'\'') {
            return value[1..value.len() - 1].to_string();
        }
    }
    value.to_string()
}

fn check_runtime(config: &DesktopConfig, env_config: &RuntimeEnv, log_dir: &Path) -> RuntimeDiagnostics {
    let mut checks = Vec::new();
    let backend_base_url = backend_base_url(config, env_config);
    let derived_health_url = join_url(backend_base_url, "health/ready");
    let derived_auth_check_url = join_url(backend_base_url, "api/sessions");
    let backend_url_overridden = backend_base_url != config.backend.base_url;
    let backend_health_url = env_config
        .get("DR_TRANSITION_BACKEND_HEALTH_URL")
        .or_else(|| env_config.get("BACKEND_HEALTH_URL"))
        .map(ToString::to_string)
        .unwrap_or_else(|| {
            if backend_url_overridden {
                derived_health_url.clone()
            } else {
                config.backend.health_url.clone()
            }
        });
    let auth_check_url = env_config
        .get("DR_TRANSITION_BACKEND_AUTH_CHECK_URL")
        .or_else(|| env_config.get("BACKEND_AUTH_CHECK_URL"))
        .map(ToString::to_string)
        .unwrap_or_else(|| {
            if backend_url_overridden {
                derived_auth_check_url.clone()
            } else {
                config.backend.auth_check_url.clone()
            }
        });
    let ollama_base_url = env_config
        .get("OLLAMA_BASE_URL")
        .unwrap_or(&config.ollama.base_url);
    let selected_model = env_config
        .get("OLLAMA_MODEL")
        .unwrap_or(&config.ollama.selected_model);
    let embedding_model = env_config
        .get("OLLAMA_EMBEDDING_MODEL")
        .unwrap_or(&config.ollama.embedding_model);

    checks.push(RuntimeCheck {
        name: "Runtime config".to_string(),
        ok: true,
        detail: if env_config.has_file() {
            "Loaded optional local overrides from .env, %ProgramData%\\DrTransition\\.env, or %LOCALAPPDATA%\\DrTransition\\.env. No secrets are required in desktop config.".to_string()
        } else {
            "Using packaged no-secret desktop config. Optional overrides were not found.".to_string()
        },
        action: "To override the hosted backend or local Ollama URL, create %LOCALAPPDATA%\\DrTransition\\.env with DR_TRANSITION_BACKEND_URL and OLLAMA_BASE_URL.".to_string(),
    });

    let backend_ok = health_ok(&backend_health_url);
    checks.push(RuntimeCheck {
        name: "Hosted backend".to_string(),
        ok: backend_ok,
        detail: if backend_ok {
            format!("Hosted backend is reachable at {backend_health_url}.")
        } else {
            format!("Hosted backend is not reachable at {backend_health_url}.")
        },
        action: format!("Check your network connection or set DR_TRANSITION_BACKEND_URL. Current app URL: {backend_base_url}"),
    });

    let auth_status = http_status(&auth_check_url);
    let auth_ok = matches!(auth_status, Some(200) | Some(401) | Some(403));
    checks.push(RuntimeCheck {
        name: "Hosted auth/session".to_string(),
        ok: auth_ok,
        detail: match auth_status {
            Some(200) => "Hosted session endpoint accepted the current request.".to_string(),
            Some(401) | Some(403) => "Hosted auth/session endpoint is reachable; sign in in the app window.".to_string(),
            Some(status) => format!("Hosted auth/session endpoint returned HTTP {status}."),
            None => format!("Hosted auth/session endpoint is not reachable at {auth_check_url}."),
        },
        action: "Sign in after the app opens. If this check fails, verify the hosted backend URL and network access.".to_string(),
    });

    let ollama_ok = health_ok(ollama_base_url);
    checks.push(RuntimeCheck {
        name: "Ollama service".to_string(),
        ok: ollama_ok,
        detail: if ollama_ok {
            format!("Ollama is reachable at {ollama_base_url}.")
        } else {
            format!("Ollama is not reachable at {ollama_base_url}.")
        },
        action: "Install Ollama and start it before launching Dr Transition.".to_string(),
    });

    let models = ollama_models(ollama_base_url).unwrap_or_default();
    for model in [selected_model, embedding_model] {
        let present = model_present(&models, model);
        checks.push(RuntimeCheck {
            name: format!("Ollama model: {model}"),
            ok: present,
            detail: if present {
                format!("{model} is downloaded in Ollama.")
            } else if ollama_ok {
                format!("{model} was not found in Ollama. Installed models: {}.", model_list_text(&models))
            } else {
                "Model status could not be checked because Ollama is offline.".to_string()
            },
            action: format!("Run: ollama pull {model}"),
        });
    }

    let ready = checks.iter().all(|check| check.ok);
    RuntimeDiagnostics {
        ready,
        title: if ready {
            "Dr Transition is ready".to_string()
        } else {
            "Setup needed before Dr Transition can start".to_string()
        },
        summary: if ready {
            "All required local dependencies were detected.".to_string()
        } else {
            "One or more required local dependencies are missing or offline.".to_string()
        },
        checks,
        logs_dir: log_dir.display().to_string(),
    }
}

fn backend_base_url<'a>(config: &'a DesktopConfig, env_config: &'a RuntimeEnv) -> &'a str {
    env_config
        .get("DR_TRANSITION_BACKEND_URL")
        .or_else(|| env_config.get("BACKEND_BASE_URL"))
        .unwrap_or(&config.backend.base_url)
}

fn join_url(base_url: &str, path: &str) -> String {
    format!("{}/{}", base_url.trim_end_matches('/'), path.trim_start_matches('/'))
}

fn ollama_models(base_url: &str) -> Result<Vec<String>> {
    let url = format!("{}/api/tags", base_url.trim_end_matches('/'));
    let raw = ureq::get(&url)
        .timeout(Duration::from_secs(5))
        .call()
        .context("failed to query Ollama tags")?
        .into_string()
        .context("failed to read Ollama response")?;
    let json: serde_json::Value = serde_json::from_str(&raw).context("failed to parse Ollama response")?;
    Ok(json
        .get("models")
        .and_then(|models| models.as_array())
        .into_iter()
        .flatten()
        .filter_map(|model| model.get("name").and_then(|name| name.as_str()))
        .map(ToString::to_string)
        .collect())
}

fn ollama_embedding(base_url: &str, model: &str, text: &str) -> Result<Vec<f32>> {
    let url = format!("{}/api/embeddings", base_url.trim_end_matches('/'));
    let payload = serde_json::to_string(&serde_json::json!({
        "model": model,
        "prompt": text,
    }))?;
    let raw = ureq::post(&url)
        .timeout(Duration::from_secs(30))
        .set("Content-Type", "application/json")
        .send_string(&payload)
        .context("failed to request Ollama embedding")?
        .into_string()
        .context("failed to read Ollama embedding response")?;
    let json: serde_json::Value = serde_json::from_str(&raw).context("failed to parse Ollama embedding response")?;
    json.get("embedding")
        .and_then(|value| value.as_array())
        .ok_or_else(|| anyhow::anyhow!("Ollama embedding response did not include an embedding"))?
        .iter()
        .map(|value| {
            value
                .as_f64()
                .map(|number| number as f32)
                .ok_or_else(|| anyhow::anyhow!("Ollama embedding response contained a non-numeric value"))
        })
        .collect()
}

fn ollama_chat_completion(
    base_url: &str,
    model: &str,
    messages: Vec<OllamaChatMessage>,
    options: Option<Value>,
) -> Result<String> {
    let url = format!("{}/api/chat", base_url.trim_end_matches('/'));
    let payload = serde_json::to_string(&serde_json::json!({
        "model": model,
        "messages": messages
            .into_iter()
            .map(|message| serde_json::json!({
                "role": message.role,
                "content": message.content,
            }))
            .collect::<Vec<_>>(),
        "stream": false,
        "options": options.unwrap_or_else(|| serde_json::json!({})),
    }))?;
    let raw = ureq::post(&url)
        .timeout(Duration::from_secs(120))
        .set("Content-Type", "application/json")
        .send_string(&payload)
        .context("failed to request Ollama chat completion")?
        .into_string()
        .context("failed to read Ollama chat response")?;
    let json: serde_json::Value = serde_json::from_str(&raw).context("failed to parse Ollama chat response")?;
    json.get("message")
        .and_then(|message| message.get("content"))
        .or_else(|| json.get("response"))
        .and_then(|content| content.as_str())
        .map(ToString::to_string)
        .ok_or_else(|| anyhow::anyhow!("Ollama chat response did not include message content"))
}

fn model_present(models: &[String], expected: &str) -> bool {
    models.iter().any(|model| model == expected || model.strip_suffix(":latest") == Some(expected))
}

fn model_list_text(models: &[String]) -> String {
    if models.is_empty() {
        "none".to_string()
    } else {
        models.join(", ")
    }
}

fn set_diagnostics(state: &RuntimeState, diagnostics: RuntimeDiagnostics) {
    *state
        .diagnostics
        .lock()
        .expect("runtime lock poisoned") = Some(diagnostics);
}

fn open_diagnostics_window(app: &tauri::App) -> Result<()> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Dr Transition Setup")
        .inner_size(980.0, 720.0)
        .min_inner_size(760.0, 560.0)
        .build()?;
    Ok(())
}

fn health_ok(url: &str) -> bool {
    ureq::get(url)
        .timeout(Duration::from_secs(2))
        .call()
        .map(|response| response.status() < 500)
        .unwrap_or(false)
}

fn http_status(url: &str) -> Option<u16> {
    match ureq::get(url).timeout(Duration::from_secs(3)).call() {
        Ok(response) => Some(response.status()),
        Err(ureq::Error::Status(status, _)) => Some(status),
        Err(_) => None,
    }
}

use std::{
    collections::HashMap,
    env, fs,
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

#[derive(Debug, Deserialize)]
struct DesktopConfig {
    backend: ServiceConfig,
    grounding: GroundingConfig,
    ollama: OllamaConfig,
}

#[derive(Debug, Deserialize)]
struct GroundingConfig {
    enabled: bool,
    reranker: ServiceConfig,
    nli: ServiceConfig,
}

#[derive(Debug, Deserialize)]
struct ServiceConfig {
    enabled: Option<bool>,
    #[serde(rename = "healthUrl")]
    health_url: String,
    executable: String,
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

struct RuntimeState {
    children: Mutex<Vec<Child>>,
    diagnostics: Mutex<Option<RuntimeDiagnostics>>,
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![runtime_status])
        .manage(RuntimeState {
            children: Mutex::new(Vec::new()),
            diagnostics: Mutex::new(None),
        })
        .setup(|app| {
            let install_dir = install_dir()?;
            let config = load_config(&install_dir)?;
            let log_dir = log_dir()?;
    fs::create_dir_all(&log_dir)?;

            let state = app.state::<RuntimeState>();
            let env_config = RuntimeEnv::load();
            let preflight = check_runtime(&config, &env_config, &log_dir);
            if !preflight.ready {
                set_diagnostics(&state, preflight);
                open_diagnostics_window(app)?;
                return Ok(());
            }

            if let Err(error) = start_services(&install_dir, &log_dir, &config, &state) {
                set_diagnostics(
                    &state,
                    failure_diagnostics(
                        "Services did not start",
                        &error.to_string(),
                        &log_dir,
                    ),
                );
                open_diagnostics_window(app)?;
                return Ok(());
            }

            let url =
                url::Url::parse("http://127.0.0.1:8000/").context("backend URL should be valid")?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Dr Transition")
                .inner_size(1280.0, 820.0)
                .min_inner_size(1024.0, 700.0)
                .build()?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let app = window.app_handle();
                if app.webview_windows().len() <= 1 {
                    if let Some(state) = app.try_state::<RuntimeState>() {
                        stop_children(&state);
                    }
                }
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

fn start_services(
    install_dir: &Path,
    log_dir: &Path,
    config: &DesktopConfig,
    state: &RuntimeState,
) -> Result<()> {
    start_service(install_dir, log_dir, "backend", &config.backend, state)?;

    if config.grounding.enabled {
        start_service(
            install_dir,
            log_dir,
            "reranker",
            &config.grounding.reranker,
            state,
        )?;
        start_service(install_dir, log_dir, "nli", &config.grounding.nli, state)?;
    }

    wait_for_health("backend", &config.backend.health_url, Duration::from_secs(90))?;
    if config.grounding.enabled {
        wait_for_health(
            "reranker",
            &config.grounding.reranker.health_url,
            Duration::from_secs(120),
        )?;
        wait_for_health("nli", &config.grounding.nli.health_url, Duration::from_secs(120))?;
    }

    Ok(())
}

fn install_dir() -> Result<PathBuf> {
    let exe = env::current_exe().context("failed to locate current executable")?;
    exe.parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| anyhow!("failed to resolve installation directory"))
}

fn load_config(install_dir: &Path) -> Result<DesktopConfig> {
    let config_path = install_dir.join("config").join("default.config.json");
    let raw = fs::read_to_string(&config_path)
        .with_context(|| format!("failed to read {}", config_path.display()))?;
    serde_json::from_str(&raw).context("failed to parse desktop configuration")
}

fn log_dir() -> Result<PathBuf> {
    let base = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(env::temp_dir);
    Ok(base.join("DrTransition").join("logs"))
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
    [env::current_dir().ok().map(|path| path.join(".env")), env_path("PROGRAMDATA"), env_path("LOCALAPPDATA")]
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
        name: "Runtime environment".to_string(),
        ok: env_config.has_file(),
        detail: "Configuration file lookup checks .env, %ProgramData%\\DrTransition\\.env, and %LOCALAPPDATA%\\DrTransition\\.env.".to_string(),
        action: "Create or copy the app .env file to %LOCALAPPDATA%\\DrTransition\\.env or %ProgramData%\\DrTransition\\.env.".to_string(),
    });

    let mysql_ok = tcp_port_open("127.0.0.1:3306", Duration::from_secs(2));
    checks.push(RuntimeCheck {
        name: "MySQL".to_string(),
        ok: mysql_ok,
        detail: if mysql_ok {
            "MySQL is accepting local connections on 127.0.0.1:3306.".to_string()
        } else {
            "MySQL is not reachable on 127.0.0.1:3306.".to_string()
        },
        action: "Install/start MySQL and create the Dr Transition database user/schema from your seed script.".to_string(),
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

fn tcp_port_open(address: &str, timeout: Duration) -> bool {
    address
        .to_socket_addrs()
        .ok()
        .and_then(|mut addresses| addresses.next())
        .and_then(|address| TcpStream::connect_timeout(&address, timeout).ok())
        .is_some()
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

fn failure_diagnostics(title: &str, error: &str, log_dir: &Path) -> RuntimeDiagnostics {
    RuntimeDiagnostics {
        ready: false,
        title: title.to_string(),
        summary: "A bundled service failed during startup.".to_string(),
        checks: vec![RuntimeCheck {
            name: "Bundled services".to_string(),
            ok: false,
            detail: error.to_string(),
            action: "Open the log folder below and check backend.err.log, reranker.err.log, and nli.err.log.".to_string(),
        }],
        logs_dir: log_dir.display().to_string(),
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

fn start_service(
    install_dir: &Path,
    log_dir: &Path,
    name: &str,
    config: &ServiceConfig,
    state: &RuntimeState,
) -> Result<()> {
    if config.enabled == Some(false) || health_ok(&config.health_url) {
        return Ok(());
    }

    let exe_path = install_dir.join(&config.executable);
    let stdout = fs::File::create(log_dir.join(format!("{name}.out.log")))?;
    let stderr = fs::File::create(log_dir.join(format!("{name}.err.log")))?;
    let child = Command::new(&exe_path)
        .current_dir(install_dir)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .with_context(|| format!("failed to start {}", exe_path.display()))?;
    state
        .children
        .lock()
        .expect("runtime lock poisoned")
        .push(child);
    Ok(())
}

fn wait_for_health(name: &str, url: &str, timeout: Duration) -> Result<()> {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if health_ok(url) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(750));
    }
    Err(anyhow!("{name} did not become healthy at {url}"))
}

fn health_ok(url: &str) -> bool {
    ureq::get(url)
        .timeout(Duration::from_secs(2))
        .call()
        .map(|response| response.status() < 500)
        .unwrap_or(false)
}

fn stop_children(state: &RuntimeState) {
    let mut children = state.children.lock().expect("runtime lock poisoned");
    for child in children.iter_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    children.clear();
}

use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use anyhow::{anyhow, Context, Result};
use serde::Deserialize;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

#[derive(Debug, Deserialize)]
struct DesktopConfig {
    backend: ServiceConfig,
    grounding: GroundingConfig,
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

struct RuntimeState {
    children: Mutex<Vec<Child>>,
}

fn main() {
    tauri::Builder::default()
        .manage(RuntimeState {
            children: Mutex::new(Vec::new()),
        })
        .setup(|app| {
            let install_dir = install_dir()?;
            let config = load_config(&install_dir)?;
            let log_dir = log_dir()?;
            fs::create_dir_all(&log_dir)?;

            let state = app.state::<RuntimeState>();
            start_service(&install_dir, &log_dir, "backend", &config.backend, &state)?;

            if config.grounding.enabled {
                start_service(
                    &install_dir,
                    &log_dir,
                    "reranker",
                    &config.grounding.reranker,
                    &state,
                )?;
                start_service(&install_dir, &log_dir, "nli", &config.grounding.nli, &state)?;
            }

            wait_for_health(
                "backend",
                &config.backend.health_url,
                Duration::from_secs(90),
            )?;
            if config.grounding.enabled {
                wait_for_health(
                    "reranker",
                    &config.grounding.reranker.health_url,
                    Duration::from_secs(120),
                )?;
                wait_for_health(
                    "nli",
                    &config.grounding.nli.health_url,
                    Duration::from_secs(120),
                )?;
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

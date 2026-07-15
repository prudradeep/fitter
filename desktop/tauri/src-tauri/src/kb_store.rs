use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

const STORE_VERSION: u32 = 1;
const PERSISTENT_SCOPES: [&str; 3] = ["main", "sector_prompt", "validated_evidence"];
const TEMPORARY_SCOPE: &str = "temporary";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct KnowledgeDocument {
    pub id: String,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub source_uri: Option<String>,
    #[serde(default)]
    pub source_type: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
    #[serde(default)]
    pub metadata: BTreeMap<String, Value>,
    #[serde(default)]
    pub chunks: Vec<KnowledgeChunk>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct KnowledgeChunk {
    pub id: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub source_ref: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    #[serde(default)]
    pub embedding: Option<Vec<f32>>,
    #[serde(default)]
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ScopeManifest {
    pub store_version: u32,
    pub scope: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub sync_cursor: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    #[serde(default)]
    pub document_checksums: BTreeMap<String, String>,
    #[serde(default)]
    pub tombstones: BTreeMap<String, Tombstone>,
    #[serde(default)]
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Tombstone {
    pub document_id: String,
    #[serde(default)]
    pub checksum: Option<String>,
    pub deleted_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ScopeSummary {
    pub scope: String,
    #[serde(default)]
    pub session_id: Option<String>,
    pub document_count: usize,
    pub tombstone_count: usize,
    #[serde(default)]
    pub sync_cursor: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct StoreStatus {
    pub base_dir: String,
    pub persistent_scopes: Vec<ScopeSummary>,
    pub temporary_sessions: Vec<ScopeSummary>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct UpsertBatch {
    pub scope: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub sync_cursor: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    #[serde(default)]
    pub documents: Vec<KnowledgeDocument>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DeleteBatch {
    pub scope: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub sync_cursor: Option<String>,
    #[serde(default)]
    pub checksum: Option<String>,
    #[serde(default)]
    pub document_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct BatchResult {
    pub scope: String,
    #[serde(default)]
    pub session_id: Option<String>,
    pub document_count: usize,
    pub tombstone_count: usize,
}

pub struct KnowledgeStore {
    root: PathBuf,
}

impl KnowledgeStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn init(&self) -> Result<()> {
        fs::create_dir_all(&self.root)
            .with_context(|| format!("failed to create {}", self.root.display()))?;
        for scope in PERSISTENT_SCOPES {
            let path = self.scope_path(scope, None)?;
            fs::create_dir_all(path.join("documents"))?;
            let manifest_path = path.join("manifest.json");
            if !manifest_path.exists() {
                self.write_manifest(&path, &ScopeManifest::new(scope, None))?;
            }
        }
        fs::create_dir_all(self.root.join(TEMPORARY_SCOPE))?;
        Ok(())
    }

    pub fn status(&self) -> Result<StoreStatus> {
        self.init()?;
        let mut persistent_scopes = Vec::new();
        for scope in PERSISTENT_SCOPES {
            persistent_scopes.push(self.summary(scope, None)?);
        }

        let mut temporary_sessions = Vec::new();
        let temp_root = self.root.join(TEMPORARY_SCOPE);
        if temp_root.exists() {
            for entry in fs::read_dir(&temp_root)? {
                let entry = entry?;
                if !entry.file_type()?.is_dir() {
                    continue;
                }
                let session_id = entry.file_name().to_string_lossy().to_string();
                temporary_sessions.push(self.summary(TEMPORARY_SCOPE, Some(&session_id))?);
            }
        }

        Ok(StoreStatus {
            base_dir: self.root.display().to_string(),
            persistent_scopes,
            temporary_sessions,
        })
    }

    pub fn manifest(&self, scope: &str, session_id: Option<&str>) -> Result<ScopeManifest> {
        let path = self.scope_path(scope, session_id)?;
        self.read_or_create_manifest(scope, session_id, &path)
    }

    pub fn list_documents(
        &self,
        scope: &str,
        session_id: Option<&str>,
    ) -> Result<Vec<KnowledgeDocument>> {
        let path = self.scope_path(scope, session_id)?;
        let documents_path = path.join("documents");
        if !documents_path.exists() {
            return Ok(Vec::new());
        }

        let mut documents = Vec::new();
        for entry in fs::read_dir(documents_path)? {
            let entry = entry?;
            if !entry.file_type()?.is_file() {
                continue;
            }
            let raw = fs::read_to_string(entry.path())?;
            documents.push(serde_json::from_str(&raw)?);
        }
        documents.sort_by(|left: &KnowledgeDocument, right| left.id.cmp(&right.id));
        Ok(documents)
    }

    pub fn get_document(
        &self,
        scope: &str,
        session_id: Option<&str>,
        document_id: &str,
    ) -> Result<Option<KnowledgeDocument>> {
        let path = self.document_path(scope, session_id, document_id)?;
        if !path.exists() {
            return Ok(None);
        }
        let raw = fs::read_to_string(path)?;
        Ok(Some(serde_json::from_str(&raw)?))
    }

    pub fn upsert_documents(&self, batch: UpsertBatch) -> Result<BatchResult> {
        let scope = validate_scope(&batch.scope)?;
        let session_id = validate_session(scope, batch.session_id.as_deref())?;
        let path = self.scope_path(scope, session_id)?;
        fs::create_dir_all(path.join("documents"))?;
        let mut manifest = self.read_or_create_manifest(scope, session_id, &path)?;

        for document in batch.documents {
            let document_id = validate_id("document id", &document.id)?;
            let checksum = document
                .checksum
                .clone()
                .unwrap_or_else(|| fallback_document_checksum(&document));
            let document_path = path
                .join("documents")
                .join(format!("{}.json", file_key(document_id)));
            write_json(&document_path, &document)?;
            manifest
                .document_checksums
                .insert(document_id.to_string(), checksum);
            manifest.tombstones.remove(document_id);
        }

        update_manifest_sync(&mut manifest, batch.sync_cursor, batch.checksum);
        self.write_manifest(&path, &manifest)?;
        Ok(self.batch_result(scope, session_id)?)
    }

    pub fn delete_documents(&self, batch: DeleteBatch) -> Result<BatchResult> {
        let scope = validate_scope(&batch.scope)?;
        let session_id = validate_session(scope, batch.session_id.as_deref())?;
        let path = self.scope_path(scope, session_id)?;
        fs::create_dir_all(path.join("documents"))?;
        let mut manifest = self.read_or_create_manifest(scope, session_id, &path)?;

        for id in batch.document_ids {
            let document_id = validate_id("document id", &id)?;
            let document_path = path
                .join("documents")
                .join(format!("{}.json", file_key(document_id)));
            if document_path.exists() {
                fs::remove_file(document_path)?;
            }
            let old_checksum = manifest.document_checksums.remove(document_id);
            manifest.tombstones.insert(
                document_id.to_string(),
                Tombstone {
                    document_id: document_id.to_string(),
                    checksum: old_checksum,
                    deleted_at: now_text(),
                },
            );
        }

        update_manifest_sync(&mut manifest, batch.sync_cursor, batch.checksum);
        self.write_manifest(&path, &manifest)?;
        Ok(self.batch_result(scope, session_id)?)
    }

    pub fn clear_temporary(&self, session_id: &str) -> Result<()> {
        let session_id = validate_id("session id", session_id)?;
        let path = self.root.join(TEMPORARY_SCOPE).join(file_key(session_id));
        if path.exists() {
            fs::remove_dir_all(path)?;
        }
        Ok(())
    }

    fn batch_result(&self, scope: &str, session_id: Option<&str>) -> Result<BatchResult> {
        let summary = self.summary(scope, session_id)?;
        Ok(BatchResult {
            scope: scope.to_string(),
            session_id: session_id.map(ToString::to_string),
            document_count: summary.document_count,
            tombstone_count: summary.tombstone_count,
        })
    }

    fn summary(&self, scope: &str, session_id: Option<&str>) -> Result<ScopeSummary> {
        let path = self.scope_path(scope, session_id)?;
        let manifest = self.read_or_create_manifest(scope, session_id, &path)?;
        Ok(ScopeSummary {
            scope: scope.to_string(),
            session_id: session_id.map(ToString::to_string),
            document_count: manifest.document_checksums.len(),
            tombstone_count: manifest.tombstones.len(),
            sync_cursor: manifest.sync_cursor,
            checksum: manifest.checksum,
            path: path.display().to_string(),
        })
    }

    fn scope_path(&self, scope: &str, session_id: Option<&str>) -> Result<PathBuf> {
        let scope = validate_scope(scope)?;
        let path = if scope == TEMPORARY_SCOPE {
            let session_id = validate_session(scope, session_id)?;
            self.root.join(TEMPORARY_SCOPE).join(file_key(
                session_id.expect("temporary session id should exist"),
            ))
        } else {
            self.root.join(scope)
        };
        Ok(path)
    }

    fn document_path(
        &self,
        scope: &str,
        session_id: Option<&str>,
        document_id: &str,
    ) -> Result<PathBuf> {
        let document_id = validate_id("document id", document_id)?;
        Ok(self
            .scope_path(scope, session_id)?
            .join("documents")
            .join(format!("{}.json", file_key(document_id))))
    }

    fn read_or_create_manifest(
        &self,
        scope: &str,
        session_id: Option<&str>,
        path: &Path,
    ) -> Result<ScopeManifest> {
        fs::create_dir_all(path.join("documents"))?;
        let manifest_path = path.join("manifest.json");
        if manifest_path.exists() {
            let raw = fs::read_to_string(&manifest_path)?;
            return Ok(serde_json::from_str(&raw)?);
        }
        let manifest = ScopeManifest::new(scope, session_id);
        self.write_manifest(path, &manifest)?;
        Ok(manifest)
    }

    fn write_manifest(&self, path: &Path, manifest: &ScopeManifest) -> Result<()> {
        write_json(&path.join("manifest.json"), manifest)
    }
}

impl ScopeManifest {
    fn new(scope: &str, session_id: Option<&str>) -> Self {
        Self {
            store_version: STORE_VERSION,
            scope: scope.to_string(),
            session_id: session_id.map(ToString::to_string),
            sync_cursor: None,
            checksum: None,
            document_checksums: BTreeMap::new(),
            tombstones: BTreeMap::new(),
            updated_at: Some(now_text()),
        }
    }
}

fn update_manifest_sync(
    manifest: &mut ScopeManifest,
    sync_cursor: Option<String>,
    checksum: Option<String>,
) {
    if sync_cursor.is_some() {
        manifest.sync_cursor = sync_cursor;
    }
    if checksum.is_some() {
        manifest.checksum = checksum;
    }
    manifest.updated_at = Some(now_text());
}

fn validate_scope(scope: &str) -> Result<&str> {
    let scope = scope.trim();
    if PERSISTENT_SCOPES.contains(&scope) || scope == TEMPORARY_SCOPE {
        Ok(scope)
    } else {
        Err(anyhow!("unsupported knowledge scope: {scope}"))
    }
}

fn validate_session<'a>(scope: &str, session_id: Option<&'a str>) -> Result<Option<&'a str>> {
    if scope == TEMPORARY_SCOPE {
        let session_id =
            session_id.ok_or_else(|| anyhow!("temporary knowledge requires a session id"))?;
        return Ok(Some(validate_id("session id", session_id)?));
    }
    if session_id.is_some() {
        return Err(anyhow!("session id is only valid for temporary knowledge"));
    }
    Ok(None)
}

fn validate_id<'a>(label: &str, value: &'a str) -> Result<&'a str> {
    let value = value.trim();
    if value.is_empty() {
        return Err(anyhow!("{label} is required"));
    }
    if value.len() > 256 {
        return Err(anyhow!("{label} is too long"));
    }
    Ok(value)
}

fn file_key(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len() * 2);
    for byte in value.bytes() {
        encoded.push_str(&format!("{byte:02x}"));
    }
    encoded
}

fn fallback_document_checksum(document: &KnowledgeDocument) -> String {
    format!(
        "chunks:{}:title:{}",
        document.chunks.len(),
        document.title.as_deref().unwrap_or("")
    )
}

fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let raw = serde_json::to_string_pretty(value)?;
    fs::write(path, raw).with_context(|| format!("failed to write {}", path.display()))
}

fn now_text() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string())
}

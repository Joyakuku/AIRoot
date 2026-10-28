use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

pub const API_VERSION: u32 = 1;
pub fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
pub fn id() -> String {
    uuid::Uuid::new_v4().to_string()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Resource {
    pub id: String,
    pub class: String,
    pub kind: String,
    pub name: String,
    pub path: String,
    pub entry: Option<String>,
    pub version: Option<String>,
    pub parent_id: Option<String>,
    pub scope: String,
    pub status: String,
    pub projects: Vec<String>,
    pub dependencies: Value,
    pub metadata: Value,
    pub updated_at: String,
}
impl Resource {
    pub fn new(class: &str, kind: &str, name: &str, path: &str) -> Self {
        Self {
            id: id(),
            class: class.into(),
            kind: kind.into(),
            name: name.into(),
            path: path.into(),
            entry: None,
            version: None,
            parent_id: None,
            scope: "host".into(),
            status: "unverified".into(),
            projects: vec![],
            dependencies: serde_json::json!([]),
            metadata: serde_json::json!({}),
            updated_at: now(),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub env_root: Option<PathBuf>,
    pub tools_root: Option<PathBuf>,
    pub component_dir: PathBuf,
    pub find_timeout_seconds: u64,
    pub backup_count: usize,
    pub log_days: u64,
    pub log_limit_bytes: u64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            env_root: None,
            tools_root: None,
            component_dir: std::env::current_exe()
                .unwrap_or_default()
                .parent()
                .unwrap_or(std::path::Path::new("."))
                .join("components"),
            find_timeout_seconds: 30,
            backup_count: 10,
            log_days: 30,
            log_limit_bytes: 200 * 1024 * 1024,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Request {
    pub command: String,
    #[serde(default)]
    pub args: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub api_version: u32,
    pub ok: bool,
    pub data: Value,
    pub error: Option<String>,
}
impl Response {
    pub fn success(data: Value) -> Self {
        Self {
            api_version: API_VERSION,
            ok: true,
            data,
            error: None,
        }
    }
    pub fn failure(e: impl ToString) -> Self {
        Self {
            api_version: API_VERSION,
            ok: false,
            data: Value::Null,
            error: Some(e.to_string()),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlanInput {
    pub action: String,
    pub resource_id: Option<String>,
    pub class: Option<String>,
    pub kind: Option<String>,
    pub name: Option<String>,
    pub target: Option<PathBuf>,
    pub project: Option<String>,
    pub version: Option<String>,
    pub manager: Option<PathBuf>,
    #[serde(default)]
    pub packages: Vec<String>,
    pub source: Option<String>,
    pub archive: Option<PathBuf>,
    pub sha256: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Step {
    #[serde(default)]
    pub environment: std::collections::BTreeMap<String, String>,
    pub program: String,
    pub args: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Operation {
    pub id: String,
    pub input: PlanInput,
    pub resource: Resource,
    pub fingerprint: Option<String>,
    pub risk: String,
    pub requirements: Vec<String>,
    pub effects: Vec<String>,
    pub steps: Vec<Step>,
    pub status: String,
    pub execution_status: String,
    #[serde(default = "not_started")]
    pub verification_status: String,
    pub registration_status: String,
    pub created_at: String,
    pub updated_at: String,
    pub error: Option<String>,
    pub attempts: u32,
    #[serde(default)]
    pub reliability: Reliability,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Reliability {
    pub lease_id: Option<String>,
    #[serde(default)]
    pub target_identity: Option<String>,
    pub owned: Vec<OwnedPath>,
    pub failure_confirmed: bool,
    pub recovery: WorkState,
    pub cleanup: WorkState,
    #[serde(default)]
    pub finalization: WorkState,
    pub vendor: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OwnedPath {
    pub path: PathBuf,
    pub identity: String,
    pub purpose: String,
    pub removed: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkState {
    pub status: String,
    pub reason: Option<String>,
    pub completed_steps: Vec<String>,
    pub attempts: u32,
}
impl Default for WorkState {
    fn default() -> Self {
        Self {
            status: "not_started".into(),
            reason: None,
            completed_steps: vec![],
            attempts: 0,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Scan {
    pub id: String,
    pub status: String,
    pub roots: Vec<PathBuf>,
    pub visited: u64,
    pub discovered: u64,
    pub skipped: Vec<Value>,
    pub providers: Vec<Value>,
    #[serde(default)]
    pub coverage: Value,
    pub created_at: String,
    pub updated_at: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Lease {
    pub id: String,
    #[serde(default)]
    pub operation_id: Option<String>,
    pub resource_ids: Vec<String>,
    pub mode: String,
    pub pid: Option<u32>,
    pub process_stamp: Option<u64>,
    #[serde(default)]
    pub process_job: Option<String>,
    pub project: Option<String>,
    pub state: String,
    pub created_at: String,
}

fn not_started() -> String {
    "not_started".into()
}

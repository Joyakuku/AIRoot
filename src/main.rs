use airoot::{ipc, model::Request};
use anyhow::Result;
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use std::path::PathBuf;
#[derive(Parser)]
#[command(
    version,
    about = "AIRoot: local environment and tool registry. Commands report risk; your Agent handles authorization."
)]
struct Cli {
    #[arg(long, global = true)]
    json: bool,
    #[arg(long, global = true, hide = true)]
    data_root: Option<PathBuf>,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    #[command(hide = true)]
    Daemon,
    Status,
    Capabilities,
    WorkerStop,
    PlatformInspect {
        id: String,
        #[arg(long)]
        path: String,
    },
    PlatformDiscover {
        #[arg(value_parser=["wsl","docker"])]
        kind: String,
    },
    Components {
        #[command(subcommand)]
        command: Components,
    },
    Config {
        #[command(subcommand)]
        command: ConfigCommand,
    },
    Env {
        #[command(subcommand)]
        command: Query,
    },
    Tool {
        #[command(subcommand)]
        command: Query,
    },
    Project {
        #[command(subcommand)]
        command: Project,
    },
    Registry {
        #[command(subcommand)]
        command: Registry,
    },
    Scan {
        #[command(subcommand)]
        command: Scan,
    },
    Operation {
        #[command(subcommand)]
        command: Operation,
    },
    Resource {
        #[command(subcommand)]
        command: Resource,
    },
    Usage {
        #[command(subcommand)]
        command: Usage,
    },
    Log {
        #[command(subcommand)]
        command: Log,
    },
}
#[derive(Subcommand)]
enum Components {
    Status,
    ServicePlan,
    Start {
        #[arg(long = "root", required = true)]
        roots: Vec<PathBuf>,
    },
    Stop,
}
#[derive(Subcommand)]
enum Query {
    List {
        #[arg(long)]
        project: Option<String>,
    },
    Show {
        id: String,
    },
}
#[derive(Subcommand)]
enum Project {
    Show { project: String },
}
#[derive(Subcommand)]
enum ConfigCommand {
    Show,
    Set {
        #[arg(long)]
        env_root: Option<PathBuf>,
        #[arg(long)]
        tools_root: Option<PathBuf>,
        #[arg(
            long,
            help = "Agent assertion that initializing missing roots was explicitly confirmed"
        )]
        initialize: bool,
    },
}
#[derive(Subcommand)]
enum Registry {
    Add {
        #[arg(long)]
        path: Option<PathBuf>,
        #[arg(long)]
        file: Option<PathBuf>,
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        parent_id: Option<String>,
    },
    Verify {
        id: String,
    },
    Ignore {
        id: String,
    },
    Restore {
        id: String,
    },
    Backup,
}
#[derive(Subcommand)]
enum Scan {
    Start {
        #[arg(long = "root")]
        roots: Vec<PathBuf>,
    },
    Status {
        id: Option<String>,
    },
    Pause {
        id: String,
    },
    Resume {
        id: String,
    },
}
#[derive(Subcommand)]
enum Operation {
    Recover {
        id: String,
    },
    Cleanup {
        id: String,
    },
    Plan {
        #[arg(long)]
        file: PathBuf,
    },
    Apply {
        id: String,
        #[arg(long = "confirm")]
        confirmations: Vec<String>,
    },
    Show {
        id: Option<String>,
    },
}
#[derive(Subcommand)]
enum Resource {
    Find {
        query: String,
        #[arg(long = "root")]
        roots: Vec<PathBuf>,
    },
    Run {
        id: String,
        #[arg(long)]
        project: Option<String>,
        #[arg(last = true)]
        args: Vec<String>,
    },
}
#[derive(Subcommand)]
enum Usage {
    List,
    Acquire {
        id: String,
        #[arg(long)]
        pid: u32,
        #[arg(long)]
        project: Option<String>,
    },
    Release {
        id: String,
        #[arg(
            long,
            help = "Agent assertion that related descendant processes have been checked"
        )]
        descendants_checked: bool,
    },
}
#[derive(Subcommand)]
enum Log {
    Show { id: String },
}
fn query(prefix: &str, c: Query) -> (String, Value) {
    match c {
        Query::List { project } => (format!("{prefix}.list"), json!({"project":project})),
        Query::Show { id } => (format!("{prefix}.show"), json!({"id":id})),
    }
}
fn request(command: Command) -> Result<Request> {
    let (command, args) = match command {
        Command::Components { command } => match command {
            Components::Status => ("components.status".into(), json!({})),
            Components::ServicePlan => ("components.service-plan".into(), json!({})),
            Components::Start { roots } => ("components.start".into(), json!({"roots":roots})),
            Components::Stop => ("components.stop".into(), json!({})),
        },
        Command::PlatformInspect { id, path } => {
            ("platform.inspect".into(), json!({"id":id,"path":path}))
        }
        Command::PlatformDiscover { kind } => ("platform.discover".into(), json!({"kind":kind})),
        Command::WorkerStop => ("worker.stop".into(), json!({})),
        Command::Status => ("status".into(), json!({})),
        Command::Capabilities => ("capabilities".into(), json!({})),
        Command::Env { command } => query("env", command),
        Command::Tool { command } => query("tool", command),
        Command::Project {
            command: Project::Show { project },
        } => ("project.show".into(), json!({"project":project})),
        Command::Config { command } => match command {
            ConfigCommand::Show => ("config.show".into(), json!({})),
            ConfigCommand::Set {
                env_root,
                tools_root,
                initialize,
            } => (
                "config.set".into(),
                json!({"env_root":env_root,"tools_root":tools_root,"initialize":initialize}),
            ),
        },
        Command::Registry { command } => match command {
            Registry::Add {
                path,
                file,
                project,
                parent_id,
            } => (
                "registry.add".into(),
                json!({"path":path,"resource":file.map(|p|airoot::store::read_json::<Value>(&p)).transpose()?,"project":project,"parent_id":parent_id}),
            ),
            Registry::Verify { id } => ("registry.verify".into(), json!({"id":id})),
            Registry::Ignore { id } => ("registry.ignore".into(), json!({"id":id})),
            Registry::Restore { id } => ("registry.restore".into(), json!({"id":id})),
            Registry::Backup => ("registry.backup".into(), json!({})),
        },
        Command::Scan { command } => match command {
            Scan::Start { roots } => ("scan.start".into(), json!({"roots":roots})),
            Scan::Status { id } => ("scan.status".into(), json!({"id":id})),
            Scan::Pause { id } => ("scan.pause".into(), json!({"id":id})),
            Scan::Resume { id } => ("scan.resume".into(), json!({"id":id})),
        },
        Command::Operation { command } => match command {
            Operation::Recover { id } => ("operation.recover".into(), json!({"id":id})),
            Operation::Cleanup { id } => ("operation.cleanup".into(), json!({"id":id})),
            Operation::Plan { file } => ("operation.plan".into(), airoot::store::read_json(&file)?),
            Operation::Apply { id, confirmations } => (
                "operation.apply".into(),
                json!({"id":id,"confirmations":confirmations}),
            ),
            Operation::Show { id } => ("operation.show".into(), json!({"id":id})),
        },
        Command::Resource { command } => match command {
            Resource::Find { query, roots } => {
                ("resource.find".into(), json!({"query":query,"roots":roots}))
            }
            Resource::Run { id, project, args } => (
                "resource.run".into(),
                json!({"id":id,"project":project,"args":args}),
            ),
        },
        Command::Usage { command } => match command {
            Usage::List => ("usage.list".into(), json!({})),
            Usage::Acquire { id, pid, project } => (
                "usage.acquire".into(),
                json!({"id":id,"pid":pid,"project":project}),
            ),
            Usage::Release {
                id,
                descendants_checked,
            } => (
                "usage.release".into(),
                json!({"id":id,"descendants_checked":descendants_checked}),
            ),
        },
        Command::Log {
            command: Log::Show { id },
        } => ("log.show".into(), json!({"id":id})),
        Command::Daemon => unreachable!(),
    };
    Ok(Request { command, args })
}
fn text(v: &Value) -> String {
    if let Some(rows) = v.as_array() {
        if rows.iter().all(|r| r["class"].is_string()) {
            if rows.is_empty() {
                return "No registered resources.".into();
            }
            return rows
                .iter()
                .map(|r| {
                    format!(
                        "{}  {}  {}  {}\n  {}",
                        r["id"].as_str().unwrap_or(""),
                        r["kind"].as_str().unwrap_or(""),
                        r["name"].as_str().unwrap_or(""),
                        r["status"].as_str().unwrap_or(""),
                        r["path"].as_str().unwrap_or("")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
        }
    }
    serde_json::to_string_pretty(v).unwrap_or_default()
}
#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    let result = async {
        let root = cli.data_root.unwrap_or(ipc::data_root()?);
        if matches!(cli.command, Command::Daemon) {
            return ipc::daemon(root).await;
        }
        let response = ipc::request(&root, request(cli.command)?).await?;
        if cli.json {
            println!("{}", serde_json::to_string(&response)?);
        } else if response.ok {
            println!("{}", text(&response.data));
        } else {
            eprintln!("{}", response.error.as_deref().unwrap_or("Unknown error"));
        }
        if !response.ok {
            std::process::exit(1);
        }
        Ok::<(), anyhow::Error>(())
    }
    .await;
    if let Err(e) = result {
        if cli.json {
            println!(
                "{}",
                serde_json::to_string(&airoot::model::Response::failure(format!("{e:#}"))).unwrap()
            );
        } else {
            eprintln!("{e:#}");
        }
        std::process::exit(1);
    }
}

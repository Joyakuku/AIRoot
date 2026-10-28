use crate::{model::*, platform, store::Store};
use anyhow::{Context, Result};
use serde_json::json;
use std::{
    fs, io,
    path::{Path, PathBuf},
};
pub fn prepare(s: &Store, mut input: PlanInput) -> Result<Operation> {
    let action = input.action.as_str();
    anyhow::ensure!(action == "install-portable", "Unsupported portable action");
    let kind = input.kind.as_deref().context("kind required")?;
    let class = if matches!(kind, "node" | "java" | "python" | "conda") {
        "env"
    } else if kind == "cli" {
        "tool"
    } else {
        anyhow::bail!("Portable recipe supports node/java/python/cli only")
    };
    let root = if class == "env" {
        s.config.env_root.as_ref()
    } else {
        s.config.tools_root.as_ref()
    }
    .context("Configure and confirm normal resource root before installation")?;
    let name = input.name.as_deref().context("name required")?;
    anyhow::ensure!(
        !name.is_empty()
            && name
                .chars()
                .all(|c| c.is_alphanumeric() || matches!(c, '-' | '_')),
        "Invalid installation name"
    );
    let target = platform::checked_target(
        &input.target.clone().unwrap_or_else(|| root.join(name)),
        root,
    )?;
    anyhow::ensure!(
        !target.exists(),
        "Install a new parallel version; target already exists"
    );
    let expected = input
        .sha256
        .as_deref()
        .context("Pinned archive SHA256 required")?
        .to_owned();
    anyhow::ensure!(
        expected.len() == 64 && expected.chars().all(|c| c.is_ascii_hexdigit()),
        "SHA256 must be 64 hexadecimal digits"
    );
    let source_url = input
        .source
        .as_ref()
        .context("Provenance source URL required")?
        .clone();
    anyhow::ensure!(
        source_url.starts_with("https://") && !source_url.contains(['@', '?', '#', '\r', '\n']),
        "Use a public HTTPS provenance URL without credentials or query strings"
    );
    let mut steps = vec![];
    let source = if let Some(archive) = &input.archive {
        let archive = platform::canonical(archive)?;
        anyhow::ensure!(
            !archive.starts_with(platform::canonical(&s.root)?),
            "Installation materials cannot live in the AIRoot information directory"
        );
        anyhow::ensure!(
            platform::hash_file(&archive)?.eq_ignore_ascii_case(&expected),
            "Archive checksum mismatch"
        );
        archive
    } else {
        let curl = platform::executable("curl").context("DOWNLOAD_PREREQUISITE_REQUIRED: curl unavailable; supply an already downloaded archive and SHA256")?;
        let archive = root
            .join(".cache")
            .join("downloads")
            .join(format!("{}.zip", id()));
        let step = Step {
            program: curl.to_string_lossy().into(),
            args: vec![
                "--fail".into(),
                "--location".into(),
                "--proto".into(),
                "=https".into(),
                "--proto-redir".into(),
                "=https".into(),
                "--silent".into(),
                "--show-error".into(),
                "--connect-timeout".into(),
                "30".into(),
                "--max-time".into(),
                "3300".into(),
                "--retry".into(),
                "2".into(),
                "--output".into(),
                archive.to_string_lossy().into(),
                source_url.clone(),
            ],
            environment: Default::default(),
        };
        steps.push(step);
        archive
    };
    input.archive = Some(source.clone());
    let mut r = Resource::new(class, kind, name, &target.to_string_lossy());
    r.version = input.version.clone();
    if let Some(project) = &input.project {
        r.projects.push(project.clone());
    }
    if class == "env" {
        anyhow::ensure!(
            !r.projects.is_empty(),
            "New environments require project association"
        );
    }
    r.metadata = json!({"recipe":"portable-zip","source":source_url,"archive":source,"sha256":expected,"created_by_airoot":true,"download_owned":!steps.is_empty(),"download_bytes":"unknown","download_seconds":"unknown"});
    let download_effects = steps
        .iter()
        .map(|step| {
            Ok(format!(
                "Executable SHA256 {} {}",
                step.program,
                platform::hash_file(Path::new(&step.program))?
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    let mut op=Operation{id:id(),input,resource:r,fingerprint:None,risk:"low".into(),requirements:vec!["source-reviewed".into()],effects:vec![format!("Extract pinned archive {} into {}",source.display(),target.display()),"No global PATH or file associations changed; archive remains at its declared original location".into()],steps,status:"planned".into(),execution_status:"not_started".into(),verification_status:"not_started".into(),registration_status:"not_started".into(),created_at:now(),updated_at:now(),error:None,attempts:0,reliability:Default::default()};
    op.effects.extend(download_effects);
    if op.resource.metadata["download_owned"] == true {
        op.effects.push(format!("Download from {source_url} to {}; size and duration unknown; transient HTTP failures retry at most twice; verify SHA256 before extraction", source.display()));
    }
    Ok(op)
}
pub fn extract(archive: &Path, target: &Path, sha: &str) -> Result<()> {
    anyhow::ensure!(
        platform::hash_file(archive)?.eq_ignore_ascii_case(sha),
        "PLAN_CHANGED: archive hash mismatch"
    );
    let mut zip = zip::ZipArchive::new(fs::File::open(archive)?)?;
    anyhow::ensure!(zip.len() <= 100000, "Archive exceeds recipe entry limit");
    // Validate the entire archive before creating any file, including Windows separators and aliases.
    let mut seen = std::collections::HashSet::new();
    let mut total = 0u64;
    for i in 0..zip.len() {
        let f = zip.by_index(i)?;
        let p = f
            .enclosed_name()
            .context("Archive path traversal rejected")?;
        anyhow::ensure!(
            !f.name().contains(['\\', ':']) && !p.is_absolute(),
            "Unsupported or absolute archive path"
        );
        for part in p.components() {
            let text = part.as_os_str().to_string_lossy();
            let upper = text.split('.').next().unwrap_or("").to_uppercase();
            anyhow::ensure!(
                !text.ends_with([' ', '.'])
                    && !matches!(upper.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                    && !((upper.starts_with("COM") || upper.starts_with("LPT"))
                        && upper.len() == 4
                        && upper.as_bytes()[3].is_ascii_digit()),
                "Windows path alias rejected"
            );
        }
        anyhow::ensure!(
            seen.insert(p.to_string_lossy().to_lowercase()),
            "Duplicate case-insensitive archive paths rejected"
        );
        anyhow::ensure!(
            f.unix_mode().is_none_or(|m| m & 0o170000 != 0o120000),
            "Archive symlinks rejected"
        );
        total = total
            .checked_add(f.size())
            .context("Archive size overflow")?;
        anyhow::ensure!(
            total <= 20 * 1024 * 1024 * 1024,
            "Archive exceeds 20 GiB extraction limit"
        );
    }
    for i in 0..zip.len() {
        let mut f = zip.by_index(i)?;
        let dest = target.join(f.enclosed_name().context("Invalid archive member")?);
        if f.is_dir() {
            fs::create_dir_all(&dest)?;
        } else {
            fs::create_dir_all(dest.parent().context("Missing parent")?)?;
            let mut out = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(dest)?;
            io::copy(&mut f, &mut out)?;
        }
    }
    Ok(())
}
pub fn identify(mut r: Resource) -> Result<Resource> {
    let mut queue = std::collections::VecDeque::from([(PathBuf::from(&r.path), 0)]);
    let mut candidates = vec![];
    while let Some((p, depth)) = queue.pop_front() {
        for found in crate::adapters::inspect(&p)? {
            if found.class == r.class
                && found.kind == r.kind
                && (r.kind != "cli" || found.name == r.name)
            {
                candidates.push(found);
            }
        }
        if depth < 3 {
            for e in fs::read_dir(p)?.flatten() {
                if e.file_type()?.is_dir() {
                    queue.push_back((e.path(), depth + 1));
                }
            }
        }
    }
    if candidates.is_empty() {
        anyhow::bail!(
            "BROKEN_INSTALLATION: no supported executable entry in extracted installation"
        );
    }
    anyhow::ensure!(
        candidates.len() == 1,
        "Installation preserved, but entry is ambiguous or unsupported; explicitly register and verify after inspection"
    );
    let detected = candidates.remove(0);
    r.entry = detected.entry;
    r.version = detected.version.or(r.version);
    Ok(r)
}

pub fn plan(s: &Store, input: PlanInput) -> Result<Operation> {
    let op = prepare(s, input)?;
    s.save("operations", &op.id, &op)?;
    Ok(op)
}

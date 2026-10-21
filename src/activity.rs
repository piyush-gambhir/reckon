use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
use std::io::Write;
pub fn pinned() -> bool {
    std::env::var_os("RECKON_ORG").is_some() || std::env::var_os("RECKON_ENV").is_some()
}
pub fn words(ctx: &Context, target: &str, args: &[String], registry: &Registry) -> Vec<String> {
    let Ok((_, conn)) = orgs::resolve(ctx, target) else {
        return vec![];
    };
    let spec = &registry[text(&conn["provider"])];
    let words = connectors::tokens(args, spec);
    let n = if text(&spec["mode"]) == "own" {
        2
    } else {
        array(&spec["read_commands"])
            .iter()
            .chain(array(&spec["deny_commands"]))
            .filter(|p| connectors::matches(&strings(p), &words))
            .map(|p| array(p).len())
            .max()
            .unwrap_or(0)
    };
    words
        .into_iter()
        .take(n)
        .map(|w| w.chars().take(32).collect())
        .collect()
}
pub fn record(h: &Path, ctx: &Context, event: &str, fields: Value) -> Value {
    let mut entry = json!({"at":now(),"org":ctx.org,"environment":ctx.env,"actor":if pinned(){"agent"}else{"operator"},"event":event});
    for (k, v) in object(&fields) {
        if !v.is_null() {
            if k == "args" {
                entry["args_sha256"] = json!(hash_json(&v));
            } else if k == "detail" {
                entry[k] = json!(
                    orgs::redact(text(&v), &crate::process::Environment::new())
                        .chars()
                        .take(300)
                        .collect::<String>()
                );
            } else {
                entry[k] = v;
            }
        }
    }
    let _ = (|| -> Result<()> {
        let path = local_path(&orgs::org_path(h, &ctx.org)?, "activity.jsonl")?;
        private_dir(path.parent().ok_or_else(|| anyhow!("missing parent"))?)?;
        let mut opts = std::fs::OpenOptions::new();
        opts.append(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let mut file = opts.open(path)?;
        ensure!(
            file.metadata()?.is_file(),
            "activity log must be a regular file"
        );
        file.write_all(format!("{}\n", entry).as_bytes())?;
        Ok(())
    })();
    entry
}
pub fn read(
    h: &Path,
    org: &str,
    days: i64,
    env: Option<&str>,
    event: Option<&str>,
    limit: usize,
) -> Result<Vec<Value>> {
    let path = local_path(&orgs::org_path(h, org)?, "activity.jsonl")?;
    if !path.is_file() {
        return Ok(vec![]);
    }
    let since = chrono::Utc::now() - chrono::Duration::days(days);
    let mut rows = std::collections::VecDeque::new();
    for v in json_lines(&path)? {
        if !instant(text(&v["at"])).is_ok_and(|t| t >= since)
            || env.is_some_and(|e| text(&v["environment"]) != e)
            || event.is_some_and(|e| text(&v["event"]) != e)
        {
            continue;
        }
        rows.push_back(v);
        if rows.len() > limit {
            rows.pop_front();
        }
    }
    Ok(rows.into_iter().rev().take(limit).collect())
}

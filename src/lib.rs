pub mod actions;
pub mod activity;
pub mod assets;
pub mod brief;
pub mod cli;
mod cli_definition;
pub mod connectors;
pub mod demo;
pub mod goals;
pub mod investigation;
pub mod migrate;
pub mod onepassword;
pub mod operations;
pub mod orgs;
pub mod process;
pub mod routines;
pub mod spend;
pub mod store;
pub mod watches;
pub mod workflows;
pub mod workspace;

pub use anyhow::{Context as ErrorContext, Result, anyhow, bail, ensure};
pub use serde_json::{Value, json};
pub use std::path::{Path, PathBuf};

pub fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}
pub fn array(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub fn strings(value: &Value) -> Vec<String> {
    array(value).iter().map(|v| text(v).to_owned()).collect()
}
pub fn number(value: &Value) -> String {
    let raw = value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string());
    let (integer, fraction) = raw.split_once('.').unwrap_or((&raw, ""));
    let sign = if integer.starts_with('-') { "-" } else { "" };
    let digits = integer.trim_start_matches('-');
    let mut out = String::from(sign);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    if !fraction.is_empty() {
        out.push('.');
        out.push_str(fraction);
    }
    out
}
pub fn object(value: &Value) -> serde_json::Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}
pub fn truth(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
        Value::Number(v) => v.as_f64() != Some(0.0),
    }
}
pub fn glob_match(pattern: &str, value: &str) -> bool {
    glob::Pattern::new(pattern).is_ok_and(|p| {
        p.matches_with(
            value,
            glob::MatchOptions {
                case_sensitive: true,
                require_literal_separator: false,
                require_literal_leading_dot: false,
            },
        )
    })
}
pub fn which(binary: &str) -> Option<PathBuf> {
    let candidate = Path::new(binary);
    let executable = |p: &Path| {
        if !p.is_file() {
            return false;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            p.metadata()
                .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
        }
        #[cfg(not(unix))]
        {
            true
        }
    };
    if candidate.components().count() > 1 {
        return executable(candidate).then(|| candidate.to_owned());
    }
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()).find_map(|dir| {
        let p = dir.join(binary);
        if executable(&p) {
            return Some(p);
        }
        #[cfg(windows)]
        for ext in ["exe", "cmd", "bat"] {
            let p = p.with_extension(ext);
            if executable(&p) {
                return Some(p);
            }
        }
        None
    })
}

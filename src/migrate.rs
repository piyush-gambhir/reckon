use crate::*;
use crate::{orgs::Registry, store::*};
use std::collections::{BTreeMap, BTreeSet};
fn parse(path: &Path) -> Result<(BTreeMap<String, String>, Vec<String>)> {
    let (mut values, mut skipped) = (BTreeMap::new(), vec![]);
    ensure!(
        !redirected(path),
        "legacy environment files must not be symlinks"
    );
    if !path.try_exists()? {
        return Ok((values, skipped));
    }
    let re = regex::Regex::new(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")?;
    let content = String::from_utf8(read_bytes(path, 1048576)?)
        .map_err(|_| anyhow!("legacy environment files must be valid UTF-8"))?;
    ensure!(
        !content.contains('\0'),
        "legacy environment files must not contain NUL bytes"
    );
    for (n, raw) in content.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(c) = re.captures(line) else {
            skipped.push(format!(
                "{} line {} (not KEY=value)",
                path.file_name().unwrap_or_default().to_string_lossy(),
                n + 1
            ));
            continue;
        };
        let k = c[1].to_owned();
        let mut v = c[2].trim();
        let quote = if v.len() >= 2
            && ((v.starts_with('\'') && v.ends_with('\''))
                || (v.starts_with('"') && v.ends_with('"')))
        {
            v.chars().next()
        } else {
            None
        };
        if quote.is_some() {
            v = &v[1..v.len() - 1];
        } else if let Some((rest, _)) = v.split_once(" #") {
            v = rest.trim_end();
        }
        if quote != Some('\'') && v.contains(['$', '`', '\\']) {
            skipped.push(format!(
                "{k} in {} (uses shell expansion; copy it by hand)",
                path.file_name().unwrap_or_default().to_string_lossy()
            ));
            continue;
        }
        values.insert(k, v.into());
    }
    Ok((values, skipped))
}
fn config_dirs(spec: &Value) -> BTreeSet<String> {
    strings(&spec["config_files"])
        .into_iter()
        .chain(object(&spec["paths"]).values().map(|v| text(v).to_owned()))
        .filter_map(|p| {
            Path::new(&p)
                .components()
                .next()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
        })
        .collect()
}
fn config_present(root: &Path, spec: &Value) -> Result<bool> {
    let files = strings(&spec["config_files"]);
    let directories = files.is_empty();
    let paths = if directories {
        object(&spec["paths"])
            .values()
            .map(|value| text(value).to_owned())
            .collect()
    } else {
        files
    };
    let mut present = false;
    for relative in paths {
        let path = local_path(root, relative)?;
        if path.try_exists()? {
            ensure!(
                if directories {
                    path.is_dir()
                } else {
                    path.is_file()
                },
                "legacy CLI config has an unexpected file type"
            );
            present = true;
        }
    }
    Ok(present)
}
fn digest(path: &Path) -> Result<Option<String>> {
    ensure!(
        !redirected(path),
        "migrated CLI config must not be redirected"
    );
    if !path.try_exists()? {
        return Ok(None);
    }
    ensure!(path.is_dir(), "migrated CLI config must be a directory");
    let mut files = vec![];
    for entry in walkdir::WalkDir::new(path).sort_by_file_name() {
        let entry = entry?;
        let relative = entry.path().strip_prefix(path)?;
        local_path(path, relative)?;
        ensure!(
            !redirected(entry.path())
                && (entry.file_type().is_file() || entry.file_type().is_dir()),
            "migrated CLI config requires regular files and directories"
        );
        if entry.file_type().is_file() {
            let name = relative
                .to_str()
                .ok_or_else(|| anyhow!("migrated CLI config filenames must be UTF-8"))?;
            // Hash each file separately so its bytes cannot mimic file boundaries.
            files.push(json!([
                name,
                sha256(read_bytes(entry.path(), 8 * 1048576)?)
            ]));
        }
    }
    Ok(Some(hash_json(&json!(files))))
}
fn migrate_session(path: &Path, org: &str, names: &BTreeMap<String, String>) -> Result<String> {
    let mut session = read_json(&path.join("session.json"))?;
    let id = identifier(text(&session["id"]))?.to_owned();
    validate_session(&session, &id)?;
    let old = text(&session["environment"]).to_owned();
    let environment = names
        .get(&old)
        .ok_or_else(|| anyhow!("migrated session environment is absent from the legacy layout"))?;
    session["org"] = json!(org);
    session["environment"] = json!(environment);
    write_json(&path.join("session.json"), &session)?;
    for dir in entries(&path.join("evidence"))? {
        let record = dir.join("record.json");
        if record.try_exists()? {
            let mut data = read_json(&record)?;
            ensure!(
                data.is_object(),
                "migrated evidence metadata must be an object"
            );
            ensure!(
                data["environment"] == old && data["session"] == session["id"],
                "migrated evidence does not belong to its source session and environment"
            );
            data["org"] = json!(org);
            data["environment"] = session["environment"].clone();
            write_json(&record, &data)?;
        }
    }
    evidence(path)?;
    Ok(id)
}
pub struct MigrationOptions<'a> {
    pub display: Option<&'a str>,
    pub dry_run: bool,
    pub rename: &'a Value,
    pub share: &'a [String],
}
pub fn migrate(
    h: &Path,
    org: &str,
    source: &Path,
    registry: &Registry,
    options: MigrationOptions<'_>,
) -> Result<Value> {
    let MigrationOptions {
        display,
        dry_run,
        rename,
        share,
    } = options;

    let source = orgs::absolute(source)?;
    for p in share {
        ensure!(
            registry.contains_key(p),
            "unknown provider for --share: {p}"
        );
    }
    let envs = ["production", "staging", "uat"]
        .iter()
        .filter(|e| {
            source.join(format!(".env.{e}")).is_file()
                || source.join(".config").join(e).is_dir()
                || source.join("infra-knowledge").join(e).is_dir()
        })
        .map(|e| (*e).to_owned())
        .collect::<Vec<_>>();
    ensure!(
        !envs.is_empty(),
        "no pre-org layout found in {} (.env.<env>, .config/<env> or infra-knowledge/<env>)",
        source.display()
    );
    let (common, mut skipped) = parse(&source.join(".env.common"))?;
    let mut settings = BTreeMap::new();
    let mut present: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for env in &envs {
        let mut values = common.clone();
        for suffix in ["", ".local"] {
            let (more, notes) = parse(&source.join(format!(".env.{env}{suffix}")))?;
            values.extend(more);
            skipped.extend(notes);
        }
        for (old, new) in [
            ("CUBEAPM_HOST", "CUBEAPM_SERVER"),
            ("JENKINS_USERNAME", "JENKINS_USER"),
        ] {
            if let Some(v) = values.remove(old) {
                values.entry(new.into()).or_insert(v);
            }
        }
        for (provider, spec) in registry {
            let has_keys = strings(&spec["required"]).iter().any(|g| {
                g.split('/')
                    .any(|k| values.get(k).is_some_and(|v| !v.is_empty()))
            });
            let has_config = config_present(&source.join(".config").join(env), spec)?;
            if has_keys || has_config {
                present
                    .entry(provider.clone())
                    .or_default()
                    .push(env.clone());
            }
        }
        settings.insert(env.clone(), values);
    }
    let mut names = BTreeMap::new();
    for env in &envs {
        names.insert(env.clone(), rename[env].as_str().unwrap_or(env).to_owned());
    }
    for (old, new) in object(rename) {
        ensure!(
            names.contains_key(&old),
            "--rename {old}={}: the old checkout has no {old} environment",
            text(&new)
        );
        orgs::name(text(&new), "environment")?;
    }
    ensure!(
        names.values().collect::<BTreeSet<_>>().len() == names.len(),
        "--rename would merge two environments"
    );
    let mut connections = json!({});
    let mut claimed = BTreeSet::new();
    for (provider, where_) in present {
        let spec = &registry[&provider];
        let prefixes = strings(&spec["prefixes"]);
        let mut per_env = BTreeMap::new();
        let mut configs = BTreeMap::new();
        for env in &where_ {
            let values = settings[env]
                .iter()
                .filter(|(k, _)| prefixes.iter().any(|p| k.starts_with(p)))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<BTreeMap<_, _>>();
            claimed.extend(values.keys().map(|key| (env.clone(), key.clone())));
            per_env.insert(env.clone(), values);
            let digests = config_dirs(spec)
                .iter()
                .map(|d| digest(&source.join(".config").join(env).join(d)))
                .collect::<Result<Vec<_>>>()?;
            configs.insert(env.clone(), digests);
        }
        let first = &where_[0];
        let same = where_
            .iter()
            .all(|e| per_env[e] == per_env[first] && configs[e] == configs[first]);
        ensure!(
            !share.contains(&provider) || same,
            "{provider} has different settings per environment; it cannot become one shared connection"
        );
        let shared = share.contains(&provider) || (where_ == envs && envs.len() > 1 && same);
        for env in if shared { vec![first.clone()] } else { where_ } {
            let name = if shared {
                provider.clone()
            } else {
                format!("{provider}-{}", names[&env])
            };
            connections[name] = json!({"provider":provider,"env":if shared{None}else{Some(&names[&env])},"values":per_env[&env],"config":config_dirs(spec).iter().filter(|d|source.join(".config").join(&env).join(d).is_dir()).collect::<Vec<_>>(),"from_env":env});
        }
    }
    for env in &envs {
        for k in settings[env].keys() {
            if !claimed.contains(&(env.clone(), k.clone())) && k != "PATH" {
                skipped.push(format!("{k} in {env} (no matching tool)"));
            }
        }
    }
    let sessions = entries(&source.join("sessions"))?
        .into_iter()
        .filter(|p| p.join("session.json").is_file())
        .collect::<Vec<_>>();
    let incidents = entries(&source.join("incidents"))?
        .into_iter()
        .filter(|p| {
            p.is_dir()
                && !p
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .starts_with('.')
        })
        .collect::<Vec<_>>();
    let mut summary_conns = json!({});
    for (n, c) in object(&connections) {
        summary_conns[n] = json!({"provider":c["provider"],"environment":c["env"].as_str().unwrap_or("all"),"settings":object(&c["values"]).keys().collect::<Vec<_>>(),"config":c["config"]});
    }
    let summary = json!({"org":org,"source":source,"environments":envs.iter().map(|e|&names[e]).collect::<Vec<_>>(),"connections":summary_conns,"skipped":skipped.into_iter().collect::<BTreeSet<_>>(),"sessions":sessions.len(),"incidents":incidents.len(),"knowledge":source.join("infra-knowledge").is_dir(),"dry_run":dry_run,"login_again":object(&connections).iter().filter(|(_,c)|truth(&c["config"])&&truth(&registry[text(&c["provider"])]["real_home"])).map(|(n,_)|n.clone()).collect::<Vec<_>>()});
    if dry_run {
        return Ok(summary);
    }
    let dest = orgs::org_path(h, org)?;
    ensure!(
        !dest.exists(),
        "org already exists (or its folder does): {org}"
    );
    // Failed creation must never enter cleanup for another process's org.
    let mut data = orgs::create(
        h,
        org,
        display,
        envs.iter().map(|e| names[e].clone()).collect(),
        Some(
            envs.iter()
                .filter(|e| e.as_str() == "production")
                .map(|e| names[e].clone())
                .collect(),
        ),
        registry,
    )?;
    let result = (|| -> Result<()> {
        for (cname, conn) in object(&connections) {
            let mut c = json!({"provider":conn["provider"]});
            if !conn["env"].is_null() {
                c["env"] = conn["env"].clone();
            }
            data["connections"][&cname] = c;
            let lines = object(&conn["values"])
                .iter()
                .map(|(k, v)| {
                    let v = text(v);
                    let v = if v.trim() != v
                        || (v.len() >= 2
                            && ((v.starts_with('\'') && v.ends_with('\''))
                                || (v.starts_with('"') && v.ends_with('"'))))
                    {
                        format!("'{v}'")
                    } else {
                        v.into()
                    };
                    format!("{k}={v}\n")
                })
                .collect::<String>();
            write(
                &orgs::secrets_path(h, org, &cname)?,
                format!(
                    "# Migrated from {:?} ({})\n{lines}",
                    source,
                    text(&conn["from_env"])
                ),
            )?;
            ensure!(
                json!(orgs::secrets(
                    h,
                    org,
                    &cname,
                    &registry[text(&conn["provider"])]
                )?) == conn["values"],
                "migrated settings for {cname} did not round-trip"
            );
            for d in strings(&conn["config"]) {
                copy_tree(
                    &source
                        .join(".config")
                        .join(text(&conn["from_env"]))
                        .join(&d),
                    &local_path(&dest, format!("config/{cname}/{d}"))?,
                )?;
            }
        }
        orgs::save(h, &data, registry)?;
        let root = orgs::workspace(h, org)?;
        for entry in entries(&source.join("infra-knowledge"))? {
            let n = entry
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string();
            let target = local_path(&root, format!("knowledge/{}", names.get(&n).unwrap_or(&n)))?;
            if entry.is_dir() {
                copy_tree(&entry, &target)?;
            } else {
                write(&target, read_bytes(&entry, 8 * 1048576)?)?;
            }
        }
        for entry in sessions {
            let target = local_path(
                &root,
                Path::new("sessions").join(entry.file_name().unwrap_or_default()),
            )?;
            copy_tree(&entry, &target)?;
            let id = migrate_session(&target, org, &names)?;
            ensure!(
                entry.file_name().and_then(|name| name.to_str()) == Some(id.as_str()),
                "migrated session ID does not match its directory"
            );
        }
        for entry in incidents {
            let target = local_path(
                &root,
                Path::new("incidents").join(entry.file_name().unwrap_or_default()),
            )?;
            copy_tree(&entry, &target)?;
            if target.join("session.json").try_exists()? {
                migrate_session(&target, org, &names)?;
            }
        }
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_dir_all(dest);
        return Err(e);
    }
    Ok(summary)
}

use crate::*;
use crate::{orgs::Registry, store::*};
use std::collections::{BTreeMap, BTreeSet};
fn parse(path: &Path) -> Result<(BTreeMap<String, String>, Vec<String>)> {
    let (mut values, mut skipped) = (BTreeMap::new(), vec![]);
    if !path.is_file() {
        return Ok((values, skipped));
    }
    let re = regex::Regex::new(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")?;
    for (n, raw) in String::from_utf8_lossy(&read_bytes(path, 1048576)?)
        .lines()
        .enumerate()
    {
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
fn digest(path: &Path) -> Result<Option<String>> {
    if !path.is_dir() {
        return Ok(None);
    }
    let mut content = vec![];
    for p in walkdir::WalkDir::new(path)
        .sort_by_file_name()
        .into_iter()
        .filter_map(std::result::Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| e.into_path())
    {
        content.extend(p.strip_prefix(path)?.to_string_lossy().as_bytes());
        content.push(0);
        content.extend(read_bytes(&p, 8 * 1048576)?);
        content.push(0);
    }
    Ok(Some(sha256(content)))
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
            let has_config = strings(&spec["config_files"])
                .iter()
                .any(|p| source.join(".config").join(env).join(p).is_file())
                || object(&spec["paths"])
                    .values()
                    .any(|p| source.join(".config").join(env).join(text(p)).is_file());
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
            claimed.extend(values.keys().cloned());
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
            if !claimed.contains(k) && k != "PATH" {
                skipped.push(format!("{k} (no matching tool)"));
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
    let result = (|| -> Result<()> {
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
                    "# Migrated from {} ({})\n{lines}",
                    source.display(),
                    text(&conn["from_env"])
                ),
            )?;
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
            let mut session = read_json(&target.join("session.json"))?;
            session["org"] = json!(org);
            let old = text(&session["environment"]);
            session["environment"] = json!(names.get(old).map(String::as_str).unwrap_or(old));
            write_json(&target.join("session.json"), &session)?;
            for d in entries(&target.join("evidence"))? {
                let p = d.join("record.json");
                if p.is_file() {
                    let mut r = read_json(&p)?;
                    r["org"] = json!(org);
                    r["environment"] = session["environment"].clone();
                    write_json(&p, &r)?;
                }
            }
        }
        for entry in incidents {
            copy_tree(
                &entry,
                &local_path(
                    &root,
                    Path::new("incidents").join(entry.file_name().unwrap_or_default()),
                )?,
            )?;
        }
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_dir_all(dest);
        return Err(e);
    }
    Ok(summary)
}

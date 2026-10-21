use crate::*;

use crate::{
    orgs::{Context, Registry},
    store::*,
};

use std::{
    collections::BTreeSet,
    io::{IsTerminal, Read, Write},
};

pub struct Args {
    pub command: String,
    values: Value,
    pub passthrough: Vec<String>,
}

impl Args {
    pub fn s(&self, key: &str) -> &str {
        self.opt(key).unwrap_or("")
    }

    pub fn opt(&self, key: &str) -> Option<&str> {
        self.values[key]
            .as_array()
            .and_then(|v| v.first())
            .and_then(Value::as_str)
    }

    pub fn vs(&self, key: &str) -> Vec<String> {
        strings(&self.values[key])
    }

    pub fn flag(&self, key: &str) -> bool {
        self.values[key].as_bool().unwrap_or(false)
    }

    pub fn i(&self, key: &str) -> Result<i64> {
        self.s(key)
            .parse()
            .map_err(|_| anyhow!("--{} requires an integer", key.replace('_', "-")))
    }

    pub fn u(&self, key: &str) -> Result<u64> {
        self.s(key).parse().map_err(|_| {
            anyhow!(
                "--{} requires a non-negative integer",
                key.replace('_', "-")
            )
        })
    }

    pub fn f(&self, key: &str) -> Result<f64> {
        self.s(key)
            .parse()
            .map_err(|_| anyhow!("--{} requires a number", key.replace('_', "-")))
    }
}

pub fn parse(argv: Vec<String>) -> std::result::Result<Args, clap::Error> {
    let cut = argv.iter().position(|v| v == "--").unwrap_or(argv.len());

    let passthrough = argv.get(cut + 1..).unwrap_or(&[]).to_vec();

    let matches = crate::cli_definition::command()
        .version(env!("CARGO_PKG_VERSION"))
        .try_get_matches_from(
            std::iter::once("reckon".to_owned()).chain(argv[..cut].iter().cloned()),
        )?;

    let mut values = json!({});

    let mut current = &matches;

    let mut command = String::new();

    while let Some((name, child)) = current.subcommand() {
        if command.is_empty() {
            command = name.into();
        } else {
            values["action"] = json!([name]);
        }

        for id in child.ids() {
            let key = id.as_str();

            if let Ok(Some(v)) = child.try_get_one::<bool>(key) {
                values[key] = json!(v);
            } else if let Ok(Some(v)) = child.try_get_many::<String>(key) {
                values[key] = json!(v.cloned().collect::<Vec<_>>());
            }
        }

        current = child;
    }

    if !passthrough.is_empty() && !["run", "open", "watch"].contains(&command.as_str()) {
        return Err(clap::Error::raw(
            clap::error::ErrorKind::UnknownArgument,
            "only run, open and watch add take arguments after --",
        ));
    }

    Ok(Args {
        command,
        values,
        passthrough,
    })
}

fn split(target: &str) -> (&str, Option<&str>) {
    target
        .split_once('/')
        .map(|(o, e)| (o, (!e.is_empty()).then_some(e)))
        .unwrap_or((target, None))
}

fn context(h: &Path, a: &Args, registry: &Registry, need_env: bool) -> Result<Context> {
    if !need_env && a.opt("org").is_some() && a.opt("env").is_none() {
        let data = orgs::load(h, a.s("org"), registry)?;

        return Ok(Context::from_data(data, ""));
    }

    orgs::context(h, a.opt("org"), a.opt("env"), registry)
}

pub fn parse_params(items: &[String]) -> Result<Value> {
    let mut params = json!({});

    for item in items {
        let (k, v) = item
            .split_once('=')
            .ok_or_else(|| anyhow!("--param takes KEY=VALUE, once per key"))?;

        ensure!(
            params.get(k).is_none(),
            "--param takes KEY=VALUE, once per key"
        );

        params[k] = json!(v);
    }

    Ok(params)
}

fn question(value: &str) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.chars().count() <= 16000,
        "question must contain 1–16000 characters"
    );

    Ok(())
}

fn available(h: &Path, ctx: &Context, registry: &Registry) -> BTreeSet<String> {
    orgs::describe(h, ctx, registry)
        .iter()
        .filter(|r| truth(&r["applies"]))
        .map(|r| text(&r["provider"]).to_owned())
        .collect()
}

fn checks_path(h: &Path, org: &str, cname: &str) -> Result<PathBuf> {
    local_path(
        &orgs::org_path(h, org)?,
        format!("runtime/{cname}/verify.json"),
    )
}

fn remember(h: &Path, ctx: &Context, rows: &[Value]) {
    for row in rows {
        if text(&row["status"]) == "skipped" {
            continue;
        }

        let cname = text(&row["connection"]);

        let entry = activity::record(
            h,
            ctx,
            "verify",
            json!({
            "target":cname,"status":row["status"],"account":row["account"],"detail":row["detail"]}
            ),
        );

        let _ = (|| -> Result<()> {
            let path = checks_path(h, &ctx.org, cname)?;

            let _lock = lock(path.parent().ok_or_else(|| anyhow!("missing parent"))?)?;

            let mut saved = if path.is_file() {
                read_json(&path)?
            } else {
                json!({})
            };

            if !saved.is_object() {
                saved = json!({});
            }

            saved[&ctx.env] = json!({
            "at":entry["at"],"status":row["status"],"detail":entry["detail"],"connection_sha256":hash_json(&ctx.data["connections"][cname])}
            );

            write_json(&path, &saved)
        })();
    }
}

fn described(h: &Path, ctx: &Context, registry: &Registry) -> Vec<Value> {
    let mut rows = orgs::describe(h, ctx, registry);

    for row in &mut rows {
        if !truth(&row["applies"]) {
            continue;
        }

        let cname = text(&row["connection"]);

        if let Ok(saved) = checks_path(h, &ctx.org, cname).and_then(|p| read_json(&p)) {
            let found = &saved[&ctx.env];

            if found.is_object()
                && text(&found["connection_sha256"]) == hash_json(&ctx.data["connections"][cname])
            {
                row["verified"] = json!({
                "at":found["at"],"status":found["status"],"detail":found["detail"]}
                );
            }
        }
    }

    rows
}

fn knowledge_count(root: &Path, layer: &str) -> usize {
    entries(&root.join("knowledge").join(layer))
        .unwrap_or_default()
        .iter()
        .filter(|p| p.extension().is_some_and(|e| e == "md" || e == "json"))
        .count()
}

fn preflight(h: &Path, ctx: &Context, registry: &Registry) -> Result<Value> {
    let root = orgs::workspace(h, &ctx.org)?;

    let rows = described(h, ctx, registry);

    let sessions = investigation::history(&root, Some(&ctx.env), None, None, 200)?;

    let open = sessions
        .iter()
        .filter(|s| text(&s["status"]) == "open")
        .take(5)
        .map(|s| {
            json!({
            "id":s["id"],"question":s["question"]}
            )
        })
        .collect::<Vec<_>>();

    let incidents = entries(&root.join("incidents"))?
        .iter()
        .filter(|p| {
            p.is_dir()
                && !p
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .starts_with('.')
        })
        .map(|p| {
            p.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string()
        })
        .collect::<Vec<_>>();

    let mut warnings = vec![];

    if ctx.production {
        warnings.push("PRODUCTION: prefer the cheapest signal and bound every query".to_owned());
    }

    if !rows.iter().any(|r| truth(&r["applies"])) {
        warnings.push(format!(
            "no connections for {}; ask the operator to run reckon connect",
            ctx.env
        ));
    }

    for r in &rows {
        if truth(&r["applies"])
            && !r["verified"]["status"].is_null()
            && text(&r["verified"]["status"]) != "ok"
        {
            warnings.push(format!("{} failed its last live check ({}); ask the operator to fix it and run reckon verify {}",text(&r["connection"]),text(&r["verified"]["status"]),text(&r["connection"])));
        }
    }

    if knowledge_count(&root, &ctx.env) == 0 && knowledge_count(&root, "_shared") == 0 {
        warnings.push("no knowledge for this org yet; do not invent facts".into());
    }

    Ok(json!({
    "org":ctx.org,"name":ctx.data["name"],"environment":ctx.env,"production":ctx.production,"resolved_from":ctx.source,"workspace":root,"connections":rows.iter().filter(|r|truth(&r["applies"])).map(|r|json!({
    "connection":r["connection"],"provider":r["provider"],"state":r["state"],"label":r["label"],"verified":r["verified"]}
    )).collect::<Vec<_>>(),"other_environments":rows.iter().filter(|r|!truth(&r["applies"])).map(|r|format!("{} ({})",text(&r["connection"]),text(&r["scope"]))).collect::<Vec<_>>(),"knowledge":{
    "_shared":knowledge_count(&root,"_shared"),&ctx.env:knowledge_count(&root,&ctx.env)}
    ,"open_sessions":open,"incidents":{
    "count":incidents.len(),"latest":incidents.last()}
    ,"warnings":warnings,"note":"ready means configured locally; reckon verify proves live access"}
    ))
}

fn version_tuple(value: &str) -> Option<(u64, u64, u64)> {
    let preferred = regex::Regex::new(r"(?i)version[\s:=]+v?(\d+)\.(\d+)\.(\d+)").ok()?;

    let fallback = regex::Regex::new(r"v?(\d+)\.(\d+)\.(\d+)").ok()?;

    let c = preferred
        .captures(value)
        .or_else(|| fallback.captures(value))?;

    Some((c[1].parse().ok()?, c[2].parse().ok()?, c[3].parse().ok()?))
}

fn doctor(h: &Path, registry: &Registry) -> Result<Value> {
    let mut checks = vec![format!("Reckon {} (Rust)", env!("CARGO_PKG_VERSION"))];

    let mut problems = vec![];

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        if h.exists() && h.metadata()?.permissions().mode() & 0o077 != 0 {
            problems.push(format!(
                "{} is readable by other users; run: chmod 700 {}",
                h.display(),
                h.display()
            ));
        }
    }

    let own = if activity::pinned() {
        Some(std::env::var("RECKON_ORG").unwrap_or_default())
    } else {
        None
    };

    let found = orgs::orgs(h)?
        .into_iter()
        .filter(|o| own.as_ref().is_none_or(|n| n == o))
        .collect::<Vec<_>>();

    checks.push(format!(
        "orgs: {}",
        if found.is_empty() {
            "none (reckon org add <org> or reckon migrate --org <org>)".into()
        } else {
            found.join(", ")
        }
    ));

    for org in found {
        let data = match orgs::load(h, &org, registry) {
            Ok(d) => d,
            Err(e) => {
                problems.push(e.to_string());

                continue;
            }
        };

        for (cname, conn) in object(&data["connections"]) {
            let spec = &registry[text(&conn["provider"])];

            match orgs::configured(h, &org, &cname, spec, None) {
                Ok(false) => checks.push(format!("{org}/{cname}: no credentials yet")),
                Err(e) => problems.push(format!("{org}/{cname}: {e}")),
                _ => {}
            }

            if which(text(&spec["binary"])).is_none() {
                checks.push(format!(
                    "{org}/{cname}: {} is not installed",
                    text(&spec["binary"])
                ));
            }
        }
    }

    let csv = include_str!("../scripts/cli-releases.csv");

    let mut lines = csv.lines();

    let header = lines.next().unwrap_or("").split(',').collect::<Vec<_>>();

    let bin_col = header.iter().position(|k| *k == "binary").unwrap_or(0);

    let release_col = header.iter().position(|k| *k == "release").unwrap_or(1);

    for line in lines {
        let columns = line.split(',').collect::<Vec<_>>();

        let binary = columns.get(bin_col).copied().unwrap_or("");

        let release = columns.get(release_col).copied().unwrap_or("");

        if which(binary).is_none() {
            continue;
        }

        let env = process::Environment::from([
            ("PATH".into(), std::env::var("PATH").unwrap_or_default()),
            ("HOME".into(), std::env::var("HOME").unwrap_or_default()),
            ("NO_UPDATE_NOTIFIER".into(), "1".into()),
            ("CI".into(), "1".into()),
        ]);

        let result = process::run(
            &[binary.into(), "version".into()],
            Some(&env),
            None,
            5.0,
            65536,
            None,
        )?;

        let output = format!(
            "{}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );

        if let (Some(have), Some(want)) = (version_tuple(&output), version_tuple(release))
            && have < want
        {
            checks.push(format!("{binary} {}.{}.{} is older than the pinned {release} (rerun scripts/setup.sh or setup.ps1 to upgrade it)",have.0,have.1,have.2));
        }
    }

    let link = h.join("bin/reckon");

    if !link.is_symlink() {
        checks.push("reckon command: created on the first reckon open".into());
    } else if !std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .any(|p| p == h.join("bin"))
    {
        checks.push(format!(
            "reckon command: {} is not on PATH; add to your shell rc: export PATH=\"{}:$PATH\"",
            link.display(),
            link.parent().unwrap_or(h).display()
        ));
    } else {
        checks.push(format!("reckon command: {}", link.display()));
    }

    checks.push(match orgs::context(h, None, None, registry) {
        Ok(ctx) => format!(
            "default context: {}/{} (from {})",
            ctx.org, ctx.env, ctx.source
        ),
        Err(e) => format!("default context: {e}"),
    });

    Ok(json!({
    "home":h,"checks":checks,"problems":problems}
    ))
}

fn connect(h: &Path, a: &Args, registry: &Registry) -> Result<Value> {
    if a.flag("list") {
        return Ok(json!(registry.iter().map(|(k,v)|json!({
"provider":k,"title":v["title"].as_str().unwrap_or(k),"binary":v["binary"],"mode":v["mode"],"settings":v.get("required").unwrap_or(&json!([]))}
)).collect::<Vec<_>>()));
    }

    let cname = a.s("name");

    ensure!(
        !cname.is_empty(),
        "usage: reckon connect <name> --provider <tool> [--for-env <env> | --label <env>=<label>]"
    );

    let mut ctx = context(h, a, registry, false)?;

    orgs::name(cname, "connection")?;

    let mut data = ctx.data.clone();

    if a.flag("remove") {
        ensure!(
            data["connections"].get(cname).is_some(),
            "unknown connection: {cname}"
        );

        data["connections"]
            .as_object_mut()
            .ok_or_else(|| anyhow!("invalid connections"))?
            .remove(cname);

        orgs::save(h, &data, registry)?;

        let checks = checks_path(h, &ctx.org, cname)?;

        if checks.is_file() {
            std::fs::remove_file(checks)?;
        }

        ctx.data = data;

        workspace::ensure(h, &ctx, registry)?;

        return Ok(json!({
        "org":ctx.org,"removed":cname,"secrets_kept":orgs::secrets_path(h,&ctx.org,cname)?}
        ));
    }

    let mut conn = data["connections"][cname].clone();

    if conn.is_object() {
        ensure!(
            a.opt("provider")
                .is_none_or(|p| p == text(&conn["provider"])),
            "{cname} already uses {}; pick another name",
            text(&conn["provider"])
        );
    } else {
        ensure!(
            registry.contains_key(a.s("provider")),
            "choose --provider from: {}",
            registry.keys().cloned().collect::<Vec<_>>().join(", ")
        );

        conn = json!({
        "provider":a.s("provider")}
        );
    }

    if let Some(bound) = a.opt("bound") {
        ensure!(
            a.vs("label").is_empty(),
            "a connection tied to one environment has no labels; --label is for shared backends"
        );

        conn.as_object_mut().expect("object").remove("env_labels");

        conn["env"] = json!(bound);
    }

    let envs = strings(&data["environments"]);

    for label in a.vs("label") {
        let (env, value) = label.split_once('=').unwrap_or((&label, ""));

        ensure!(
            envs.contains(&env.to_owned()) && orgs::valid_label(value),
            "--label takes ENV=LABEL with one of: {}",
            envs.join(", ")
        );

        conn.as_object_mut().expect("object").remove("env");

        if !conn["env_labels"].is_object() {
            conn["env_labels"] = json!({});
        }

        conn["env_labels"][env] = json!(value);
    }

    if a.flag("allow_any") {
        conn.as_object_mut().expect("object").remove("resources");
    }

    for item in a.vs("allow") {
        let (kind, pattern) = item.split_once('=').unwrap_or((&item, ""));

        ensure!(!pattern.is_empty(), "--allow takes KIND=PATTERN");

        if !conn["resources"].is_object() {
            conn["resources"] = json!({});
        }

        if !conn["resources"][kind].is_array() {
            conn["resources"][kind] = json!([]);
        }

        if !array(&conn["resources"][kind]).contains(&json!(pattern)) {
            conn["resources"][kind]
                .as_array_mut()
                .expect("array")
                .push(json!(pattern));
        }
    }

    if let Some(account) = a.opt("account") {
        if account.is_empty() {
            conn.as_object_mut().expect("object").remove("account");
        } else {
            ensure!(
                truth(&registry[text(&conn["provider"])]["identity"]),
                "{} does not report which account it uses, so --account cannot be checked",
                text(&conn["provider"])
            );

            conn["account"] = json!(account);
        }
    }

    data["connections"][cname] = conn.clone();

    orgs::save(h, &data, registry)?;

    ctx.data = data;

    workspace::ensure(h, &ctx, registry)?;

    let spec = &registry[text(&conn["provider"])];

    let (path, created) = connectors::template(h, &ctx.org, cname, spec)?;

    let ready = orgs::configured(h, &ctx.org, cname, spec, None).unwrap_or(false);

    let env = std::iter::once(ctx.env.clone())
        .chain(envs)
        .find(|e| !e.is_empty() && orgs::applies(&conn, e))
        .unwrap_or_default();

    let target = format!("{cname} --org {} --env {env}", ctx.org);

    Ok(json!({
    "org":ctx.org,"connection":cname,"provider":conn["provider"],"environment":conn["env"].as_str().unwrap_or("all"),"env_labels":conn.get("env_labels").unwrap_or(&json!({
    }
    )),"resources":conn.get("resources").unwrap_or(&json!({
    }
    )),"account":conn["account"],"secrets":path,"secrets_created":created,"next":if ready{
    format!("reckon verify {target}")}
    else{
    format!("edit {}{}, then: reckon verify {target}",path.display(),if truth(&spec["login"]){
    format!(" or run: reckon login {target}")}
    else{
    String::new()}
    )}
    }
    ))
}

pub fn execute(a: &Args) -> Result<Value> {
    let h = orgs::home()?;

    let registry = orgs::providers()?;

    let name = a.command.as_str();

    if activity::pinned()
        && ([
            "use", "link", "unlink", "connect", "login", "migrate", "open", "routine", "watch",
        ]
        .contains(&name)
            || (name == "org" && a.s("action") == "add")
            || (name == "secrets"
                && ["setup", "set", "link", "import", "recover"].contains(&a.s("action")))
            || (name == "act" && a.s("action") == "apply"))
    {
        bail!("reckon {name} is for the operator's own terminal, not a pinned agent");
    }

    match name {
        "secrets" => {
            if a.s("action") == "setup" {
                return onepassword::setup(
                    &h,
                    a.s("vault"),
                    a.s("reader"),
                    a.opt("writer"),
                    a.s("op"),
                );
            }
            if a.s("action") == "status" {
                let ctx = if a.opt("org").is_some() || a.opt("env").is_some() || activity::pinned()
                {
                    Some(context(
                        &h,
                        a,
                        &registry,
                        activity::pinned() || a.opt("env").is_some(),
                    )?)
                } else {
                    None
                };
                return onepassword::status(&h, ctx.as_ref(), &registry);
            }
            let ctx = context(&h, a, &registry, true)?;
            if a.s("action") == "check" {
                return onepassword::check(&h, &ctx, a.s("connection"), &registry);
            }
            if a.s("action") == "benchmark" {
                return onepassword::benchmark(
                    &h,
                    &ctx,
                    a.s("connection"),
                    &registry,
                    a.u("samples")?,
                );
            }
            let result = match a.s("action") {
                "set" => onepassword::set_secret(
                    &h,
                    &ctx,
                    a.s("connection"),
                    a.s("key"),
                    &registry,
                    a.flag("update_linked"),
                ),
                "link" => onepassword::link(
                    &h,
                    &ctx,
                    a.s("connection"),
                    a.s("key"),
                    a.s("item"),
                    a.s("field"),
                    &registry,
                ),
                "import" => onepassword::import(
                    &h,
                    &ctx,
                    a.s("connection"),
                    &registry,
                    &a.vs("key"),
                    a.flag("dry_run"),
                ),
                "recover" => onepassword::recover(&h, &ctx, a.s("connection"), &registry),
                _ => bail!("unknown secret operation"),
            }?;
            if !truth(&result["dry_run"]) {
                activity::record(
                    &h,
                    &ctx,
                    &format!("secrets.{}", a.s("action")),
                    json!({"target":result["connection"]}),
                );
            }
            return Ok(result);
        }
        "org" => {
            let org = a.s("org");

            if a.s("action") == "add" {
                let source = if let Some(like) = a.opt("like") {
                    orgs::load(&h, like, &registry)?
                } else {
                    Value::Null
                };

                let envs = if let Some(envs) = a.opt("envs") {
                    envs.split(',')
                        .map(str::trim)
                        .filter(|s| !s.is_empty())
                        .map(str::to_owned)
                        .collect()
                } else if source.is_object() {
                    strings(&source["environments"])
                } else {
                    vec!["prod".into(), "uat".into(), "dev".into()]
                };

                let production = if let Some(p) = a.opt("production") {
                    Some(p.split(',').map(|s| s.trim().to_owned()).collect())
                } else if source.is_object() && a.opt("envs").is_none() {
                    Some(strings(&source["production"]))
                } else {
                    None
                };

                let mut data =
                    orgs::create(&h, org, a.opt("name"), envs.clone(), production, &registry)?;

                let mut templates = vec![];

                if source.is_object() {
                    for (cname, conn) in object(&source["connections"]) {
                        let mut copied = json!({
                        "provider":conn["provider"]}
                        );

                        for k in ["env", "env_labels"] {
                            if let Some(v) = conn.get(k) {
                                copied[k] = v.clone();
                            }
                        }

                        if copied["env"]
                            .as_str()
                            .is_none_or(|e| envs.contains(&e.to_owned()))
                            && object(&copied["env_labels"])
                                .keys()
                                .all(|e| envs.contains(e))
                        {
                            data["connections"][cname] = copied;
                        }
                    }

                    orgs::save(&h, &data, &registry)?;

                    for (cname, conn) in object(&data["connections"]) {
                        templates.push(
                            connectors::template(
                                &h,
                                org,
                                &cname,
                                &registry[text(&conn["provider"])],
                            )?
                            .0,
                        );
                    }
                }

                workspace::ensure(&h, &Context::from_data(data.clone(), &envs[0]), &registry)?;

                if !h.join("context").exists() {
                    orgs::set_context(&h, org, &envs[0], &registry)?;
                }

                return Ok(json!({
                "org":org,"environments":envs,"production":data["production"],"connections":object(&data["connections"]).keys().collect::<Vec<_>>(),"fill_in":templates,"next":if templates.is_empty(){
                format!("reckon connect <name> --org {org} --provider <tool> [--for-env <env>]")}
                else{
                format!("fill in each credentials file, then reckon verify --org {org} --env {}",envs[0])}
                }
                ));
            }

            let own = if activity::pinned() {
                Some(std::env::var("RECKON_ORG").unwrap_or_default())
            } else {
                None
            };

            if a.s("action") == "list" {
                let mut rows = vec![];

                for org in orgs::orgs(&h)? {
                    if own.as_ref().is_some_and(|o| o != &org) {
                        continue;
                    }

                    let data = orgs::load(&h, &org, &registry)?;

                    rows.push(json!({
                    "org":org,"name":data["name"],"environments":data["environments"]}
                    ));
                }

                return Ok(json!(rows));
            }

            let org = if org.is_empty() {
                orgs::context(&h, None, None, &registry)?.org
            } else {
                org.into()
            };

            ensure!(
                own.as_ref().is_none_or(|o| o == &org),
                "a pinned agent sees only its own org"
            );

            let mut data = orgs::load(&h, &org, &registry)?;

            data["workspace"] = json!(orgs::workspace(&h, &org)?);

            return Ok(data);
        }

        "use" => {
            let (mut org, mut env) = split(a.s("target"));

            let selected;

            if env.is_none() && !orgs::orgs(&h)?.contains(&org.to_owned()) {
                env = Some(org);

                selected = orgs::context(&h, None, None, &registry)?.org;

                org = &selected;
            }

            orgs::set_context(&h, org, env.unwrap_or(""), &registry)?;

            let mut result = json!({
            "org":org,"environment":env,"note":"used by the next reckon open and by shells without a pinned context"}
            );

            let here = orgs::linked(&h, None)?;

            if !here.is_null()
                && (text(&here["org"]) != org || text(&here["env"]) != env.unwrap_or(""))
            {
                result["warning"] = json!(format!(
                    "this folder is linked to {}/{} and keeps it (reckon link)",
                    text(&here["org"]),
                    text(&here["env"])
                ));
            }

            return Ok(result);
        }

        "link" => {
            if a.opt("target").is_none() {
                ensure!(
                    a.opt("folder").is_none() && a.vs("use").is_empty(),
                    "name the org and environment: reckon link <org>/<env> [<folder>] [--use <connection>]"
                );

                let here = orgs::linked(&h, None)?;

                return Ok(json!({
                "links":object(&orgs::links(&h)?).into_iter().map(|(p,mut v)|{
                v["path"]=json!(p);
                v}
                ).collect::<Vec<_>>(),"here":here["path"]}
                ));
            }

            let (org, env) = split(a.s("target"));

            let env =
                env.ok_or_else(|| anyhow!("name the environment too: reckon link {org}/<env>"))?;

            return orgs::link(
                &h,
                &a.opt("folder")
                    .map(PathBuf::from)
                    .unwrap_or(std::env::current_dir()?),
                org,
                env,
                a.vs("use"),
                &registry,
            );
        }

        "unlink" => {
            return orgs::unlink(
                &h,
                &a.opt("folder")
                    .map(PathBuf::from)
                    .unwrap_or(std::env::current_dir()?),
            );
        }

        "connect" => return connect(&h, a, &registry),
        "migrate" => {
            let mut rename = json!({});

            for item in a.vs("rename") {
                let (old, new) = item
                    .split_once('=')
                    .ok_or_else(|| anyhow!("--rename takes OLD=NEW"))?;

                ensure!(rename.get(old).is_none(), "--rename takes OLD=NEW");

                rename[old] = json!(new);
            }

            let mut result = migrate::migrate(
                &h,
                a.s("org"),
                &a.opt("source")
                    .map(PathBuf::from)
                    .unwrap_or(std::env::current_dir()?),
                &registry,
                migrate::MigrationOptions {
                    display: a.opt("name"),
                    dry_run: a.flag("dry_run"),
                    rename: &rename,
                    share: &a.vs("share"),
                },
            )?;

            if !a.flag("dry_run") {
                let data = orgs::load(&h, a.s("org"), &registry)?;

                workspace::ensure(&h, &Context::from_data(data, ""), &registry)?;

                result["next"] = json!(format!(
                    "reckon use {}/<env>, reckon verify, then reckon open",
                    a.s("org")
                ));
            }

            return Ok(result);
        }

        "open" => {
            let (org, env) = a.opt("target").map(split).unwrap_or(("", None));

            ensure!(
                org.is_empty() || env.is_some(),
                "name the environment too: reckon open {org}/<env>"
            );

            let ctx = orgs::context(&h, (!org.is_empty()).then_some(org), env, &registry)?;

            match spend::over(&h, &ctx.org) {
                Ok(Some(w)) => eprintln!("warning: {w} (reckon spend)"),
                Err(e) => {
                    eprintln!("warning: could not check the agent budget: {e} (reckon spend)")
                }

                _ => {}
            }

            return Ok(json!({
            "_exit":workspace::launch(&h,&ctx,&registry,a.opt("runtime"),&a.passthrough)?}
            ));
        }

        "demo" => return demo::run(&h),
        "doctor" => return doctor(&h, &registry),
        "act" if a.s("action") == "apply" => {
            let id = a.s("target");

            ensure!(!id.is_empty(), "usage: reckon act apply <action-id>");

            identifier(id)?;

            let mut owner = None;

            for org in orgs::orgs(&h)? {
                let root = orgs::workspace(&h, &org)?;

                if local_path(&root, format!("actions/{id}/action.json"))?.is_file() {
                    owner = Some((org, root));

                    break;
                }
            }

            let (org, root) = owner.ok_or_else(|| anyhow!("unknown action: {id}"))?;

            let record = actions::load(&root, id)?.1;

            let ctx = orgs::context(
                &h,
                Some(&org),
                Some(text(&record["environment"])),
                &registry,
            )?;

            let done = actions::apply(&h, &ctx, &root, id, &registry)?;

            activity::record(
                &h,
                &ctx,
                "act.apply",
                json!({
                "target":done["connection"],"action":done["id"],"operation":done["operation"],"status":done["status"]}
                ),
            );

            return Ok(done);
        }

        "routine" | "watch" if a.s("action") != "add" => {
            let org = if let Some(org) = a.opt("org") {
                org.to_owned()
            } else {
                orgs::context(&h, None, None, &registry)?.org
            };

            orgs::load(&h, &org, &registry)?;

            if name == "routine" {
                let routine = a.s("name");

                if a.s("action") == "list" {
                    return Ok(json!(
                        object(&routines::load(&h, &org)?)
                            .into_iter()
                            .map(|(n, mut r)| {
                                r["routine"] = json!(n);

                                r
                            })
                            .collect::<Vec<_>>()
                    ));
                }

                ensure!(
                    !routine.is_empty(),
                    "usage: reckon routine {} <name>",
                    a.s("action")
                );

                return match a.s("action") {
                    "remove" => routines::remove(&h, &org, routine),
                    "install" => routines::install(&h, &org, routine),
                    _ => routines::run(&h, &org, routine, &registry),
                };
            }

            ensure!(
                a.passthrough.is_empty(),
                "only reckon watch add takes a read after --"
            );

            if a.s("action") == "list" {
                return Ok(json!(
                    object(&watches::load(&h, &org)?)
                        .into_iter()
                        .map(|(n, mut r)| {
                            r["watch"] = json!(n);

                            r
                        })
                        .collect::<Vec<_>>()
                ));
            }

            if a.s("action") == "remove" {
                ensure!(a.vs("name").len() == 1, "usage: reckon watch remove <name>");

                return watches::remove(&h, &org, a.s("name"));
            }

            return Ok(json!(watches::run(&h, &org, &registry, &a.vs("name"))?));
        }

        "spend" => {
            let days = a.i("days")?;

            ensure!((1..=365).contains(&days), "--days must be 1-365");

            ensure!(
                a.opt("set_budget").is_some()
                    || !std::env::args()
                        .any(|s| s == "--budget-days" || s.starts_with("--budget-days=")),
                "--budget-days goes with --set-budget"
            );

            if a.opt("set_budget").is_some() {
                ensure!(
                    !a.flag("all_orgs"),
                    "set a budget for one org at a time (--org)"
                );

                ensure!(
                    !activity::pinned(),
                    "budgets are for the operator's own terminal, not a pinned agent"
                );

                let tokens = a.u("set_budget")?;

                let days = a.u("budget_days")?;

                ensure!(
                    (1..=365).contains(&days),
                    "--set-budget takes 0 or more tokens and --budget-days 1-365"
                );

                let org = a.opt("org").map(str::to_owned).unwrap_or(
                    orgs::context(&h, None, None, &registry)
                        .map(|c| c.org)
                        .unwrap_or_default(),
                );

                return spend::set_budget(&h, &org, tokens, days);
            }

            if a.flag("all_orgs") {
                ensure!(
                    !activity::pinned(),
                    "a pinned agent sees only its own org; run reckon spend --all-orgs in your own terminal"
                );

                return Ok(json!(
                    orgs::orgs(&h)?
                        .iter()
                        .map(|o| spend::report(&h, o, days))
                        .collect::<Result<Vec<_>>>()?
                ));
            }

            let org = if activity::pinned() {
                orgs::context(&h, a.opt("org"), None, &registry)?.org
            } else if let Some(org) = a.opt("org") {
                org.into()
            } else {
                orgs::context(&h, None, None, &registry)?.org
            };

            orgs::load(&h, &org, &registry)?;

            return Ok(json!([spend::report(&h, &org, days)?]));
        }

        "brief" if a.flag("all_orgs") => {
            let days = a.i("days")?;

            ensure!((1..=365).contains(&days), "--days must be 1-365");

            ensure!(
                a.opt("org").is_none() && a.opt("env").is_none(),
                "--all-orgs covers every org; drop --org and --env"
            );

            let mut result = brief::all_orgs(&h, &registry, days)?;

            if a.flag("save") {
                let target = local_path(
                    &h,
                    format!("briefs/{}-all.md", chrono::Utc::now().format("%Y-%m-%d")),
                )?;

                write(&target, format!("{}\n", brief::render_all(&result)))?;

                result["saved"] = json!(target);
            }

            return Ok(result);
        }

        _ => {}
    }

    let ctx = context(&h, a, &registry, true)?;

    let root = orgs::workspace(&h, &ctx.org)?;

    match name{

    "login"=>{
ensure!(std::io::stdin().is_terminal(),"reckon login is interactive; run it yourself in a terminal");
let p=connectors::prepare(&h,&ctx,a.s("connection"),&registry,true,None)?;
ensure!(truth(&p.spec["login"]),"{} has no login command; put its settings in {}",text(&p.conn["provider"]),orgs::secrets_path(&h,&ctx.org,&p.connection)?.display());
let argv=connectors::command(&h,&ctx,&p,&strings(&p.spec["login"]))?;
let code=std::process::Command::new(&argv[0]).args(&argv[1..]).env_clear().envs(&p.env).current_dir(&p.env["TMPDIR"]).status()?.code().unwrap_or(1);
Ok(json!({
"connection":p.connection,"exit_code":code}
))}
,
    "status"=>Ok(json!({
"org":ctx.org,"name":ctx.data["name"],"environment":ctx.env,"production":ctx.production,"resolved_from":ctx.source,"workspace":root,"connections":described(&h,&ctx,&registry)}
)),"preflight"=>preflight(&h,&ctx,&registry),
    "verify"=>{
let rows=connectors::verify(&h,&ctx,&registry,&a.vs("connections"))?;
remember(&h,&ctx,&rows);
Ok(json!(rows))}
,
    "log"=>{
let(days,limit)=(a.i("days")?,a.u("limit")?);
ensure!((1..=365).contains(&days)&&(1..=1000).contains(&limit),"--days must be 1-365 and --limit 1-1000");
ensure!(!a.flag("all_envs")||!activity::pinned(),"a pinned agent sees only its own environment's activity");
Ok(json!(activity::read(&h,&ctx.org,days,if a.flag("all_envs"){
None}
else{
Some(&ctx.env)}
,a.opt("event"),limit as usize)?))}
,
    "run"=>{
ensure!(!a.passthrough.is_empty(),"usage: reckon run <connection|tool> -- <arguments>");
ensure!(a.opt("label").is_none()||a.opt("session").is_some(),"--label needs --session");
let words=activity::words(&ctx,a.s("target"),&a.passthrough,&registry);
let outcome=(||->Result<connectors::Outcome>{
if let Some(sid)=a.opt("session"){
let(path,data)=session_path(&root,sid)?;
investigation::matching(&data,&ctx)?;
investigation::spend(&path,&data,1)?;
}
let stdin=if a.flag("stdin"){
let mut bytes=vec![];
std::io::stdin().take(1048577).read_to_end(&mut bytes)?;
ensure!(bytes.len()<=1048576,"standard input over 1 MiB; pass a file argument instead");
Some(bytes)}
else{
None}
;
let cache=if let Some(age)=a.opt("cache"){
let unit=match age.chars().last(){
Some('s')=>1,Some('m')=>60,Some('h')=>3600,Some('d')=>86400,_=>bail!("--cache takes a number and s, m, h or d, e.g. 15m")}
;
age[..age.len()-1].parse::<u64>().map_err(|_|anyhow!("--cache takes a number and s, m, h or d, e.g. 15m"))?.checked_mul(unit).ok_or_else(||anyhow!("cache age too large"))?}
else{
0}
;
if activity::pinned()&&std::env::var("RECKON_RUNTIME").unwrap_or_default()!="claude"
&& let Ok((_,conn))=orgs::resolve(&ctx,a.s("target")){
ensure!(text(&registry[text(&conn["provider"])]["mode"])!="approval","{} runs only after you approve each command, which needs Claude Code (reckon open {}/{} --runtime claude); otherwise run it yourself in your own terminal",a.s("target"),ctx.org,ctx.env);
}
connectors::run(&h,&ctx,a.s("target"),&a.passthrough,&registry,connectors::RunOptions{timeout:a.f("timeout")?,max_bytes:a.u("max_bytes")? as usize,stdin:stdin.as_deref(),cache_seconds:cache})}
)();
let outcome=match outcome{
Ok(o)=>o,Err(e)=>{
activity::record(&h,&ctx,"refused",json!({
"target":a.s("target"),"command":words,"args":a.passthrough,"session":a.opt("session"),"detail":e.to_string()}
));
return Err(e);
}
}
;
if let Some(note)=&outcome.quota_note{
eprintln!("reckon: {note}");
}
if let Some(at)=&outcome.cached_at{
eprintln!("reckon: cached result observed at {at}; drop --cache for a fresh read");
}
std::io::stdout().write_all(orgs::redact(&String::from_utf8_lossy(&outcome.result.stdout),&outcome.values).as_bytes())?;
std::io::stderr().write_all(orgs::redact(&String::from_utf8_lossy(&outcome.result.stderr),&outcome.values).as_bytes())?;
if let Some(sid)=a.opt("session"){
let record=investigation::record_run(&root,sid,&ctx,&outcome,a.opt("label"),a.f("timeout")?,a.u("max_bytes")? as usize)?;
eprintln!("reckon: saved as evidence {} in {sid}",text(&record["id"]));
}
if outcome.result.status!=process::Status::Ok{
eprintln!("reckon: {} {}",outcome.connection,outcome.result.status.name());
}
activity::record(&h,&ctx,"run",json!({
"target":outcome.connection,"command":words,"args":a.passthrough,"session":a.opt("session"),"status":outcome.result.status.name(),"exit_code":outcome.result.exit_code,"cached":outcome.cached_at.as_ref().map(|_|true)}
));
let code=match outcome.result.status{
process::Status::Ok=>0,process::Status::Failed=>outcome.result.exit_code.unwrap_or(1),process::Status::Timeout=>124,process::Status::OutputLimit=>3,process::Status::Unavailable=>127,process::Status::Cancelled=>130}
;
Ok(json!({
"_exit":code}
))}
,
    "debug"|"investigate"|"monitor"|"analyze"=>{
let service=resolve_service(&root,&ctx.env,a.s("service"))?;
question(a.s("question"))?;
let bounds=window(a.s("start"),a.s("end"))?;
let draft=json!({
"service_context":service,"service":service["id"],"window":bounds,"playbook":a.s("playbook")}
);
let(tasks,gaps)=operations::plan(&draft)?;
for t in &tasks{
operations::build(text(&t["operation"]),&t["params"],&t["window"],&root)?;
}
let(path,data)=create(&root,&ctx.org,&ctx.env,a.s("question"),name,draft)?;
let sid=text(&data["id"]);
let collection=if a.flag("collect"){
let records=investigation::collect(&h,&ctx,&root,sid,investigation::CollectOptions{operation:None,params:json!({
}
),refresh:false,timeout:15.0,max_bytes:1048576},&registry)?;
let successful=records.iter().filter(|r|["ok","reused"].contains(&text(&r["status"]))).count();
json!({
"successful":successful,"attempted":records.len(),"partial":records.is_empty()||successful!=records.len()}
)}
else{
Value::Null}
;
Ok(json!({
"session":sid,"org":ctx.org,"environment":ctx.env,"path":path,"report":investigation::report(&root,sid)?,"planned_operations":tasks.len(),"gaps":gaps,"collection":collection,"next":format!("reckon resume {sid}")}
))}
,
    "task"=>{
let mut recipe=Value::Null;
let mut playbook=Value::Null;
let mut content=a.s("question").to_owned();
let mut area=a.opt("area").map(|s|json!(s)).unwrap_or(Value::Null);
let mut budget=a.opt("budget").map(|_|a.u("budget").map(|n|json!(n))).transpose()?.unwrap_or(Value::Null);
if let Some(flow)=a.opt("workflow"){
let start=workflows::start(&root,flow,&parse_params(&a.vs("param"))?)?;
if content.is_empty(){
content=start.question;
}
if area.is_null(){
area=start.area;
}
if budget.is_null(){
budget=start.budget;
}
recipe=start.snapshot;
playbook=start.playbook;
}
else{
ensure!(a.vs("param").is_empty(),"--param needs --workflow");
}
ensure!(!content.is_empty(),"task needs --question or --workflow");
question(&content)?;
ensure!(a.opt("start").is_some()==a.opt("end").is_some(),"give both --from and --to, or neither");
if !area.is_null(){
orgs::name(text(&area),"area")?;
}
ensure!(budget.is_null()||budget.as_u64().is_some_and(|n|(1..=1000).contains(&n)),"--budget must be 1-1000");
let bounds=if a.opt("start").is_some(){
window(a.s("start"),a.s("end"))?}
else{
Value::Null}
;
let previous=if recipe.is_null(){
Value::Null}
else{
workflows::previous(&root,&ctx.env,&recipe)}
;
let mut extra=json!({
"area":area,"budget":budget,"window":bounds,"playbook":playbook}
);
if !recipe.is_null(){
extra["workflow"]=recipe.clone();
}
if !previous.is_null(){
extra["previous"]=previous.clone();
}
let(path,data)=create(&root,&ctx.org,&ctx.env,&content,"task",extra)?;
let sid=text(&data["id"]);
let mut result=json!({
"session":sid,"org":ctx.org,"environment":ctx.env,"path":path,"report":investigation::report(&root,sid)?,"next":format!("reckon run <connection> --session {sid} -- <arguments>")}
);
if !previous.is_null(){
result["previous"]=investigation::summary(&root,&previous);
result["start_with"]=json!(format!("the last review's actions: for each, record what happened with reckon note {sid} --kind finding --follows <action-note-id> --evidence <ids>"));
}
if !recipe.is_null(){
let mut checks=array(&recipe["checks"]).iter().map(|c|format!("{}: {}",text(&c["id"]),text(&c["text"]))).collect::<Vec<_>>();
if truth(&previous["actions"]){
checks.push("follow-up: each action from the last review".into());
}
result["checks"]=json!(checks);
result["done_when"]=json!(format!("reckon check {sid} shows no check left to do; skip one with reckon note {sid} --kind question --text \"skip <check>: <reason>\""));
}
Ok(result)}
,
    "workflows"=>Ok(json!(object(&workflows::recipes(&root)?).values().map(|r|json!({
"workflow":r["id"],"title":r["title"],"area":r["area"],"budget":r["budget"],"params":object(&r["params"]).iter().map(|(k,v)|(k.clone(),json!(format!("{}{}",text(&v["help"]),if truth(&v["default"]){
format!(" (default {})",text(&v["default"]))}
else{
String::new()}
)))).collect::<serde_json::Map<_,_>>(),"checks":array(&r["checks"]).iter().map(|c|c["id"].clone()).collect::<Vec<_>>(),"source":r["source"]}
)).collect::<Vec<_>>())),
    "watch"=>{
ensure!(a.vs("name").len()==1&&a.opt("connection").is_some(),"reckon watch add <name> --connection <conn> --items <path> --key <field> -- <read>");
watches::add(&h,&ctx,a.s("name"),watches::WatchOptions{target:a.s("connection"),args:&a.passthrough,items:a.s("items"),key:a.s("key"),title:a.opt("title"),production:a.flag("production")},&registry)}
,
    "inbox"=>{
ensure!(!a.flag("all_envs")||!activity::pinned(),"a pinned agent sees only its own environment's inbox");
let days=a.i("days")?;
ensure!((1..=365).contains(&days),"--days must be 1-365");
Ok(json!(watches::inbox(&h,&ctx.org,days,if a.flag("all_envs"){
None}
else{
Some(&ctx.env)}
)?))}
,
    "routine"=>{
ensure!(a.opt("name").is_some()&&a.opt("workflow").is_some()&&a.opt("at").is_some(),"reckon routine add <name> --workflow <id> --at <when> [--param k=v]");
routines::add(&h,&ctx,a.s("name"),routines::RoutineOptions{workflow:a.s("workflow"),params:parse_params(&a.vs("param"))?,at:a.s("at"),timeout_minutes:a.u("timeout_minutes")?,production:a.flag("production")})}
,
    "services"=>{
if a.s("action")=="show"{
ensure!(a.opt("name").is_some(),"services show requires a name");
return resolve_service(&root,&ctx.env,a.s("name"));
}
let entries=services(&root,&ctx.env)?;
if a.s("action")=="validate"{
for service in &entries{
for t in operations::plan(&json!({
"service_context":service,"service":service["id"],"playbook":"latency","window":{
"from":"2026-01-01T00:00:00Z","to":"2026-01-01T00:30:00Z"}
}
))?.0{
operations::build(text(&t["operation"]),&t["params"],&t["window"],&root)?;
}
}
Ok(json!({
"org":ctx.org,"environment":ctx.env,"valid_services":entries.len()}
))}
else{
Ok(json!(entries))}
}
,
    "capabilities"=>{
let mut plain=ctx.clone();
plain.link=Value::Null;
let rows=operations::OPERATIONS.iter().map(|(op,provider,capability,required,optional,_)|{
let problem=investigation::source(&h,&plain,provider,&registry);
json!({
"operation":op,"provider":provider,"capability":capability,"required":required.split_whitespace().collect::<Vec<_>>(),"optional":optional.split_whitespace().collect::<Vec<_>>(),"readiness":if *provider=="git"{
"local"}
else if problem.is_empty(){
"ready"}
else{
&problem}
}
)}
).collect::<Vec<_>>();
Ok(json!({
"org":ctx.org,"environment":ctx.env,"live_verified":false,"operations":rows}
))}
,
    "collect"=>{
ensure!(a.s("params")=="{}"||a.opt("operation").is_some(),"--params requires --operation");
let rows=investigation::collect(&h,&ctx,&root,a.s("session"),investigation::CollectOptions{operation:a.opt("operation"),params:serde_json::from_str(a.s("params"))?,refresh:a.flag("refresh"),timeout:a.f("timeout")?,max_bytes:a.u("max_bytes")? as usize},&registry)?;
investigation::report(&root,a.s("session"))?;
Ok(json!(rows))}
,
    "resume"|"report"|"note"|"promote"|"check"|"close"=>{
let sid=a.s("session");
let(path,mut data)=session_path(&root,sid)?;
if activity::pinned()||name=="check"{
investigation::matching(&data,&ctx)?;
}
match name{
"resume"=>investigation::resume(&root,sid),"report"=>Ok(json!({
"report":investigation::report(&root,sid)?}
)),"check"=>investigation::check(&root,sid,&available(&h,&ctx,&registry),&registry),"note"=>{
if let Some(id)=a.opt("withdraw"){
investigation::withdraw(&root,sid,id,a.s("text"))}
else{
ensure!(a.opt("kind").is_some(),"note needs --kind, or --withdraw <note-id>");
investigation::note(&root,sid,a.s("kind"),a.s("text"),&a.vs("evidence"),a.s("next_check"),&a.vs("follows"))}
}
,"promote"=>Ok(json!({
"incident":investigation::promote(&root,sid,a.s("slug"))?,"note":"Preliminary snapshot; complete RCA.md using the RCA template."}
)),_=>{
let _lock=lock(&path)?;
data=session_path(&root,sid)?.1;
if a.s("outcome")=="diagnosed"{
ensure!(investigation::live(&data["notes"]).iter().any(|n|text(&n["kind"])=="finding"),"record an evidence-backed finding before marking the investigation diagnosed");
}
if a.s("outcome")=="done"&&truth(&data["workflow"]){
investigation::matching(&data,&ctx)?;
let checked=investigation::check(&root,sid,&available(&h,&ctx,&registry),&registry)?;
ensure!(checked.get("workflow").is_some(),"cannot close as done: {}",strings(&checked["problems"]).join("; "));
let todo=strings(&checked["workflow"]["todo"]);
ensure!(!todo.contains(&"report-actions".into()),"report-actions: the recorded actions are outside the workflow's limit; merge or withdraw some with reckon note {sid} --withdraw <note-id> --text \"<why>\"");
ensure!(todo.is_empty(),"workflow checks still to do: {}; read them, or record why with reckon note {sid} --kind question --text \"skip <check>: <reason>\"",todo.join(", "));
}
data["status"]=json!(a.s("outcome"));
data["closed_at"]=json!(now());
write_json(&path.join("session.json"),&data)?;
Ok(json!({
"session":sid,"status":a.s("outcome")}
))}
}
}
,
    "history"=>{
let limit=a.u("limit")?;
ensure!((1..=200).contains(&limit),"history limit must be 1–200");
ensure!(!a.flag("all_envs")||!activity::pinned(),"a pinned agent sees only its own environment's history");
Ok(json!(investigation::history(&root,if a.flag("all_envs"){
None}
else{
Some(&ctx.env)}
,a.opt("service"),a.opt("query"),limit as usize)?))}
,
    "brief"=>{
let days=a.i("days")?;
ensure!((1..=365).contains(&days),"--days must be 1-365");
ensure!(!a.flag("all_envs")||!activity::pinned(),"a pinned agent sees only its own environment; run reckon brief --all-envs in your own terminal");
let envs=if a.flag("all_envs"){
strings(&ctx.data["environments"])}
else{
vec![]}
;
let mut result=brief::brief(&h,&ctx,&registry,days,&envs)?;
if a.flag("save"){
let target=local_path(&root,format!("briefs/{}-{}.md",chrono::Utc::now().format("%Y-%m-%d"),text(&result["environment"])))?;
write(&target,format!("{}\n",brief::render(&result)))?;
result["saved"]=json!(target);
}
Ok(result)}
,
    "act"=>match a.s("action"){
"list"=>Ok(json!(actions::listing(&root)?.into_iter().filter(|r|!activity::pinned()||text(&r["environment"])==ctx.env).collect::<Vec<_>>())),"show"=>{
let record=actions::load(&root,a.s("target"))?.1;
ensure!(!activity::pinned()||text(&record["environment"])==ctx.env,"action {} belongs to {}; a pinned agent sees only {}",text(&record["id"]),text(&record["environment"]),ctx.env);
Ok(record)}
,"reconcile"=>{
let done=actions::reconcile(&h,&ctx,&root,a.s("target"),&registry)?;
activity::record(&h,&ctx,"act.reconcile",json!({
"target":done["connection"],"action":done["id"],"status":done["status"]}
));
Ok(done)}
,_=>{
let mut params:Value=serde_json::from_str(a.s("params"))?;
ensure!(params.is_object(),"write parameters must be an object");
if a.flag("body_stdin"){
let mut bytes=vec![];
std::io::stdin().take(65537).read_to_end(&mut bytes)?;
let body=String::from_utf8(bytes)?;
params[if a.s("target").starts_with("jira."){
"description"}
else{
"body"}
]=json!(body);
}
if let Some(sid)=a.opt("session"){
investigation::matching(&session_path(&root,sid)?.1,&ctx)?;
}
let(record,preview)=actions::prepare(&h,&ctx,&root,a.s("target"),params,&registry,a.opt("session"))?;
activity::record(&h,&ctx,"act.prepare",json!({
"target":record["connection"],"action":record["id"],"operation":record["operation"]}
));
eprintln!("{preview}");
Ok(json!({
"action":record["id"],"status":record["status"],"next":format!("the operator reviews and runs: reckon act apply {}",text(&record["id"]))}
))}
}
,
    _=>bail!("unsupported command: {name}")}
}

fn checked(row: &Value) -> String {
    let found = &row["verified"];

    if found.is_null() {
        return String::new();
    }

    format!(
        "  last verify: {} {}Z{}",
        text(&found["status"]),
        text(&found["at"]).get(..16).unwrap_or("").replace('T', " "),
        if truth(&found["detail"]) && text(&found["status"]) != "ok" {
            format!(
                ": {}",
                text(&found["detail"]).chars().take(80).collect::<String>()
            )
        } else {
            String::new()
        }
    )
}

pub fn display(command: &str, r: &Value) -> Result<()> {
    match command {
        "link" if r.get("links").is_some() => {
            for row in array(&r["links"]) {
                println!(
                    "{}{}  ->  {}/{}{}",
                    if row["path"] == r["here"] { "* " } else { "  " },
                    text(&row["path"]),
                    text(&row["org"]),
                    text(&row["env"]),
                    if truth(&row["use"]) {
                        format!("  prefers {}", strings(&row["use"]).join(", "))
                    } else {
                        String::new()
                    }
                );
            }
            if array(&r["links"]).is_empty() {
                println!(
                    "no folder links yet: reckon link <org>/<env> [<folder>] [--use <connection>]"
                );
            }
        }
        "link" | "unlink" => println!(
            "{command}ed {}  ->  {}/{}{}",
            text(&r["path"]),
            text(&r["org"]),
            text(&r["env"]),
            if command == "link" && truth(&r["use"]) {
                format!("  prefers {}", strings(&r["use"]).join(", "))
            } else {
                String::new()
            }
        ),
        "connect" if r.is_array() => {
            for row in array(r) {
                println!(
                    "{:15}{:24}{:10}{}",
                    text(&row["provider"]),
                    text(&row["title"]),
                    text(&row["mode"]),
                    strings(&row["settings"]).join(", ")
                );
            }
        }
        "watch" if r.is_array() && r.get(0).is_some_and(|v| v.get("status").is_some()) => {
            for row in array(r) {
                println!(
                    "{:9}{:20}{}",
                    text(&row["status"]),
                    text(&row["watch"]),
                    if row.get("new").is_some() {
                        format!("{} new of {}", row["new"], row["items"])
                    } else {
                        text(&row["detail"]).into()
                    }
                );
            }
        }
        "status" | "preflight" => {
            println!(
                "{} · {}{}  (from {})",
                text(&r["org"]),
                text(&r["environment"]),
                if truth(&r["production"]) {
                    " (PRODUCTION)"
                } else {
                    ""
                },
                text(&r["resolved_from"])
            );

            println!("workspace: {}", text(&r["workspace"]));

            for row in array(&r["connections"]) {
                println!(
                    "  {:22}{:14}{}{}{}{}{}",
                    text(&row["connection"]),
                    text(&row["provider"]),
                    text(&row["state"]),
                    if truth(&row["label"]) {
                        format!(" label={}", text(&row["label"]))
                    } else {
                        String::new()
                    },
                    if truth(&row["account"]) {
                        format!(" account={}", text(&row["account"]))
                    } else {
                        String::new()
                    },
                    if command == "status" && !truth(&row["applies"]) {
                        format!("  [{} only]", text(&row["scope"]))
                    } else {
                        String::new()
                    },
                    checked(row)
                );
            }

            if array(&r["connections"]).is_empty() {
                println!("  no connections yet: reckon connect <name> --provider <tool>");
            }

            if command == "preflight" {
                if truth(&r["other_environments"]) {
                    println!(
                        "other environments: {}",
                        strings(&r["other_environments"]).join(", ")
                    );
                }

                println!(
                    "knowledge: {}",
                    object(&r["knowledge"])
                        .iter()
                        .map(|(k, v)| format!("{k} {v} files"))
                        .collect::<Vec<_>>()
                        .join(" · ")
                );

                for s in array(&r["open_sessions"]) {
                    println!("open session: {}  {}", text(&s["id"]), text(&s["question"]));
                }

                println!(
                    "incidents: {}{}",
                    r["incidents"]["count"],
                    if truth(&r["incidents"]["latest"]) {
                        format!(" · latest {}", text(&r["incidents"]["latest"]))
                    } else {
                        String::new()
                    }
                );

                for w in strings(&r["warnings"]) {
                    println!("warning: {w}");
                }

                println!("note: {}", text(&r["note"]));
            }
        }

        "brief" => {
            println!(
                "{}",
                if r.get("orgs").is_some() {
                    brief::render_all(r)
                } else {
                    brief::render(r)
                }
            );

            if truth(&r["saved"]) {
                println!("Saved: {}", text(&r["saved"]));
            }
        }

        "doctor" => {
            println!("home: {}", text(&r["home"]));

            for c in strings(&r["checks"]) {
                println!("  {c}");
            }

            for p in strings(&r["problems"]) {
                println!("  problem: {p}");
            }

            println!(
                "{}",
                if truth(&r["problems"]) {
                    format!("{} problem(s)", array(&r["problems"]).len())
                } else {
                    "healthy".into()
                }
            );
        }

        "verify" => {
            for row in array(r) {
                println!(
                    "{:<8}{:22}{}",
                    text(&row["status"]),
                    text(&row["connection"]),
                    text(&row["detail"]).chars().take(200).collect::<String>()
                );
            }

            if array(r).is_empty() {
                println!("no connections apply to this environment");
            }
        }

        "check" => {
            println!(
                "{}  {}",
                if truth(&r["ok"]) { "ok" } else { "problems" },
                text(&r["session"])
            );

            for key in ["problems", "warnings"] {
                for item in strings(&r[key]) {
                    println!(
                        "  {}: {item}",
                        if key == "problems" {
                            "problem"
                        } else {
                            "warning"
                        }
                    );
                }
            }

            if truth(&r["workflow"]) {
                println!("workflow {}:", text(&r["workflow"]["workflow"]));

                for row in array(&r["workflow"]["checks"]) {
                    println!(
                        "  {:12}{:16}{}",
                        text(&row["status"]),
                        text(&row["id"]),
                        text(&row["text"])
                    );
                }
            }
        }

        "log" => {
            for row in array(r) {
                let what = row["operation"]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| strings(&row["command"]).join(" "));

                println!(
                    "{}  {:6} {:8} {:13}{:16} {}  {}",
                    text(&row["at"]),
                    text(&row["environment"]),
                    text(&row["actor"]),
                    text(&row["event"]),
                    text(&row["target"]),
                    what,
                    row["status"].as_str().unwrap_or(text(&row["detail"]))
                );
            }

            if array(r).is_empty() {
                println!("no activity in this period");
            }
        }

        "history" => {
            for row in array(r) {
                println!(
                    "{}  {}  {}",
                    text(&row["id"]),
                    text(&row["environment"]),
                    text(&row["question"])
                );
            }

            if array(r).is_empty() {
                println!("No matching saved sessions.");
            }
        }

        "collect" => {
            for row in array(r) {
                println!(
                    "{}  {}  {}",
                    text(&row["id"]),
                    text(&row["status"]),
                    text(&row["label"])
                );
            }
        }

        "capabilities" => {
            println!(
                "{} · {} (local readiness; connectivity not verified)",
                text(&r["org"]),
                text(&r["environment"])
            );

            for row in array(&r["operations"]) {
                println!(
                    "  {:28}{}",
                    text(&row["operation"]),
                    text(&row["readiness"])
                );
            }
        }

        "resume" => {
            let s = &r["session"];

            println!(
                "{} · {}/{} · {} · {}",
                text(&s["id"]),
                text(&s["org"]),
                text(&s["environment"]),
                s["service"].as_str().unwrap_or(text(&s["mode"])),
                text(&s["status"])
            );

            println!("{}", text(&s["question"]));

            if truth(&s["window"]) {
                println!(
                    "{} → {}",
                    text(&s["window"]["from"]),
                    text(&s["window"]["to"])
                );
            }

            for e in array(&r["evidence"]) {
                println!(
                    "  {}  {}  {}: {}",
                    text(&e["id"]),
                    text(&e["status"]),
                    text(&e["label"]),
                    text(&e["summary"])
                );
            }

            for n in array(&s["notes"]) {
                println!("  {}: {}", text(&n["kind"]), text(&n["text"]));
            }

            for gap in strings(&r["gaps"]) {
                println!("  Gap: {gap}");
            }

            if truth(&r["previous"]) {
                let p = &r["previous"];

                println!(
                    "Last review {} ({}):",
                    text(&p["session"]),
                    text(&p["status"])
                );

                for action in array(&p["actions"]) {
                    println!(
                        "  action {}: {}",
                        text(&action["id"]),
                        text(&action["text"])
                    );
                }

                for done in array(&p["writes"]) {
                    println!(
                        "  write {}  {} {}",
                        text(&done["action"]),
                        text(&done["status"]),
                        text(&done["url"])
                    );
                }
            }
        }

        "workflows" => {
            for row in array(r) {
                println!("{:18}{}", text(&row["workflow"]), text(&row["title"]));

                for (k, v) in object(&row["params"]) {
                    println!("  --param {k}=  {}", text(&v));
                }

                println!("  checks: {}", strings(&row["checks"]).join(", "));
            }
        }

        "spend" => {
            for row in array(r) {
                if row.get("models").is_none() {
                    let budget = &row["agent_token_budget"];
                    println!(
                        "{}: budget {}",
                        text(&row["org"]),
                        if budget.is_null() {
                            "cleared".into()
                        } else {
                            format!(
                                "{} tokens per {} days",
                                number(&budget["tokens"]),
                                budget["days"]
                            )
                        }
                    );
                    continue;
                }
                let mut cost = row["usd"]
                    .as_f64()
                    .map(|n| format!("  ~${n:.2}"))
                    .unwrap_or_default();
                if truth(&row["budget"]) {
                    cost.push_str(&format!(
                        "  (budget {} per {} days)",
                        number(&row["budget"]["tokens"]),
                        row["budget"]["days"]
                    ));
                }
                println!(
                    "{:18}{:>16} tokens in {} days{cost}",
                    text(&row["org"]),
                    number(&row["tokens"]),
                    row["days"]
                );
                for model in array(&row["models"]) {
                    println!(
                        "  {:34}in {}  out {}  cache read {}  cache write {}",
                        format!("{} {}", text(&model["runtime"]), text(&model["model"])),
                        number(&model["input"]),
                        number(&model["output"]),
                        number(&model["cache_read"]),
                        number(&model["cache_write"])
                    );
                }
                if truth(&row["unpriced_models"]) && truth(&row["models"]) {
                    println!(
                        "  no price for {} (add it to $RECKON_HOME/prices.json for an estimate)",
                        strings(&row["unpriced_models"]).join(", ")
                    );
                }
            }
        }
        "inbox" => {
            for row in array(r) {
                println!(
                    "{}  [{}] {:18}{}",
                    text(&row["at"]).get(..16).unwrap_or(""),
                    text(&row["environment"]),
                    text(&row["watch"]),
                    text(&row["title"])
                );
            }

            if array(r).is_empty() {
                println!("nothing new");
            }
        }

        _ if r.is_object() && r.get("report").is_some() => {
            if truth(&r["synthetic"]) {
                println!("Synthetic demo completed: no provider connections.");
            }

            for key in ["session", "org", "environment"] {
                if r.get(key).is_some() {
                    println!("{key}: {}", text(&r[key]));
                }
            }

            if truth(&r["collection"]) {
                println!(
                    "Collection: {}/{} successful{}",
                    r["collection"]["successful"],
                    r["collection"]["attempted"],
                    if truth(&r["collection"]["partial"]) {
                        " (partial; inspect report gaps)"
                    } else {
                        ""
                    }
                );
            }

            println!("Report: {}", text(&r["report"]));

            if let Some(next) = r["next"].as_str().or_else(|| r["resume"].as_str()) {
                println!("Next: {next}");
            }
        }

        _ => println!("{}", serde_json::to_string_pretty(r)?),
    }

    Ok(())
}

pub fn exit_code(command: &str, r: &Value) -> i32 {
    if let Some(n) = r["_exit"].as_i64() {
        return if n < 0 { 128 + (-n as i32) } else { n as i32 };
    }

    if command == "verify"
        && array(r)
            .iter()
            .any(|v| !["ok", "skipped"].contains(&text(&v["status"])))
    {
        return 1;
    }

    if command == "collect"
        && array(r)
            .iter()
            .any(|v| !["ok", "reused"].contains(&text(&v["status"])))
    {
        return 2;
    }

    if truth(&r["collection"]["partial"]) {
        return 2;
    }

    if ["doctor", "check"].contains(&command) && truth(&r["problems"]) {
        return 1;
    }

    if command == "act" {
        return match text(&r["status"]) {
            "unknown" => 2,
            "failed" | "cancelled" => 1,
            _ => 0,
        };
    }

    0
}

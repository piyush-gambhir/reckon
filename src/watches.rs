use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
pub fn load(h: &Path, org: &str) -> Result<Value> {
    let p = local_path(&orgs::org_path(h, org)?, "watches.json")?;
    if p.is_file() {
        let data = read_json(&p)?;
        ensure!(
            data.is_object()
                && object(&data)
                    .iter()
                    .all(|(name, watch)| orgs::valid_name(name) && watch.is_object()),
            "watches.json must map watch names to objects"
        );
        Ok(data)
    } else {
        Ok(json!({}))
    }
}
pub struct WatchOptions<'a> {
    pub target: &'a str,
    pub args: &'a [String],
    pub items: &'a str,
    pub key: &'a str,
    pub title: Option<&'a str>,
    pub production: bool,
}
pub fn add(
    h: &Path,
    ctx: &Context,
    name: &str,
    options: WatchOptions<'_>,
    registry: &Registry,
) -> Result<Value> {
    let WatchOptions {
        target,
        args,
        items,
        key,
        title,
        production,
    } = options;

    orgs::name(name, "watch")?;
    ensure!(
        !ctx.production || production,
        "{}/{} is production; a watch there runs unattended, so pass --production to confirm",
        ctx.org,
        ctx.env
    );
    for (label, value) in [
        ("--items", items),
        ("--key", key),
        ("--title", title.unwrap_or("")),
    ] {
        ensure!(
            value.is_empty() || goals::valid_path(value),
            "{label} is a dotted path such as data or result.items"
        );
    }
    ensure!(
        !args.is_empty(),
        "give the read after --, e.g. -- disputes list --limit 20"
    );
    let (cname, conn) = orgs::resolve(ctx, target)?;
    let provider = text(&conn["provider"]);
    let spec = &registry[provider];
    ensure!(
        text(&spec["mode"]) != "approval",
        "{provider} asks before every run; it cannot be watched unattended"
    );
    connectors::check(provider, spec, args)?;
    let _registry_lock = lock(&local_path(
        &orgs::org_path(h, &ctx.org)?,
        "runtime/watch-registry",
    )?)?;
    let mut watches = load(h, &ctx.org)?;
    ensure!(
        watches.get(name).is_none(),
        "watch {name} exists; remove it first"
    );
    let record = json!({"connection":cname,"environment":ctx.env,"args":args,"items":items,"key":key,"title":title,"created_at":now(),"production_approved":production});
    watches[name] = record.clone();
    write_json(
        &local_path(&orgs::org_path(h, &ctx.org)?, "watches.json")?,
        &watches,
    )?;
    let mut record = record;
    record["watch"] = json!(name);
    Ok(record)
}
pub fn remove(h: &Path, org: &str, name: &str) -> Result<Value> {
    orgs::name(name, "watch")?;
    let _registry_lock = lock(&local_path(
        &orgs::org_path(h, org)?,
        "runtime/watch-registry",
    )?)?;
    // Keep the lock outside the state directory, which removal deletes.
    let _watch_lock = lock(&local_path(
        &orgs::org_path(h, org)?,
        format!("runtime/watch-locks/{name}"),
    )?)?;
    let mut watches = load(h, org)?;
    ensure!(watches.get(name).is_some(), "unknown watch: {name}");
    watches
        .as_object_mut()
        .ok_or_else(|| anyhow!("invalid watches"))?
        .remove(name);
    write_json(
        &local_path(&orgs::org_path(h, org)?, "watches.json")?,
        &watches,
    )?;
    let folder = local_path(&orgs::org_path(h, org)?, format!("runtime/watches/{name}"))?;
    if folder.exists() {
        std::fs::remove_dir_all(folder)?;
    }
    Ok(json!({"org":org,"removed":name}))
}
pub fn clean(data: &Value, values: &impl orgs::Redactable) -> Value {
    match data {
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| (k.clone(), clean(v, values)))
                .collect(),
        ),
        Value::Array(a) => json!(a.iter().map(|v| clean(v, values)).collect::<Vec<_>>()),
        Value::String(s) => json!(orgs::redact(s, values)),
        _ => data.clone(),
    }
}
fn scalar(v: &Value) -> String {
    v.as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| v.to_string())
}
fn poll(h: &Path, ctx: &Context, name: &str, watch: &Value, registry: &Registry) -> Result<Value> {
    let folder = local_path(
        &orgs::org_path(h, &ctx.org)?,
        format!("runtime/watches/{}", orgs::name(name, "watch")?),
    )?;
    let lock_folder = local_path(
        &orgs::org_path(h, &ctx.org)?,
        format!("runtime/watch-locks/{name}"),
    )?;
    let Some(_lock) = try_lock(&lock_folder)? else {
        return Ok(
            json!({"watch":name,"status":"skipped","items":0,"new":0,"detail":"another run of this watch is going"}),
        );
    };
    ensure!(
        load(h, &ctx.org)?.get(name) == Some(watch),
        "watch {name} changed or was removed before this poll; retry"
    );
    ensure!(
        !ctx.production || watch["production_approved"] == true,
        "watch {name} is in a production environment; remove it and add it again with --production to confirm unattended runs"
    );
    let connection = text(&watch["connection"]);
    ensure!(
        ctx.data["connections"].get(connection).is_some(),
        "{name}: connection {connection} no longer exists; remove the watch and add it again"
    );
    let provider = text(&ctx.data["connections"][connection]["provider"]);
    ensure!(
        text(&registry[provider]["mode"]) != "approval",
        "{provider} asks for approval before every run; it cannot be watched unattended"
    );
    let outcome = connectors::run(
        h,
        ctx,
        connection,
        &strings(&watch["args"]),
        registry,
        connectors::RunOptions {
            timeout: 60.0,
            max_bytes: 8 * 1048576,
            stdin: None,
            cache_seconds: 0,
        },
    )?;
    ensure!(
        outcome.result.status == process::Status::Ok,
        "{name}: the read {} (exit {:?})",
        outcome.result.status.name(),
        outcome.result.exit_code
    );
    let data: Value = serde_json::from_slice(&outcome.result.stdout).map_err(|_| {
        anyhow!("{name}: the read did not return JSON; add the tool's JSON flag to the watch")
    })?;
    let data = clean(&data, &outcome.values);
    let found = pick(&data, text(&watch["items"]));
    ensure!(
        found.is_array(),
        "{name}: no list at {} in the output",
        if text(&watch["items"]).is_empty() {
            "the top level"
        } else {
            text(&watch["items"])
        }
    );
    let found = array(found);
    ensure!(
        found.is_empty()
            || found
                .iter()
                .any(|i| !pick(i, text(&watch["key"])).is_null()),
        "{name}: no item has a {} field; set --key",
        text(&watch["key"])
    );
    let state_path = folder.join("state.json");
    let fingerprint = hash_json(&json!([
        watch["connection"],
        watch["environment"],
        watch["args"],
        watch["items"],
        watch["key"],
        watch["created_at"],
        outcome.source_sha256
    ]));
    let state = if state_path.is_file() {
        read_json(&state_path)?
    } else {
        Value::Null
    };
    let baseline = state.is_null() || text(&state["fingerprint"]) != fingerprint;
    let mut order = if baseline {
        vec![]
    } else {
        strings(&state["seen"])
    };
    let mut fresh = vec![];
    for item in found {
        let id = pick(item, text(&watch["key"]));
        if id.is_null() {
            continue;
        }
        let id = scalar(id);
        if order.contains(&id) {
            continue;
        }
        order.push(id.clone());
        let title = if truth(&watch["title"]) {
            pick(item, text(&watch["title"]))
        } else {
            &Value::Null
        };
        fresh.push(json!({"id":actions::printable(&id).chars().take(200).collect::<String>(),"title":actions::printable(&if title.is_null(){id}else{scalar(title)}.replace('\n'," ")).chars().take(200).collect::<String>()}));
    }
    if !baseline && !fresh.is_empty() {
        let p = local_path(&orgs::workspace(h, &ctx.org)?, "inbox.jsonl")?;
        for item in &fresh {
            append_json(
                &p,
                &json!({"at":now(),"watch":name,"environment":ctx.env,"connection":connection,"id":item["id"],"title":item["title"]}),
            )?;
        }
    }
    write_json(
        &state_path,
        &json!({"fingerprint":fingerprint,"seen":order.iter().skip(order.len().saturating_sub(5000)).collect::<Vec<_>>(),"checked_at":now()}),
    )?;
    Ok(
        json!({"watch":name,"status":if baseline{"baseline"}else{"ok"},"items":found.len(),"new":if baseline{0}else{fresh.len()}}),
    )
}
pub fn run(h: &Path, org: &str, registry: &Registry, names: &[String]) -> Result<Vec<Value>> {
    let watches = load(h, org)?;
    for n in names {
        ensure!(watches.get(n).is_some(), "unknown watch: {n}");
    }
    let mut rows = vec![];
    for (name, watch) in object(&watches) {
        if !names.is_empty() && !names.contains(&name) {
            continue;
        }
        let row = (|| -> Result<Value> {
            let mut ctx = orgs::context(h, Some(org), Some(text(&watch["environment"])), registry)?;
            ctx.link = Value::Null;
            let row = poll(h, &ctx, &name, &watch, registry)?;
            activity::record(
                h,
                &ctx,
                "watch",
                json!({"target":name,"status":row["status"],"new":row["new"]}),
            );
            Ok(row)
        })();
        rows.push(match row {
            Ok(v) => v,
            Err(e) => {
                if let Ok(ctx) =
                    orgs::context(h, Some(org), Some(text(&watch["environment"])), registry)
                {
                    activity::record(
                        h,
                        &ctx,
                        "watch",
                        json!({"target":name,"status":"error","detail":e.to_string()}),
                    );
                }
                json!({"watch":name,"status":"error","detail":e.to_string()})
            }
        });
    }
    Ok(rows)
}
pub fn inbox(h: &Path, org: &str, days: i64, env: Option<&str>) -> Result<Vec<Value>> {
    let path = local_path(&orgs::workspace(h, org)?, "inbox.jsonl")?;
    if !path.is_file() {
        return Ok(vec![]);
    }
    let since = chrono::Utc::now() - chrono::Duration::days(days);
    Ok(json_lines(&path)?
        .filter(|v| {
            instant(text(&v["at"])).is_ok_and(|t| t >= since)
                && env.is_none_or(|e| text(&v["environment"]) == e)
        })
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect())
}

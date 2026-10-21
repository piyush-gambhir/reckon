use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
pub fn load(h: &Path, org: &str) -> Result<Value> {
    let p = local_path(&orgs::org_path(h, org)?, "routines.json")?;
    if p.is_file() {
        read_json(&p)
    } else {
        Ok(json!({}))
    }
}
pub fn schedule(value: &str) -> Result<Value> {
    let value = value.trim().to_lowercase();
    let re = regex::Regex::new(
        r"^(daily|weekdays|sun|mon|tue|wed|thu|fri|sat) ([01]\d|2[0-3]):([0-5]\d)$",
    )?;
    let c = re.captures(&value).ok_or_else(|| {
        anyhow!(
            "--at takes daily HH:MM, weekdays HH:MM or a day like mon HH:MM (24-hour, local time)"
        )
    })?;
    let days = match &c[1] {
        "daily" => (0..7).collect::<Vec<_>>(),
        "weekdays" => (1..6).collect(),
        d => vec![
            ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
                .iter()
                .position(|v| *v == d)
                .unwrap_or(0),
        ],
    };
    Ok(json!({"at":value,"days":days,"hour":c[2].parse::<u32>()?,"minute":c[3].parse::<u32>()?}))
}
pub struct RoutineOptions<'a> {
    pub workflow: &'a str,
    pub params: Value,
    pub at: &'a str,
    pub timeout_minutes: u64,
    pub production: bool,
}
pub fn add(h: &Path, ctx: &Context, name: &str, options: RoutineOptions<'_>) -> Result<Value> {
    let RoutineOptions {
        workflow,
        params,
        at,
        timeout_minutes,
        production,
    } = options;

    orgs::name(name, "routine")?;
    ensure!(
        !ctx.production || production,
        "{}/{} is production; a routine there runs unattended, so pass --production to confirm",
        ctx.org,
        ctx.env
    );
    ensure!(
        object(&params)
            .values()
            .all(|v| !text(v).chars().any(char::is_control)),
        "--param values cannot contain control characters"
    );
    ensure!(
        (5..=240).contains(&timeout_minutes),
        "--timeout-minutes must be 5-240"
    );
    workflows::start(&orgs::workspace(h, &ctx.org)?, workflow, &params)?;
    let mut routines = load(h, &ctx.org)?;
    ensure!(
        routines.get(name).is_none(),
        "routine {name} exists; remove it (and its installed job) first"
    );
    let record = json!({"environment":ctx.env,"workflow":workflow,"params":params,"runtime":"claude","schedule":schedule(at)?,"timeout_minutes":timeout_minutes});
    routines[name] = record.clone();
    write_json(
        &local_path(&orgs::org_path(h, &ctx.org)?, "routines.json")?,
        &routines,
    )?;
    let mut record = record;
    record["routine"] = json!(name);
    record["org"] = json!(ctx.org);
    Ok(record)
}
pub fn remove(h: &Path, org: &str, name: &str) -> Result<Value> {
    let mut routines = load(h, org)?;
    ensure!(routines.get(name).is_some(), "unknown routine: {name}");
    routines
        .as_object_mut()
        .ok_or_else(|| anyhow!("invalid routines"))?
        .remove(name);
    write_json(
        &local_path(&orgs::org_path(h, org)?, "routines.json")?,
        &routines,
    )?;
    Ok(
        json!({"org":org,"removed":name,"note":if cfg!(target_os="macos"){format!("unload and delete its job: com.reckon.{org}.{name} in ~/Library/LaunchAgents")}else{"delete its line from your crontab (crontab -e)".into()}}),
    )
}
pub fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''").replace('%', "\\%"))
}
pub fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&c))
    {
        value.into()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}
pub fn prompt(routine: &Value) -> String {
    let params = object(&routine["params"])
        .iter()
        .map(|(k, v)| format!("--param {}", shell_quote(&format!("{k}={}", text(v)))))
        .collect::<Vec<_>>()
        .join(" ");
    let workflow = text(&routine["workflow"]);
    format!(
        "Scheduled routine. Run the {workflow} workflow for this org and environment.\n1. Start it: reckon task --workflow {workflow}{}\n2. Follow its playbook in the reckon skill (workflows/{workflow}.md). Save every read with --session <id>; use --cache 6h for repeated reads.\n3. Record findings with evidence (reckon note). Writes: only reckon act prepare, for the operator to apply.\n4. For a check you cannot do, record why: reckon note <id> --kind question --text \"skip <check>: <reason>\".\n5. Finish with reckon check <id> and reckon report <id>; close it as done only if check shows nothing to do.\nNobody is watching: do not ask questions, and stop rather than guess.",
        if params.is_empty() {
            String::new()
        } else {
            format!(" {params}")
        }
    )
}
pub fn run(h: &Path, org: &str, name: &str, registry: &Registry) -> Result<Value> {
    orgs::name(name, "routine")?;
    let routines = load(h, org)?;
    let routine = routines
        .get(name)
        .ok_or_else(|| anyhow!("unknown routine: {name}"))?;
    let ctx = orgs::context(h, Some(org), Some(text(&routine["environment"])), registry)?;
    let folder = local_path(&orgs::org_path(h, org)?, format!("routines/{name}"))?;
    let Ok(_lock) = lock(&folder) else {
        activity::record(
            h,
            &ctx,
            "routine",
            json!({"target":name,"status":"skipped","detail":"the previous run is still going"}),
        );
        return Ok(
            json!({"routine":name,"status":"skipped","detail":"the previous run is still going"}),
        );
    };
    let attempt = (|| -> Result<Value> {
        let root = workspace::ensure(h, &ctx, registry)?;
        let binary=std::env::var("RECKON_AGENT_BIN").ok().or_else(||which("claude").map(|p|p.display().to_string())).ok_or_else(||anyhow!("claude is not available to scheduled runs; reinstall with reckon routine install"))?;
        ensure!(
            which(&binary).is_some(),
            "claude is not available to scheduled runs; reinstall with reckon routine install"
        );
        let args = vec![
            binary,
            "-p".into(),
            prompt(routine),
            "--permission-mode".into(),
            "dontAsk".into(),
            "--permission-prompts".into(),
            "none".into(),
            "--strict-mcp-config".into(),
        ];
        let result = process::run_bounded(
            &args,
            Some(&workspace::agent_env(h, &ctx, registry, "claude")),
            Some(&root),
            routine["timeout_minutes"].as_f64().unwrap_or(45.0) * 60.0,
            8 * 1048576,
            None,
        )?;
        let log = local_path(
            &folder,
            format!("{}.log", chrono::Utc::now().format("%Y%m%dT%H%M%S%.6fZ")),
        )?;
        let mut bytes = result.stdout;
        bytes.extend(result.stderr);
        write(&log, bytes)?;
        Ok(
            json!({"routine":name,"org":org,"environment":ctx.env,"status":result.status.name(),"exit_code":if result.status==process::Status::Timeout{None}else{result.exit_code},"log":log}),
        )
    })();
    match attempt {
        Ok(result) => {
            activity::record(
                h,
                &ctx,
                "routine",
                json!({"target":name,"status":result["status"],"exit_code":result["exit_code"]}),
            );
            Ok(result)
        }
        Err(e) => {
            activity::record(
                h,
                &ctx,
                "routine",
                json!({"target":name,"status":"error","detail":e.to_string()}),
            );
            Err(e)
        }
    }
}
fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}
pub fn install(h: &Path, org: &str, name: &str) -> Result<Value> {
    orgs::name(name, "routine")?;
    let routines = load(h, org)?;
    let routine = routines
        .get(name)
        .ok_or_else(|| anyhow!("unknown routine: {name}"))?;
    let reckon = h.join("bin/reckon");
    let reckon = if reckon.exists() {
        reckon
    } else {
        std::env::current_exe()?
    };
    let command = [
        reckon.display().to_string(),
        "routine".into(),
        "run".into(),
        name.into(),
        "--org".into(),
        org.into(),
    ];
    let agent = which("claude")
        .ok_or_else(|| anyhow!("claude is not on PATH; install Claude Code first"))?;
    let env = process::Environment::from([
        (
            "PATH".into(),
            std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".into()),
        ),
        ("RECKON_HOME".into(), h.display().to_string()),
        (
            "RECKON_AGENT_BIN".into(),
            agent.canonicalize()?.display().to_string(),
        ),
    ]);
    let folder = local_path(&orgs::org_path(h, org)?, format!("routines/{name}"))?;
    private_dir(&folder)?;
    let sched = &routine["schedule"];
    if cfg!(target_os = "macos") {
        let label = format!("com.reckon.{org}.{name}");
        let target = orgs::expand_home("~/Library/LaunchAgents").join(format!("{label}.plist"));
        let intervals=array(&sched["days"]).iter().map(|d|format!("<dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{}</integer><key>Minute</key><integer>{}</integer></dict>",sched["hour"],sched["minute"])).collect::<String>();
        let content = format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>{}</string><key>ProgramArguments</key><array>{}</array><key>EnvironmentVariables</key><dict>{}</dict><key>StartCalendarInterval</key><array>{intervals}</array><key>StandardOutPath</key><string>{}</string><key>StandardErrorPath</key><string>{}</string></dict></plist>\n",
            xml(&label),
            command
                .iter()
                .map(|a| format!("<string>{}</string>", xml(a)))
                .collect::<String>(),
            env.iter()
                .map(|(k, v)| format!("<key>{}</key><string>{}</string>", xml(k), xml(v)))
                .collect::<String>(),
            xml(&folder.join("launchd.log").display().to_string()),
            xml(&folder.join("launchd.log").display().to_string())
        );
        std::fs::create_dir_all(target.parent().ok_or_else(|| anyhow!("missing parent"))?)?;
        std::fs::write(&target, content)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o644))?;
        }
        Ok(
            json!({"routine":name,"file":target,"next":format!("launchctl bootstrap gui/{} {}",user_id(),target.display())}),
        )
    } else {
        let line = format!(
            "{} {} * * {} {} {}",
            sched["minute"],
            sched["hour"],
            array(&sched["days"])
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join(","),
            env.iter()
                .map(|(k, v)| format!("{k}={}", quote(v)))
                .collect::<Vec<_>>()
                .join(" "),
            command
                .iter()
                .map(|a| quote(a))
                .collect::<Vec<_>>()
                .join(" ")
        );
        Ok(json!({"routine":name,"crontab":line,"next":"add the crontab line with: crontab -e"}))
    }
}
fn user_id() -> u32 {
    #[cfg(unix)]
    {
        unsafe { libc::getuid() }
    }
    #[cfg(not(unix))]
    {
        0
    }
}

use crate::store::*;
use crate::*;
pub fn tool(name: &str, args: &[String]) -> Result<Value> {
    if args.first().is_some_and(|v| v == "commands") {
        return Ok(json!({"effect":"read","command":name}));
    }
    let get = |flag: &str| {
        args.iter()
            .position(|a| a == flag)
            .and_then(|i| args.get(i + 1))
            .map(String::as_str)
            .unwrap_or("")
    };
    match (
        name,
        args.first().map(String::as_str),
        args.get(1).map(String::as_str),
    ) {
        ("cubeapm", Some("metrics"), _) | ("grafana", Some("datasource"), _) => {
            let start = get("--from");
            let timestamp = instant(start)?.timestamp();
            let query = if name == "cubeapm" {
                args.get(2).map(String::as_str).unwrap_or("")
            } else {
                get("--expr")
            };
            let value = if query.contains("request")
                || (query.contains("count") && !query.contains("latency_sum"))
            {
                20
            } else if start.contains("13:30:00") {
                100
            } else {
                850
            };
            Ok(
                json!({"status":"success","data":{"resultType":"matrix","result":[{"metric":{"service":"checkout","fixture":"synthetic"},"values":[[timestamp,value.to_string()],[timestamp+60,value.to_string()]]}]}}),
            )
        }
        ("cubeapm", Some("logs"), _) => Ok(
            json!({"_time":"2026-09-08T14:03:00Z","_msg":"Synthetic fixture: pricing request timed out","service":"checkout"}),
        ),
        ("grafana", Some("annotation"), _) => Ok(
            json!([{"time":1788876060000u64,"text":"Synthetic deploy checkout #42","tags":["checkout"]}]),
        ),
        ("jenkins", Some("build"), _) => Ok(
            json!([{"number":42,"result":"SUCCESS","timestamp":1788876060000u64,"description":"Synthetic fixture; inspect build 42 to verify deployed revision"}]),
        ),
        _ => bail!("Unsupported synthetic command"),
    }
}
pub fn run(h: &Path) -> Result<Value> {
    ensure!(
        !cfg!(windows),
        "the synthetic executable demo currently requires macOS/Linux or WSL2"
    );
    let home = local_path(
        h,
        format!("demos/{}", &uuid::Uuid::new_v4().simple().to_string()[..10]),
    )?;
    private_dir(&home.join("bin"))?;
    let mut registry = orgs::providers()?;
    let exe = std::env::current_exe()?;
    for binary in ["cubeapm", "grafana", "jenkins"] {
        let path = home.join("bin").join(binary);
        write(
            &path,
            format!(
                "#!/bin/sh\nexec {} __demo-tool {} \"$@\"\n",
                routines::shell_quote(&exe.display().to_string()),
                binary
            ),
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
        }
        registry
            .get_mut(binary)
            .ok_or_else(|| anyhow!("missing provider"))?["binary"] = json!(path);
        let spec = registry
            .get_mut(binary)
            .ok_or_else(|| anyhow!("missing provider"))?;
        if !spec["defaults"].is_object() {
            spec["defaults"] = json!({});
        }
        spec["defaults"]["PATH"] = json!(format!("{}:/usr/bin:/bin", home.join("bin").display()));
    }
    let mut data = orgs::create(
        &home,
        "demo",
        Some("Synthetic demo"),
        vec!["staging".into()],
        Some(vec![]),
        &registry,
    )?;
    data["connections"] = json!({"cubeapm":{"provider":"cubeapm"},"grafana":{"provider":"grafana"},"jenkins-staging":{"provider":"jenkins","env":"staging"}});
    orgs::save(&home, &data, &registry)?;
    for (cname, content) in [
        (
            "cubeapm",
            "CUBEAPM_SERVER=https://synthetic.invalid\nCUBEAPM_EMAIL=fixture@synthetic.invalid\nCUBEAPM_PASSWORD=synthetic-password\n",
        ),
        (
            "grafana",
            "GRAFANA_URL=https://synthetic.invalid\nGRAFANA_TOKEN=synthetic-token\n",
        ),
        (
            "jenkins-staging",
            "JENKINS_URL=https://synthetic.invalid\nJENKINS_USER=fixture\nJENKINS_TOKEN=synthetic-token\n",
        ),
    ] {
        write(&orgs::secrets_path(&home, "demo", cname)?, content)?;
    }
    let root = orgs::workspace(&home, "demo")?;
    let mut mapping: Value = serde_json::from_str(include_str!("../examples/services.json"))?;
    mapping["services"][0]["sources"]
        .as_object_mut()
        .ok_or_else(|| anyhow!("invalid fixture"))?
        .remove("github");
    write_json(
        &local_path(&root, "knowledge/staging/services.json")?,
        &mapping,
    )?;
    let ctx = orgs::Context::from_data(data, "staging");
    let (_, session) = create(
        &root,
        "demo",
        "staging",
        "Synthetic demo: checkout latency increased around a deployment",
        "debug",
        json!({"service":"checkout","service_context":mapping["services"][0],"window":{"from":"2026-09-08T14:00:00Z","to":"2026-09-08T14:30:00Z"},"playbook":"latency"}),
    )?;
    let sid = text(&session["id"]);
    let records = investigation::collect(
        &home,
        &ctx,
        &root,
        sid,
        investigation::CollectOptions {
            operation: None,
            params: json!({}),
            refresh: false,
            timeout: 15.0,
            max_bytes: 1048576,
        },
        &registry,
    )?;
    ensure!(
        records.len() == 7 && records.iter().all(|r| text(&r["status"]) == "ok"),
        "synthetic collection failed; inspect {}",
        root.display()
    );
    let refs = records
        .iter()
        .filter(|r| text(&r["label"]).starts_with("latency_ms"))
        .map(|r| text(&r["id"]).to_owned())
        .collect::<Vec<_>>();
    investigation::note(
        &root,
        sid,
        "finding",
        "Synthetic latency samples changed from 100 ms in the baseline to 850 ms in the incident window.",
        &refs,
        "",
        &[],
    )?;
    investigation::note(
        &root,
        sid,
        "hypothesis",
        "The nearby deployment may explain the slowdown; timing alone is insufficient.",
        &[],
        "Inspect build 42, establish the deployed SHA, and test its changes against pricing traces and request volume.",
        &[],
    )?;
    Ok(
        json!({"synthetic":true,"network_used":false,"home":home,"session":sid,"org":"demo","environment":"staging","report":investigation::report(&root,sid)?,"evidence_records":records.len(),"resume":format!("RECKON_HOME={} reckon resume {sid} --org demo --env staging",routines::shell_quote(&home.display().to_string()))}),
    )
}

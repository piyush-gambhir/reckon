use reckon::{orgs::Context, store::*, *};
#[cfg(unix)]
use std::fs;

fn words(values: &[&str]) -> Vec<String> {
    values.iter().map(|s| (*s).into()).collect()
}
fn fixture() -> Result<(tempfile::TempDir, orgs::Registry, Context)> {
    let dir = tempfile::tempdir()?;
    let registry = orgs::providers()?;
    let mut data = orgs::create(
        dir.path(),
        "acme",
        None,
        words(&["staging", "prod"]),
        None,
        &registry,
    )?;
    data["connections"] = json!({"jenkins-staging":{"provider":"jenkins","env":"staging"},"cubeapm":{"provider":"cubeapm","env_labels":{"staging":"STAGING","prod":"PROD"}}});
    orgs::save(dir.path(), &data, &registry)?;
    Ok((dir, registry, Context::from_data(data, "staging")))
}

#[test]
fn public_commands_and_flags_match_the_previous_cli() -> Result<()> {
    let commands: Value = serde_json::from_str(include_str!("fixtures/commands.json"))?;
    for (command, flags) in object(&commands) {
        let error = cli::parse(vec![command.clone(), "--help".into()])
            .err()
            .ok_or_else(|| anyhow!("help should return display help"))?;
        assert_eq!(error.exit_code(), 0, "{command}: {error}");
        let help = error.to_string();
        for flag in strings(&flags) {
            assert!(help.contains(&flag), "{command} lost {flag}");
        }
    }
    Ok(())
}

#[test]
fn safety_contracts_match_the_frozen_reference() -> Result<()> {
    let registry = orgs::providers()?;
    let cases: Value = serde_json::from_str(include_str!("fixtures/safety.json"))?;
    for case in array(&cases["check"]) {
        let args = &case["args"];
        let provider = text(&args[0]);
        let words = strings(&args[1]);
        let result = connectors::check(provider, &registry[provider], &words).and_then(|_| {
            if truth(&args[2]) {
                connectors::scope(&registry[provider], &words, text(&args[2])).map(|_| ())
            } else {
                Ok(())
            }
        });
        assert_eq!(
            result.is_ok(),
            truth(&case["allowed"]),
            "check {args}: {result:?}"
        );
    }
    for case in array(&cases["scope"]) {
        let args = &case["args"];
        let result = connectors::scope(&args[0], &strings(&args[1]), text(&args[2]));
        assert_eq!(
            result.is_ok(),
            truth(&case["allowed"]),
            "scope {args}: {result:?}"
        );
        if let Ok(result) = result {
            assert_eq!(json!(result), case["result"], "scope {args}");
        }
    }
    for case in array(&cases["bind"]) {
        let args = &case["args"];
        let values = object(&args[3])
            .iter()
            .map(|(k, v)| (k.clone(), text(v).to_owned()))
            .collect();
        let result = connectors::bind(&args[0], &strings(&args[1]), &args[2], &values);
        assert_eq!(
            result.is_ok(),
            truth(&case["allowed"]),
            "bind {args}: {result:?}"
        );
    }
    for case in array(&cases["site_allowed"]) {
        let args = &case["args"];
        assert_eq!(
            connectors::site_allowed(text(&args[0]), text(&args[1])),
            truth(&case["result"]),
            "site {args}"
        );
    }
    Ok(())
}

#[test]
fn tool_arguments_are_preserved_after_the_separator() -> Result<()> {
    let a = cli::parse(words(&[
        "run",
        "jenkins",
        "--org",
        "acme",
        "--",
        "build",
        "list",
        "--org=other",
        "-o",
        "json",
    ]))?;
    assert_eq!(a.s("org"), "acme");
    assert_eq!(
        a.passthrough,
        words(&["build", "list", "--org=other", "-o", "json"])
    );
    assert!(cli::parse(words(&["status", "--", "x"])).is_err());
    Ok(())
}

#[test]
fn all_typed_operations_preserve_their_request_contracts() -> Result<()> {
    let cases: Value = serde_json::from_str(include_str!("fixtures/operations.json"))?;
    for case in array(&cases) {
        let op = text(&case["operation"]);
        let built = operations::build(op, &case["params"], &case["bounds"], Path::new("."))?;
        let expected = &case["expected"];
        assert_eq!(json!(built.argv), expected["argv"], "{op}");
        assert_eq!(built.window, expected["window"], "{op}");
        assert_eq!(built.temporal_scope, text(&expected["temporal_scope"]));
        assert_eq!(built.limit, expected["limit"].as_u64().unwrap_or(0));
        if let Some(body) = built.stdin {
            let actual: Value = serde_json::from_slice(&body)?;
            let expected: Value = serde_json::from_str(text(&expected["stdin"]))?;
            assert_eq!(actual, expected, "{op}");
        }
    }
    let repo = tempfile::tempdir()?;
    let built = operations::build(
        "git.show",
        &json!({"path":repo.path(),"revision":"abcdef123456"}),
        &json!({"from":"2026-01-01T00:00:00Z","to":"2026-01-01T00:10:00Z"}),
        repo.path(),
    )?;
    assert!(built.argv.contains(&"--no-ext-diff".into()));
    assert_eq!(built.argv.last().map(String::as_str), Some("--"));
    Ok(())
}

#[test]
fn invalid_operation_arguments_fail_before_execution() -> Result<()> {
    let bounds = json!({"from":"2026-01-01T00:00:00Z","to":"2026-01-01T00:10:00Z"});
    for params in [
        json!({"query":"-evil"}),
        json!({"query":"up","unknown":"x"}),
        json!({"query":"up","step":0}),
        json!({"query":"up","step":true}),
        json!({"query":"up","step":1.5}),
    ] {
        assert!(
            operations::build("cubeapm.metrics.range", &params, &bounds, Path::new(".")).is_err(),
            "{params}"
        );
    }
    assert!(window("2026-01-01T00:00:00", "2026-01-01T00:01:00Z").is_err());
    assert!(window("2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z").is_err());
    Ok(())
}

#[test]
fn provider_parsers_reject_alternative_spellings_of_denied_flags() -> Result<()> {
    let registry = orgs::providers()?;
    for (provider, args) in [
        (
            "jenkins",
            words(&["job", "list", "--server=https://elsewhere.invalid"]),
        ),
        ("github", words(&["auth", "token"])),
        (
            "postgres",
            words(&["-dpostgresql://user:password@elsewhere/db"]),
        ),
        ("kubernetes", words(&["get", "secrets.v1."])),
        ("mysql", words(&["--loose_host=elsewhere"])),
        ("kafka", words(&["-b", "elsewhere"])),
    ] {
        assert!(
            connectors::check(provider, &registry[provider], &args).is_err(),
            "{provider} {args:?}"
        );
    }
    Ok(())
}

#[test]
fn shared_backends_cannot_escape_the_environment_label() -> Result<()> {
    let registry = orgs::providers()?;
    let spec = &registry["cubeapm"];
    for args in [
        words(&["metrics", "query", "up"]),
        words(&["metrics", "query", "up{env=\"PROD\"}"]),
        words(&["metrics", "query", "up{env=\"STAGING\"} or up"]),
        words(&["traces", "search", "--env", "PROD"]),
        words(&["traces", "search", "--"]),
    ] {
        assert!(
            connectors::scope(spec, &args, "STAGING").is_err(),
            "{args:?}"
        );
    }
    let args = connectors::scope(
        spec,
        &words(&["traces", "search", "--service", "api"]),
        "STAGING",
    )?;
    assert!(args.windows(2).any(|p| p == ["--env", "STAGING"]));
    Ok(())
}

#[test]
fn resource_binding_rejects_missing_and_foreign_targets() -> Result<()> {
    let r = orgs::providers()?;
    let spec = &r["github"];
    let conn = json!({"provider":"github","resources":{"repo":["acme/*"]}});
    for args in [
        words(&["issue", "list"]),
        words(&["issue", "list", "--repo", "other/api"]),
        words(&["api", "/repos/acme/api"]),
    ] {
        assert!(connectors::bind(spec, &args, &conn, &Default::default()).is_err());
    }
    assert!(
        connectors::bind(
            spec,
            &words(&["issue", "list", "--repo", "acme/api"]),
            &conn,
            &Default::default()
        )
        .is_ok()
    );
    Ok(())
}

#[test]
fn site_limits_validate_urls_before_matching() -> Result<()> {
    for site in [
        "https://evil.net#.acme.com/",
        "https://acme.com@evil.net/",
        "https://acme.com:0/",
        "sc-domain:acme.com.evil.net",
        "https://evil.net/?next=acme.com",
    ] {
        assert!(
            !connectors::site_allowed(site, "sc-domain:acme.com"),
            "{site}"
        );
    }
    assert!(connectors::site_allowed(
        "https://www.acme.com/a",
        "sc-domain:acme.com"
    ));
    assert!(!connectors::site_allowed(
        "https://acme.com/other",
        "https://acme.com/blog/"
    ));
    assert!(connectors::site_allowed(
        "https://acme.com/blog/a",
        "https://acme.com/blog/"
    ));
    Ok(())
}

#[test]
fn context_and_session_identity_fail_closed() -> Result<()> {
    let (dir, r, ctx) = fixture()?;
    assert!(orgs::context(dir.path(), Some("missing"), Some("staging"), &r).is_err());
    assert!(orgs::context(dir.path(), Some("acme"), Some("missing"), &r).is_err());
    assert!(orgs::resolve(&ctx, "jenkins-staging").is_ok());
    let mut prod = ctx.clone();
    prod.env = "prod".into();
    assert!(orgs::resolve(&prod, "jenkins-staging").is_err());
    let root = orgs::workspace(dir.path(), "acme")?;
    let (_, data) = create(&root, "acme", "staging", "q", "task", json!({}))?;
    assert!(investigation::matching(&data, &prod).is_err());
    Ok(())
}

#[test]
fn child_environment_contains_only_the_selected_connection() -> Result<()> {
    let (dir, r, ctx) = fixture()?;
    let spec = &r["jenkins"];
    let values = orgs::ResolvedSettings::from(process::Environment::from([
        ("JENKINS_TOKEN".into(), "one-secret".into()),
        ("JENKINS_URL".into(), "https://one.invalid".into()),
    ]));
    let (env, returned) = orgs::child_env(
        dir.path(),
        &ctx,
        "jenkins-staging",
        &ctx.data["connections"]["jenkins-staging"],
        spec,
        false,
        Some(&values),
    )?;
    assert_eq!(*returned, *values);
    assert_eq!(
        env.get("JENKINS_TOKEN").map(String::as_str),
        Some("one-secret")
    );
    assert_eq!(
        env.get("JENKINS_READ_ONLY").map(String::as_str),
        Some("true")
    );
    assert!(env["HOME"].contains("runtime/jenkins-staging/home"));
    assert_eq!(env["RECKON_ENV"], "staging");
    assert!(!env.contains_key("CODEX_HOME"));
    Ok(())
}

#[test]
fn secret_parsing_never_evaluates_shell_text_or_echoes_values() -> Result<()> {
    let (dir, r, _) = fixture()?;
    let p = orgs::secrets_path(dir.path(), "acme", "jenkins-staging")?;
    write(&p, "JENKINS_TOKEN='$(touch /this-must-not-exist)'\n")?;
    let values = orgs::secrets(dir.path(), "acme", "jenkins-staging", &r["jenkins"])?;
    assert_eq!(values["JENKINS_TOKEN"], "$(touch /this-must-not-exist)");
    write(&p, "AWS_SECRET_ACCESS_KEY=do-not-print-this\n")?;
    let error = orgs::secrets(dir.path(), "acme", "jenkins-staging", &r["jenkins"])
        .err()
        .ok_or_else(|| anyhow!("expected refusal"))?
        .to_string();
    assert!(!error.contains("do-not-print-this"));
    Ok(())
}

#[test]
fn persisted_files_are_private_and_atomically_replaced() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let p = dir.path().join("state/data.json");
    write_json(&p, &json!({"value":"first"}))?;
    write_json(&p, &json!({"value":"second"}))?;
    assert_eq!(read_json(&p)?, json!({"value":"second"}));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(p.metadata()?.permissions().mode() & 0o777, 0o600);
        assert_eq!(
            p.parent()
                .ok_or_else(|| anyhow!("parent"))?
                .metadata()?
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
    assert!(read_bytes(&p, 2).is_err());
    assert!(local_path(dir.path(), "../outside").is_err());
    Ok(())
}

#[cfg(unix)]
#[test]
fn symlinks_fifos_and_hardlinked_locks_are_rejected() -> Result<()> {
    use std::os::unix::{ffi::OsStrExt, fs::symlink};
    let dir = tempfile::tempdir()?;
    let secret = dir.path().join("secret");
    write(&secret, "keep this")?;
    symlink(&secret, dir.path().join("link"))?;
    assert!(local_path(dir.path(), "link").is_err());
    assert!(write(&dir.path().join("link"), "overwrite").is_err());
    assert_eq!(read_text(&secret)?, "keep this");
    symlink(dir.path().join("absent"), dir.path().join("broken"))?;
    assert!(local_path(dir.path(), "broken").is_err());
    let fifo = dir.path().join("fifo");
    let c = std::ffi::CString::new(fifo.as_os_str().as_bytes())?;
    unsafe {
        assert_eq!(libc::mkfifo(c.as_ptr(), 0o600), 0);
    }
    assert!(read_bytes(&fifo, 1024).is_err());
    let locked = dir.path().join("locked");
    private_dir(&locked)?;
    fs::hard_link(&secret, locked.join(".lock"))?;
    assert!(lock(&locked).is_err());
    Ok(())
}

#[test]
fn locks_exclude_competing_writers_and_release_on_drop() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let held = lock(dir.path())?;
    assert!(lock(dir.path()).is_err());
    drop(held);
    assert!(lock(dir.path()).is_ok());
    Ok(())
}

#[test]
fn growing_activity_logs_do_not_hit_the_state_file_size_limit() -> Result<()> {
    let (dir, _, ctx) = fixture()?;
    let path = orgs::org_path(dir.path(), "acme")?.join("activity.jsonl");
    let mut content = vec![b' '; 9 * 1048576];
    content.push(b'\n');
    content.extend(format!("{}\n",json!({"at":now(),"org":"acme","environment":"staging","event":"run","actor":"operator"})).as_bytes());
    write(&path, content)?;
    let rows = activity::read(dir.path(), &ctx.org, 7, Some("staging"), None, 1)?;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["event"], "run");
    Ok(())
}

#[test]
fn evidence_is_redacted_hashed_and_checked_before_citation() -> Result<()> {
    let (dir, _, ctx) = fixture()?;
    let root = orgs::workspace(dir.path(), "acme")?;
    let (path, session) = create(&root, "acme", "staging", "q", "task", json!({"budget":1}))?;
    let outcome = connectors::Outcome {
        connection: "jenkins-staging".into(),
        provider: "jenkins".into(),
        argv: words(&["jenkins", "job", "list", "-o", "json"]),
        values: process::Environment::from([("JENKINS_TOKEN".into(), "secret-value".into())])
            .into(),
        result: process::Output {
            status: process::Status::Ok,
            exit_code: Some(0),
            stdout: b"[{\"message\":\"secret-value\"}]".to_vec(),
            stderr: vec![],
            duration_ms: 1,
        },
        spec: json!({}),
        cached_at: None,
        quota_note: None,
    };
    let record = investigation::record_run(
        &root,
        text(&session["id"]),
        &ctx,
        &outcome,
        None,
        60.0,
        1024,
    )?;
    assert_eq!(evidence(&path)?.len(), 1);
    assert!(investigation::spend(&path, &session, 1).is_err());
    let artifact = path.join(format!("evidence/{}/stdout.txt", text(&record["id"])));
    assert!(!read_text(&artifact)?.contains("secret-value"));
    write(&artifact, "[]")?;
    assert!(evidence(&path).is_err());
    assert!(
        investigation::note(
            &root,
            text(&session["id"]),
            "finding",
            "cannot cite corruption",
            &[text(&record["id"]).into()],
            "",
            &[]
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn action_hashes_freeze_payload_and_target_without_secret_values() -> Result<()> {
    let conn = json!({"provider":"github","env":"staging","account":"acme-bot"});
    let values = process::Environment::from([
        ("GH_HOST".into(), "github.com".into()),
        ("GH_TOKEN".into(), "first-secret".into()),
    ]);
    let mut changed = values.clone();
    changed.insert("GH_TOKEN".into(), "rotated-secret".into());
    assert_eq!(
        actions::target(&conn, &values),
        actions::target(&conn, &changed)
    );
    changed.insert("GH_HOST".into(), "other.invalid".into());
    assert_ne!(
        actions::target(&conn, &values),
        actions::target(&conn, &changed)
    );
    let mut record = json!({"org":"acme","environment":"staging","connection":"gh","operation":"github.issue.create","params":{"repo":"acme/api","title":"a","body":"b"}});
    let hash = actions::digest(&record);
    record["params"]["repo"] = json!("other/api");
    assert_ne!(hash, actions::digest(&record));
    Ok(())
}

#[cfg(unix)]
#[test]
fn processes_enforce_time_output_and_exit_status_limits() -> Result<()> {
    let timeout = process::run(
        &words(&["/bin/sh", "-c", "sleep 10"]),
        None,
        None,
        0.05,
        1024,
        None,
    )?;
    assert_eq!(timeout.status, process::Status::Timeout);
    let limit = process::run(
        &words(&[
            "/bin/sh",
            "-c",
            "while :; do printf xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx; done",
        ]),
        None,
        None,
        2.0,
        10000,
        None,
    )?;
    assert_eq!(limit.status, process::Status::OutputLimit);
    assert_eq!(limit.stdout.len() + limit.stderr.len(), 10000);
    let failed = process::run(
        &words(&["/bin/sh", "-c", "printf partial; exit 7"]),
        None,
        None,
        2.0,
        1024,
        None,
    )?;
    assert_eq!(failed.exit_code, Some(7));
    assert_eq!(failed.stdout, b"partial");
    Ok(())
}

#[test]
fn compiled_executable_runs_without_python() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_reckon"))
        .args(["org", "add", "acme", "--json"])
        .env_clear()
        .env("RECKON_HOME", dir.path())
        .env("PATH", "")
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let data: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(data["org"], "acme");
    Ok(())
}

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
#[ignore = "requires RECKON_REAL_BIN_DIR with all ten pinned first-party CLIs"]
fn real_cli_resolvers_accept_reads_and_refuse_writes() -> Result<()> {
    let folder = PathBuf::from(
        std::env::var_os("RECKON_REAL_BIN_DIR")
            .ok_or_else(|| anyhow!("set RECKON_REAL_BIN_DIR"))?,
    );
    let registry = orgs::providers()?;
    let dir = tempfile::tempdir()?;
    let mut env = process::Environment::new();
    for key in ["HOME", "TMPDIR", "XDG_CONFIG_HOME"] {
        env.insert(key.into(), dir.path().to_string_lossy().into_owned());
    }
    env.insert("PATH".into(), "/usr/bin:/bin".into());
    for name in [
        "GSC", "BWT", "CLARITY", "GRAFANA", "JENKINS", "CUBEAPM", "ES", "JIRA", "NGINXPM",
        "WHATSAPP",
    ] {
        env.insert(format!("{name}_NO_UPDATE_NOTIFIER"), "1".into());
    }
    for (provider, read, denied) in [
        (
            "elasticsearch",
            words(&["cluster", "health"]),
            words(&["-o", "json", "index", "delete", "fixture"]),
        ),
        (
            "jenkins",
            words(&["job", "list"]),
            words(&["-o", "json", "job", "build", "fixture"]),
        ),
        (
            "jira",
            words(&["issue", "search", "--jql", "fixture"]),
            words(&["-o", "json", "issue", "create"]),
        ),
        (
            "grafana",
            words(&["dashboard", "list"]),
            words(&["-o", "json", "dashboard", "delete", "fixture"]),
        ),
        (
            "nginxpm",
            words(&["proxy", "list"]),
            words(&["-o", "json", "proxy", "delete", "1"]),
        ),
        (
            "cubeapm",
            words(&["traces", "search", "--service", "fixture"]),
            words(&["-o", "json", "logs", "delete", "fixture"]),
        ),
        (
            "gsc",
            words(&["top", "queries", "--last", "7d"]),
            words(&["auth", "-o", "json", "token"]),
        ),
        (
            "bing",
            words(&["sites", "list"]),
            words(&["-o", "json", "submit", "urls", "https://fixture.invalid/a"]),
        ),
        (
            "clarity",
            words(&["export", "--days", "1"]),
            words(&["auth", "logout"]),
        ),
        (
            "whatsapp",
            words(&["numbers", "list"]),
            words(&["messages", "send"]),
        ),
    ] {
        let mut spec = registry[provider].clone();
        spec["binary"] = json!(folder.join(text(&spec["binary"])));
        let read = connectors::resolved(&spec, &env, &read)?;
        assert_eq!(read["effect"], "read", "{provider}");
        assert!(
            connectors::resolved(&spec, &env, &denied).is_err(),
            "{provider}"
        );
    }
    Ok(())
}

#[test]
fn displayed_numbers_preserve_integer_precision_and_scientific_notation() {
    assert_eq!(
        number(&json!(18446744073709551615u64)),
        "18,446,744,073,709,551,615"
    );
    assert_eq!(number(&json!(-1234.5)), "-1,234.5");
    for value in [json!(1e300), json!(1e-100), json!(-1e300)] {
        assert_eq!(number(&value), value.to_string());
    }
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
        words(&["metrics", "query", "sum(up{env=\"STAGING\"}) + sum(up)"]),
        words(&[
            "metrics",
            "query",
            "topk(scalar(sum(up)), up{env=\"STAGING\"})",
        ]),
        words(&[
            "metrics",
            "query",
            "up{env=\"STAGING\"} + sum_over_time(up[5m])",
        ]),
        words(&["metrics", "query", "info(up{env=\"STAGING\"})"]),
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
fn shared_metrics_check_every_selector_without_rewriting_the_query() -> Result<()> {
    let registry = orgs::providers()?;
    for query in [
        "sum(rate(requests{env=\"STAGING\"}[5m])) / sum(rate(errors{env=\"STAGING\"}[5m]))",
        "up{env=\"STAGING\"} or other{environment=\"STAGING\"}",
        "topk(scalar(sum(up{env=\"STAGING\"})), other{env=\"STAGING\"})",
        "sum_over_time((up{env=\"STAGING\"})[5m:1m])",
        "up{env=~\"STAGING\"}",
        "info(up{env=\"STAGING\"}, {env=\"STAGING\"})",
    ] {
        let args = words(&["metrics", "query", "-o", "json", query]);
        assert_eq!(
            connectors::scope(&registry["cubeapm"], &args, "STAGING")?,
            args
        );
    }
    for query in [
        "up{env=\"STAGING\",job=\"api\"} + other{job=\"api\"}",
        "up{env=\"STAGING\"} # + up{env=\"STAGING\"}\n + sum(up)",
        "up{env=~\"STAGING|PROD\"}",
        "label_replace(up, \"x\", \"env=STAGING\", \"job\", \".*\")",
        "up{env=\"STAGING\"} + (sum(up)[5m:1m])",
    ] {
        assert!(
            connectors::scope(
                &registry["cubeapm"],
                &words(&["metrics", "query", query]),
                "STAGING"
            )
            .is_err(),
            "{query}"
        );
    }
    assert!(
        connectors::scope(
            &registry["cubeapm"],
            &words(&["metrics", "query", "up{env=~\"staging.1\"}"]),
            "staging.1"
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn shared_metric_query_bytes_are_bounded_before_parsing() -> Result<()> {
    let registry = orgs::providers()?;
    let prefix = "up{env=\"STAGING\",note=\"";
    let suffix = "\"}";
    for (size, allowed) in [(65536, true), (65537, false)] {
        let query = format!(
            "{prefix}{}{suffix}",
            "x".repeat(size - prefix.len() - suffix.len())
        );
        let result = connectors::scope(
            &registry["cubeapm"],
            &words(&["metrics", "query", &query]),
            "STAGING",
        );
        assert_eq!(result.is_ok(), allowed);
        if !allowed {
            assert!(
                result
                    .err()
                    .is_some_and(|e| e.to_string().contains("complex"))
            );
        }
    }
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
fn creating_an_org_never_adopts_an_existing_directory() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let registry = orgs::providers()?;
    let path = orgs::org_path(dir.path(), "acme")?;
    std::fs::create_dir_all(&path)?;
    std::fs::write(path.join("keep.txt"), "pre-existing data")?;
    assert!(
        orgs::create(
            dir.path(),
            "acme",
            None,
            words(&["staging"]),
            None,
            &registry
        )
        .is_err()
    );
    assert_eq!(
        std::fs::read_to_string(path.join("keep.txt"))?,
        "pre-existing data"
    );
    assert!(!path.join("org.json").exists());
    Ok(())
}

#[test]
fn copying_a_tree_into_itself_is_refused_before_creating_output() -> Result<()> {
    let dir = tempfile::tempdir()?;
    std::fs::write(dir.path().join("keep.txt"), "source")?;
    let nested = dir.path().join("nested/output");
    assert!(copy_tree(dir.path(), &nested).is_err());
    assert!(!dir.path().join("nested").exists());
    assert!(copy_tree(dir.path(), dir.path()).is_err());
    assert_eq!(
        std::fs::read_to_string(dir.path().join("keep.txt"))?,
        "source"
    );
    Ok(())
}

#[test]
fn competing_org_creators_cannot_overwrite_each_other() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let registry = orgs::providers()?;
    let barrier = std::sync::Barrier::new(8);
    let results = std::thread::scope(|scope| {
        let workers = (0..8)
            .map(|_| {
                scope.spawn(|| {
                    barrier.wait();
                    orgs::create(
                        dir.path(),
                        "acme",
                        None,
                        words(&["staging"]),
                        None,
                        &registry,
                    )
                })
            })
            .collect::<Vec<_>>();
        workers
            .into_iter()
            .map(|worker| worker.join().map_err(|_| anyhow!("org creator panicked")))
            .collect::<Result<Vec<_>>>()
    })?;
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        orgs::load(dir.path(), "acme", &registry)?["environments"],
        json!(["staging"])
    );
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
    assert!(
        Path::new(&env["HOME"])
            .ends_with(Path::new("runtime").join("jenkins-staging").join("home"))
    );
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
fn private_state_creation_preserves_existing_parent_permissions() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir()?;
    let shared = dir.path().join("shared");
    fs::create_dir(&shared)?;
    fs::set_permissions(&shared, fs::Permissions::from_mode(0o755))?;
    let state = shared.join("reckon/new/state.json");
    write_json(&state, &json!({"fixture":true}))?;
    assert_eq!(shared.metadata()?.permissions().mode() & 0o777, 0o755);
    assert_eq!(
        shared.join("reckon").metadata()?.permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        shared.join("reckon/new").metadata()?.permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(state.metadata()?.permissions().mode() & 0o777, 0o600);
    assert!(private_dir(&state).is_err());
    assert_eq!(state.metadata()?.permissions().mode() & 0o777, 0o600);
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
fn sha256_preserves_standard_digest_vectors() {
    assert_eq!(
        sha256(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    assert_eq!(
        sha256(b"abc"),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
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

#[cfg(unix)]
#[test]
fn activity_append_refuses_hard_links_and_repairs_log_permissions() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let (dir, _, ctx) = fixture()?;
    let external = dir.path().join("external");
    fs::write(&external, "unchanged")?;
    fs::set_permissions(&external, fs::Permissions::from_mode(0o644))?;
    let log = orgs::org_path(dir.path(), "acme")?.join("activity.jsonl");
    fs::hard_link(&external, &log)?;
    activity::record(dir.path(), &ctx, "run", json!({"status":"ok"}));
    assert_eq!(fs::read_to_string(&external)?, "unchanged");
    assert_eq!(external.metadata()?.permissions().mode() & 0o777, 0o644);
    fs::remove_file(&log)?;
    fs::write(&log, "")?;
    fs::set_permissions(&log, fs::Permissions::from_mode(0o644))?;
    activity::record(dir.path(), &ctx, "run", json!({"status":"ok"}));
    assert_eq!(log.metadata()?.permissions().mode() & 0o777, 0o600);
    assert_eq!(
        activity::read(dir.path(), "acme", 1, Some("staging"), None, 10)?.len(),
        1
    );
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
        source_sha256: String::new(),
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
fn a_saved_watch_cannot_bypass_a_new_per_command_approval_policy() -> Result<()> {
    let (dir, mut registry, ctx) = fixture()?;
    watches::add(
        dir.path(),
        &ctx,
        "builds",
        watches::WatchOptions {
            target: "jenkins-staging",
            args: &words(&["job", "list"]),
            items: "",
            key: "id",
            title: None,
            production: false,
        },
        &registry,
    )?;
    registry["jenkins"]["mode"] = json!("approval");
    let rows = watches::run(dir.path(), "acme", &registry, &[])?;
    assert_eq!(rows[0]["status"], "error");
    assert!(text(&rows[0]["detail"]).contains("approval"));
    assert!(
        !orgs::org_path(dir.path(), "acme")?
            .join("runtime/watches/builds/state.json")
            .exists()
    );
    Ok(())
}

#[test]
#[cfg(unix)]
fn folder_links_reject_lossy_path_keys_without_changing_another_binding() -> Result<()> {
    use std::os::unix::ffi::OsStringExt;
    let (home, registry, _) = fixture()?;
    let repositories = tempfile::tempdir()?;
    let valid = repositories.path().join("repo-\u{fffd}");
    let invalid = repositories
        .path()
        .join(std::ffi::OsString::from_vec(b"repo-\xff".to_vec()));
    std::fs::create_dir(&valid)?;
    orgs::link(home.path(), &valid, "acme", "staging", vec![], &registry)?;
    let path = home.path().join("links.json");
    let before = read_bytes(&path, 8 * 1048576)?;
    assert!(orgs::link(home.path(), &invalid, "acme", "prod", vec![], &registry).is_err());
    assert_eq!(read_bytes(&path, 8 * 1048576)?, before);
    assert!(orgs::unlink(home.path(), &invalid).is_err());
    assert_eq!(read_bytes(&path, 8 * 1048576)?, before);
    orgs::unlink(home.path(), &valid)?;
    assert!(object(&orgs::links(home.path())?).is_empty());
    Ok(())
}

#[test]
fn directory_listings_preserve_missing_paths_and_report_invalid_state() -> Result<()> {
    let root = tempfile::tempdir()?;
    assert!(entries(&root.path().join("missing"))?.is_empty());
    let folder = root.path().join("folder");
    std::fs::create_dir(&folder)?;
    assert!(entries(&folder)?.is_empty());
    std::fs::write(folder.join("b"), "second")?;
    std::fs::write(folder.join("a"), "first")?;
    assert_eq!(entries(&folder)?, vec![folder.join("a"), folder.join("b")]);
    let file = root.path().join("state");
    std::fs::write(&file, "preserved data")?;
    assert!(entries(&file).is_err());
    assert_eq!(std::fs::read(&file)?, b"preserved data");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert!(entries(&file.join("child")).is_err());
        let blocked = root.path().join("blocked");
        let child = blocked.join("child");
        std::fs::create_dir_all(&child)?;
        std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o000))?;
        let inaccessible = std::fs::metadata(&child).is_err();
        let result = entries(&child);
        std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o700))?;
        // Privileged test runners can bypass directory permissions.
        if inaccessible {
            assert!(result.is_err());
        }
    }
    Ok(())
}

#[test]
fn json_state_writes_reject_unreadable_nesting_without_replacing_data() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("state.json");
    write_json(&path, &json!({"before":true}))?;
    let before = read_bytes(&path, 8 * 1048576)?;
    let nested: Value = serde_json::from_str(&format!("{}0{}", "[".repeat(125), "]".repeat(125)))?;
    let wrapped = json!({"one":{"two":{"three":nested.clone()}}});
    assert!(write_json(&path, &wrapped).is_err());
    assert_eq!(read_bytes(&path, 8 * 1048576)?, before);
    write_json(&path, &nested)?;
    assert_eq!(read_json(&path)?, nested);
    Ok(())
}

#[test]
fn rejected_session_metadata_does_not_leave_an_incomplete_session() -> Result<()> {
    let root = tempfile::tempdir()?;
    let nested: Value = serde_json::from_str(&format!("{}0{}", "[".repeat(125), "]".repeat(125)))?;
    for extra in [
        json!({"padding":"x".repeat(8 * 1048576)}),
        json!({"one":{"two":{"three":nested}}}),
    ] {
        assert!(
            create(
                root.path(),
                "acme",
                "staging",
                "Rejected metadata",
                "task",
                extra
            )
            .is_err()
        );
        assert!(entries(&root.path().join("sessions"))?.is_empty());
    }
    let (path, data) = create(
        root.path(),
        "acme",
        "staging",
        "Valid metadata",
        "task",
        json!({}),
    )?;
    assert_eq!(read_json(&path.join("session.json"))?, data);
    assert!(path.join("evidence").is_dir());
    Ok(())
}

#[test]
fn promotion_validates_session_metadata_before_publishing_an_incident() -> Result<()> {
    let (home, _, _) = fixture()?;
    let root = orgs::workspace(home.path(), "acme")?;
    let (path, mut data) = create(&root, "acme", "staging", "Promotion", "task", json!({}))?;
    let sid = text(&data["id"]).to_owned();
    data["padding"] = json!("");
    let size = serde_json::to_string_pretty(&data)?.len() + 1;
    data["padding"] = json!("x".repeat(8 * 1048576 - size));
    write_json(&path.join("session.json"), &data)?;
    let before = file_sha256(&path.join("session.json"))?;
    assert!(investigation::promote(&root, &sid, "bounded-metadata").is_err());
    assert!(entries(&root.join("incidents"))?.is_empty());
    assert_eq!(file_sha256(&path.join("session.json"))?, before);

    data.as_object_mut()
        .ok_or_else(|| anyhow!("session metadata must be an object"))?
        .remove("padding");
    write_json(&path.join("session.json"), &data)?;
    let incident = investigation::promote(&root, &sid, "bounded-metadata")?;
    assert!(incident.join("RCA.md").is_file());
    assert_eq!(
        read_json(&path.join("session.json"))?["incident"],
        json!(incident.strip_prefix(&root)?)
    );
    assert!(investigation::promote(&root, &sid, "bounded-metadata").is_err());
    Ok(())
}

#[test]
fn json_state_writes_respect_the_reader_limit_and_preserve_previous_data() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("state.json");
    let maximum = 8 * 1048576;
    write_json(&path, &json!("x".repeat(maximum - 3)))?;
    assert_eq!(std::fs::metadata(&path)?.len(), maximum as u64);
    assert!(read_json(&path).is_ok());
    let before = file_sha256(&path)?;
    // Escaping expands the encoded file beyond the input string's byte count.
    let large = json!("\"".repeat(maximum / 2));
    assert!(write_json(&path, &large).is_err());
    assert_eq!(file_sha256(&path)?, before);
    assert!(read_json(&path).is_ok());
    let absent = dir.path().join("new/state.json");
    assert!(write_json(&absent, &large).is_err());
    assert!(!absent.parent().is_some_and(Path::exists));
    Ok(())
}

#[test]
fn folder_link_mutations_refuse_an_active_lock_and_preserve_state() -> Result<()> {
    let (home, registry, _) = fixture()?;
    let repositories = tempfile::tempdir()?;
    let first = repositories.path().join("first");
    let second = repositories.path().join("second");
    std::fs::create_dir(&first)?;
    std::fs::create_dir(&second)?;
    orgs::link(home.path(), &first, "acme", "staging", vec![], &registry)?;
    let path = home.path().join("links.json");
    let before = read_bytes(&path, 8 * 1048576)?;
    let held = lock(home.path())?;
    for result in [
        orgs::link(home.path(), &second, "acme", "staging", vec![], &registry),
        orgs::unlink(home.path(), &first),
    ] {
        let error = result
            .err()
            .ok_or_else(|| anyhow!("link mutation ignored an active lock"))?;
        assert!(error.to_string().contains("folder links are locked"));
        assert_eq!(read_bytes(&path, 8 * 1048576)?, before);
    }
    drop(held);
    orgs::link(home.path(), &second, "acme", "staging", vec![], &registry)?;
    orgs::unlink(home.path(), &first)?;
    assert_eq!(object(&orgs::links(home.path())?).len(), 1);
    Ok(())
}

#[test]
fn concurrent_folder_links_preserve_every_successful_update() -> Result<()> {
    let (home, registry, _) = fixture()?;
    let repositories = tempfile::tempdir()?;
    let folders = (0..8)
        .map(|i| repositories.path().join(format!("repo-{i}")))
        .collect::<Vec<_>>();
    for folder in &folders {
        std::fs::create_dir(folder)?;
    }
    let barrier = std::sync::Barrier::new(folders.len());
    let results = std::thread::scope(|scope| {
        let workers = folders
            .iter()
            .map(|folder| {
                let (barrier, home, registry) = (&barrier, home.path(), &registry);
                scope.spawn(move || {
                    barrier.wait();
                    orgs::link(home, folder, "acme", "staging", vec![], registry)
                })
            })
            .collect::<Vec<_>>();
        workers
            .into_iter()
            .map(|worker| {
                worker
                    .join()
                    .map_err(|_| anyhow!("folder link worker panicked"))
            })
            .collect::<Result<Vec<_>>>()
    })?;
    let saved = orgs::links(home.path())?;
    for result in &results {
        if let Err(error) = result {
            assert!(error.to_string().contains("folder links are locked"));
        }
    }
    let successful = results
        .iter()
        .filter_map(|result| result.as_ref().ok())
        .collect::<Vec<_>>();
    assert!(!successful.is_empty());
    assert_eq!(object(&saved).len(), successful.len());
    for record in successful {
        assert_eq!(saved[text(&record["path"])]["org"], "acme");
    }
    Ok(())
}

#[test]
fn concurrent_watch_registration_never_loses_a_successful_add() -> Result<()> {
    let (dir, registry, ctx) = fixture()?;
    let barrier = std::sync::Barrier::new(6);
    let results = std::thread::scope(|scope| {
        let workers = (0..6)
            .map(|i| {
                let (barrier, h, registry, ctx) = (&barrier, dir.path(), &registry, &ctx);
                scope.spawn(move || {
                    barrier.wait();
                    watches::add(
                        h,
                        ctx,
                        &format!("w{i}"),
                        watches::WatchOptions {
                            target: "jenkins-staging",
                            args: &words(&["job", "list"]),
                            items: "",
                            key: "id",
                            title: None,
                            production: false,
                        },
                        registry,
                    )
                })
            })
            .collect::<Vec<_>>();
        workers
            .into_iter()
            .map(|worker| {
                worker
                    .join()
                    .map_err(|_| anyhow!("watch registration panicked"))
            })
            .collect::<Result<Vec<_>>>()
    })?;
    let saved = watches::load(dir.path(), "acme")?;
    let successful = results
        .iter()
        .filter_map(|result| result.as_ref().ok())
        .collect::<Vec<_>>();
    assert!(!successful.is_empty());
    assert_eq!(object(&saved).len(), successful.len());
    for record in successful {
        assert!(saved.get(text(&record["watch"])).is_some());
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn unsafe_watch_locks_are_errors_not_successful_overlap_skips() -> Result<()> {
    let (dir, registry, ctx) = fixture()?;
    watches::add(
        dir.path(),
        &ctx,
        "builds",
        watches::WatchOptions {
            target: "jenkins-staging",
            args: &words(&["job", "list"]),
            items: "",
            key: "id",
            title: None,
            production: false,
        },
        &registry,
    )?;
    let lock_dir = orgs::org_path(dir.path(), "acme")?.join("runtime/watch-locks/builds");
    private_dir(&lock_dir)?;
    let external = dir.path().join("external");
    fs::write(&external, "unchanged")?;
    fs::hard_link(&external, lock_dir.join(".lock"))?;
    let rows = watches::run(dir.path(), "acme", &registry, &[])?;
    assert_eq!(rows[0]["status"], "error");
    assert!(text(&rows[0]["detail"]).contains("private regular file"));
    assert_eq!(fs::read_to_string(&external)?, "unchanged");
    Ok(())
}

#[test]
fn typed_detail_identities_match_the_request_and_canonical_trace_ids() -> Result<()> {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures/typed_outputs.json"))?;
    let requests: Value = serde_json::from_str(include_str!("fixtures/typed_requests.json"))?;
    let dir = tempfile::tempdir()?;
    for (operation, field) in [
        ("jenkins.build.get", "number"),
        ("github.run.get", "databaseId"),
        ("nginxpm.proxy.get", "id"),
        ("nginxpm.cert.get", "id"),
        ("cubeapm.trace.get", "traceID"),
    ] {
        let built = operations::build(
            operation,
            &requests[operation],
            &json!({"from":"2026-10-01T00:00:00Z","to":"2026-10-01T00:05:00Z"}),
            dir.path(),
        )?;
        let make = |payload: &Value, command: &[String]| -> Result<Value> {
            let mut record = json!({"id":"e-identity","operation":operation,"provider":built.provider,"capability":built.capability,"command":command,"artifacts":{}});
            investigation::save(
                dir.path(),
                &mut record,
                &process::Output {
                    status: process::Status::Ok,
                    exit_code: Some(0),
                    stdout: serde_json::to_vec(payload)?,
                    stderr: vec![],
                    duration_ms: 1,
                },
                &process::Environment::new(),
                "json",
                1000,
            )?;
            Ok(record)
        };
        let mut payload = fixtures[operation].clone();
        assert_eq!(make(&payload, &built.argv)?["status"], "ok");
        payload[field] = if field == "traceID" {
            json!("fedcba9876543210")
        } else {
            json!(2)
        };
        assert_eq!(
            make(&payload, &built.argv)?["status"],
            "failed",
            "{operation}"
        );
        payload = fixtures[operation].clone();
        if field == "traceID" {
            payload[field] = json!(format!(
                "{:0>32}",
                text(&payload[field]).to_ascii_uppercase()
            ));
            assert_eq!(make(&payload, &built.argv)?["status"], "ok");
            payload["spans"][0]["traceID"] = json!("fedcba9876543210");
            assert_eq!(make(&payload, &built.argv)?["status"], "failed");
        } else {
            let mut command = built.argv.clone();
            let index = if operation == "jenkins.build.get" {
                4
            } else {
                3
            };
            command[index] = "001".into();
            assert_eq!(make(&payload, &command)?["status"], "ok");
            command.remove(index);
            assert_eq!(make(&payload, &command)?["status"], "failed");
        }
    }
    Ok(())
}

#[test]
fn typed_operation_contracts_accept_provider_formats_and_reject_unrelated_json() -> Result<()> {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures/typed_outputs.json"))?;
    let requests: Value = serde_json::from_str(include_str!("fixtures/typed_requests.json"))?;
    let dir = tempfile::tempdir()?;
    assert_eq!(object(&fixtures).len(), operations::OPERATIONS.len());
    for &(operation, provider, capability, _, _, format) in operations::OPERATIONS {
        let built = operations::build(
            operation,
            &requests[operation],
            &json!({"from":"2026-10-01T00:00:00Z","to":"2026-10-01T00:05:00Z"}),
            dir.path(),
        )?;
        let fixture = fixtures
            .get(operation)
            .expect("every typed operation has a provider fixture");
        let stdout = if format == "text" {
            text(fixture).as_bytes().to_vec()
        } else {
            serde_json::to_vec(fixture)?
        };
        let make = |stdout| -> Result<Value> {
            let mut record = json!({"id":"e-contract","provider":provider,"operation":operation,"capability":capability,"command":built.argv,"artifacts":{}});
            investigation::save(
                dir.path(),
                &mut record,
                &process::Output {
                    status: process::Status::Ok,
                    exit_code: Some(0),
                    stdout,
                    stderr: vec![],
                    duration_ms: 1,
                },
                &process::Environment::new(),
                format,
                1000,
            )?;
            Ok(record)
        };
        let record = make(stdout)?;
        assert_eq!(record["status"], "ok", "{operation}: {record}");
        if format != "text" {
            let record = make(b"1".to_vec())?;
            assert_eq!(record["status"], "failed", "{operation}");
            assert_eq!(record["completeness"], "invalid_output", "{operation}");
        }
        if [
            "grafana.annotations",
            "jenkins.builds",
            "github.runs",
            "jira.search",
        ]
        .contains(&operation)
        {
            assert_eq!(
                make(b"null".to_vec())?["status"],
                "ok",
                "{operation}: empty Go slice"
            );
        }
    }
    Ok(())
}

#[test]
fn metric_means_remain_finite_for_extreme_finite_values() {
    for (values, expected) in [
        (vec![1e308, 1e308], 1e308),
        (vec![-1e308, -1e308], -1e308),
        (vec![f64::MAX; 100], f64::MAX),
        (vec![-f64::MAX, f64::MAX], 0.0),
        (vec![0.0, 0.0], 0.0),
    ] {
        let payload = json!({"data":{"resultType":"matrix","result":[{"metric":{},"values":values.iter().enumerate().map(|(i,value)|json!([i,value.to_string()])).collect::<Vec<_>>()}]}});
        let summary = investigation::payload_summary(&payload.to_string(), "json", 100);
        assert_eq!(summary["completeness"], "bounded");
        let mean = summary["series"][0]["mean"].as_f64().expect("finite mean");
        assert!(mean.is_finite());
        assert_eq!(mean, expected);
    }
}

#[test]
fn metric_validation_covers_histograms_labels_and_series_outside_the_preview() {
    let valid = json!({"metric":{},"values":[[0,"1"]]});
    for invalid in [
        json!({"metric":{"job":3},"values":[[0,"1"]]}),
        json!({"metric":[],"values":[]}),
        json!({"values":[[0,"1e1000"]]}),
        json!({"histograms":[[0,{}]]}),
        json!({"histograms":[[0,{"count":"1","sum":"2","buckets":[[4,"0","1","1"]]}]]}),
    ] {
        let mut series = vec![valid.clone(); 20];
        series.push(invalid);
        let payload = json!({"data":{"resultType":"matrix","result":series}});
        assert_eq!(
            investigation::payload_summary(&payload.to_string(), "json", 100)["completeness"],
            "invalid_output"
        );
    }
    let mut series = vec![json!({"metric":{},"values":[]}); 20];
    series.push(valid);
    let payload = json!({"data":{"resultType":"matrix","result":series}});
    let summary = investigation::payload_summary(&payload.to_string(), "json", 100);
    assert_eq!(summary["completeness"], "bounded");
    assert_eq!(summary["series_truncated"], true);
    assert_eq!(array(&summary["series"]).len(), 20);
}

#[test]
fn github_format_detection_respects_projection_flags_and_flag_values() -> Result<()> {
    let registry = orgs::providers()?;
    let spec = &registry["github"];
    for (args, expected) in [
        (words(&["pr", "list", "--json=number"]), "json"),
        (
            words(&["pr", "list", "--search", "--jq", "--json", "number"]),
            "json",
        ),
        (
            words(&["run", "ls", "-b", "--template", "--json=number"]),
            "json",
        ),
        (
            words(&["pr", "list", "--json", "title", "--jq=.[].title"]),
            "text",
        ),
        (words(&["pr", "list", "--json", "title", "-t{{.}}"]), "text"),
    ] {
        assert_eq!(
            investigation::output_format(&args, spec),
            expected,
            "{args:?}"
        );
    }
    Ok(())
}

#[test]
fn review_quality_is_bounded_and_cannot_hide_provider_errors() -> Result<()> {
    let dir = tempfile::tempdir()?;
    for (provider, command, payload) in [
        (
            "bing",
            words(&["bwt", "stats", "summary"]),
            json!({"error":"denied"}),
        ),
        (
            "clarity",
            words(&["clarity", "recordings", "list"]),
            json!({"error":"denied"}),
        ),
        (
            "github",
            words(&["gh", "pr", "list", "--jq", "."]),
            json!({"error":"denied"}),
        ),
        (
            "grafana",
            words(&["grafana", "datasource", "query"]),
            json!({"warnings":vec!["line\n".repeat(2000);25]}),
        ),
        (
            "jenkins",
            words(&["jenkins", "build", "get"]),
            json!({"building":false,"result":"x".repeat(100000)}),
        ),
    ] {
        let mut record = json!({"id":"e-quality","provider":provider,"operation":format!("run:{provider}"),"capability":"run","command":command,"artifacts":{}});
        investigation::save(
            dir.path(),
            &mut record,
            &process::Output {
                status: process::Status::Ok,
                exit_code: Some(0),
                stdout: serde_json::to_vec(&payload)?,
                stderr: vec![],
                duration_ms: 1,
            },
            &process::Environment::new(),
            "json",
            1000,
        )?;
        if provider == "grafana" {
            let warnings = strings(&record["data_quality"]["warnings"]);
            assert_eq!(warnings.len(), 21);
            assert!(
                warnings
                    .iter()
                    .all(|warning| warning.len() <= 250 && !warning.contains('\n'))
            );
        } else if provider == "jenkins" {
            assert_eq!(
                text(&record["data_quality"]["sources"][0]["data_state"]).len(),
                200
            );
            assert_eq!(record["status"], "ok");
        } else {
            assert_eq!(record["status"], "failed");
            assert_eq!(record["completeness"], "provider_error");
        }
        assert!(serde_json::to_vec(&record)?.len() < 65536);
    }
    Ok(())
}

#[test]
fn gsc_quality_stays_bounded_and_preserves_provider_failures() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let period = json!({"complete":false,"data_state":"all","start":"2026-10-01","end":"2026-10-07","first_incomplete_date":"x".repeat(10000)});
    for (provider, payload, status, completeness) in [
        (
            "gsc",
            json!({"sources":vec![period.clone();21]}),
            "ok",
            "partial",
        ),
        (
            "gsc",
            json!({"error":"forbidden","complete":false}),
            "failed",
            "provider_error",
        ),
        ("gsc", json!({"rows":[]}), "ok", "bounded"),
        ("jenkins", period, "ok", "bounded"),
    ] {
        let mut record = json!({"id":"e-quality","provider":provider,"operation":format!("run:{provider}"),"capability":"run","command":[provider,"fixture"],"artifacts":{}});
        let output = process::Output {
            status: process::Status::Ok,
            exit_code: Some(0),
            stdout: serde_json::to_vec(&payload)?,
            stderr: vec![],
            duration_ms: 1,
        };
        investigation::save(
            dir.path(),
            &mut record,
            &output,
            &process::Environment::new(),
            "json",
            1000,
        )?;
        assert_eq!(record["status"], status);
        assert_eq!(record["completeness"], completeness);
        assert!(serde_json::to_vec(&record)?.len() < 65536);
        if payload.get("sources").is_some() {
            assert_eq!(array(&record["data_quality"]["sources"]).len(), 20);
            assert_eq!(record["data_quality"]["sources_truncated"], true);
            assert!(
                strings(&record["data_quality"]["warnings"])
                    .iter()
                    .any(|warning| warning.contains("first 20"))
            );
        } else if provider != "gsc" || payload.get("rows").is_some() {
            assert!(record.get("data_quality").is_none());
        }
    }
    Ok(())
}

#[test]
fn incomplete_output_hides_raw_and_escaped_secret_prefixes() {
    let secret = "pāss\"\\token";
    let values = process::Environment::from([("JENKINS_TOKEN".into(), secret.into())]);
    let escaped = serde_json::to_string(secret).expect("synthetic secret serializes");
    let ascii = canonical(&json!(secret));
    for value in [
        secret,
        &escaped[1..escaped.len() - 1],
        &ascii[1..ascii.len() - 1],
    ] {
        for cut in 1..=value.len() {
            let mut bytes = b"response: ".to_vec();
            bytes.extend_from_slice(&value.as_bytes()[..cut]);
            for status in [
                process::Status::OutputLimit,
                process::Status::Timeout,
                process::Status::Cancelled,
            ] {
                assert_eq!(
                    orgs::redact_output(&bytes, &values, status),
                    "response: [REDACTED]",
                    "{status:?}, cut {cut}"
                );
            }
        }
    }
    assert_eq!(
        orgs::redact_output("response: pā".as_bytes(), &values, process::Status::Ok),
        "response: pā"
    );
    let marker_values = process::Environment::from([
        ("JENKINS_TOKEN".into(), "D]token".into()),
        ("OTHER_TOKEN".into(), "before[REDACTED]after".into()),
    ]);
    assert_eq!(
        orgs::redact_output(
            b"response: [REDACTED]",
            &marker_values,
            process::Status::Timeout
        ),
        "response: [REDACTED]"
    );
    assert_eq!(
        orgs::redact_output(
            b"response: before[REDACTED]",
            &marker_values,
            process::Status::Timeout
        ),
        "response: [REDACTED]"
    );
    let repeated =
        process::Environment::from([("JENKINS_TOKEN".into(), format!("{}b", "a".repeat(65534)))]);
    let mut bytes = vec![b'a'; 65535];
    bytes.push(b'c');
    assert_eq!(
        orgs::redact_output(&bytes, &repeated, process::Status::OutputLimit),
        String::from_utf8(bytes).expect("fixture uses ASCII")
    );
}

#[test]
fn redaction_markers_are_stable_across_secrets_and_repeated_passes() {
    let values = process::Environment::from([
        ("JENKINS_TOKEN".into(), "ACTE".into()),
        ("OTHER_TOKEN".into(), "REDA".into()),
    ]);
    let redacted = orgs::redact("token ACTE REDA", &values);
    assert_eq!(redacted, "token [REDACTED] [REDACTED]");
    assert_eq!(orgs::redact(&redacted, &values), redacted);
    let marker_secret =
        process::Environment::from([("JENKINS_TOKEN".into(), "before[REDACTED]after".into())]);
    assert_eq!(
        orgs::redact("before[REDACTED]after", &marker_secret),
        "[REDACTED]"
    );
}

#[test]
fn redaction_expansion_cannot_make_saved_evidence_unreadable() -> Result<()> {
    let (dir, _, ctx) = fixture()?;
    let root = orgs::workspace(dir.path(), "acme")?;
    let (path, session) = create(&root, "acme", "staging", "q", "task", json!({}))?;
    let outcome = connectors::Outcome {
        connection: "jenkins-staging".into(),
        provider: "jenkins".into(),
        source_sha256: String::new(),
        argv: words(&["jenkins", "job", "list"]),
        values: process::Environment::from([("JENKINS_TOKEN".into(), "aaaa".into())]).into(),
        result: process::Output {
            status: process::Status::Ok,
            exit_code: Some(0),
            stdout: vec![b'a'; 4 * 1048576],
            stderr: vec![0xff; 3 * 1048576],
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
        8 * 1048576,
    )?;
    let records = evidence(&path)?;
    assert_eq!(records.len(), 1);
    assert_eq!(record["status"], "output_limit");
    assert_eq!(record["completeness"], "partial");
    assert!(
        record["artifacts"]["stdout"]["bytes"].as_u64().unwrap_or(0)
            + record["artifacts"]["stderr"]["bytes"].as_u64().unwrap_or(0)
            <= 8 * 1048576
    );
    for stream in ["stdout", "stderr"] {
        let content = read_text(&path.join(text(&record["artifacts"][stream]["path"])))?;
        assert!(content.len() <= 8 * 1048576);
        assert!(!content.contains("aaaa"));
    }
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

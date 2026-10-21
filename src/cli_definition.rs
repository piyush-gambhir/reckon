// Command definitions preserve the existing public CLI. Business logic lives in cli.rs.
use clap::{Arg, ArgAction, Command};

pub fn command() -> Command {
    Command::new("reckon")
    .about("Reckon: one workspace for several businesses.\n\nOrgs hold environments, tool connections, knowledge and saved work. The agent\nworks in one org and environment at a time and reaches every tool through\nreckon run, which adds that connection's credentials and keeps reads read-only.\n")
    .subcommand_required(true)
    .subcommand(secret_commands())
    .subcommand(
        Command::new("org")
        .subcommand_required(true)
        .subcommand(
            Command::new("add")
            .arg(Arg::new("org").value_parser(clap::builder::StringValueParser::new()).required(true))
            .arg(Arg::new("name").long("name").help("display name").value_parser(clap::builder::StringValueParser::new()))
            .arg(Arg::new("envs").long("envs").help("comma-separated environments (default prod,uat,dev, or the --like org's)").value_parser(clap::builder::StringValueParser::new()))
            .arg(Arg::new("production").long("production").help("comma-separated production environments (default: prod or production)").value_parser(clap::builder::StringValueParser::new()))
            .arg(Arg::new("like").long("like").help("copy another org's environments and connection layout, without secrets or resource limits").value_parser(clap::builder::StringValueParser::new()))
            .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
        )
        .subcommand(
            Command::new("list")
            .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
        )
        .subcommand(
            Command::new("show")
            .arg(Arg::new("org").value_parser(clap::builder::StringValueParser::new()).num_args(1))
            .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
        )
    )
    .subcommand(
        Command::new("use")
        .arg(Arg::new("target").help("<org>/<env>, or <env> to keep the current org").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("link")
        .arg(Arg::new("target").help("<org>/<env>").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("folder").help("the folder to link (default: the current one)").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("use").long("use").help("in this folder, reckon run <tool> means this connection (repeatable)").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("unlink")
        .arg(Arg::new("folder").help("the linked folder (default: the current one)").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("connect")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("name").help("connection name, e.g. jenkins-prod").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("provider").long("provider").help("tool (reckon connect --list shows them)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("bound").long("for-env").help("tie the connection to one environment; omit to share it across all").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("label").long("label").help("label that selects ENV inside a shared backend, e.g. prod=production (repeatable)").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("list").long("list").help("list supported tools").action(ArgAction::SetTrue))
        .arg(Arg::new("allow").long("allow").help("limit the connection to resources, e.g. repo=acme/* or site=sc-domain:acme.com (repeatable)").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("allow_any").long("allow-any").help("remove the connection's resource limits").action(ArgAction::SetTrue))
        .arg(Arg::new("account").long("account").help("the login, email or account id this connection must use, checked by reckon verify and before every write (a * pattern is allowed; '' removes it)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("remove").long("remove").help("remove the connection; its secrets file is kept for you to delete").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("login")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("connection").value_parser(clap::builder::StringValueParser::new()).required(true))
    )
    .subcommand(
        Command::new("migrate")
        .arg(Arg::new("org").long("org").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("name").long("name").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("source").long("from").help("old checkout (default: current directory)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("rename").long("rename").help("rename an environment, e.g. production=prod").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("share").long("share").help("make one connection for every environment, e.g. cubeapm when one server holds them all").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("dry_run").long("dry-run").action(ArgAction::SetTrue))
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("open")
        .arg(Arg::new("target").help("<org>/<env> (default: reckon use)").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("runtime").long("runtime").value_parser(clap::builder::StringValueParser::new()).value_parser(["claude","codex"]))
    )
    .subcommand(
        Command::new("status")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("preflight")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("doctor")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("verify")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("connections").value_parser(clap::builder::StringValueParser::new()).num_args(0..))
    )
    .subcommand(
        Command::new("run")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("target").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("session").long("session").help("save the output as evidence in this session").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("label").long("label").help("evidence label (with --session)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("timeout").long("timeout").help("seconds, at most 120").value_parser(clap::builder::StringValueParser::new()).default_value("60").allow_negative_numbers(true))
        .arg(Arg::new("max_bytes").long("max-bytes").value_parser(clap::builder::StringValueParser::new()).default_value("8388608").allow_negative_numbers(true))
        .arg(Arg::new("stdin").long("stdin").help("pass this command's standard input to the CLI").action(ArgAction::SetTrue))
        .arg(Arg::new("cache").long("cache").help("reuse an identical successful read younger than AGE (e.g. 15m, 2h, 1d)").value_parser(clap::builder::StringValueParser::new()))
    )
    .subcommand(
        Command::new("debug")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("service").long("service").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("question").long("question").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("start").long("from").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("end").long("to").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("playbook").long("playbook").value_parser(clap::builder::StringValueParser::new()).value_parser(["latency","errors","workload","changes"]).default_value("latency"))
        .arg(Arg::new("collect").long("collect").help("perform the mapped live reads now").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("investigate")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("service").long("service").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("question").long("question").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("start").long("from").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("end").long("to").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("playbook").long("playbook").value_parser(clap::builder::StringValueParser::new()).value_parser(["latency","errors","workload","changes"]).default_value("latency"))
        .arg(Arg::new("collect").long("collect").help("perform the mapped live reads now").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("monitor")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("service").long("service").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("question").long("question").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("start").long("from").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("end").long("to").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("playbook").long("playbook").value_parser(clap::builder::StringValueParser::new()).value_parser(["latency","errors","workload","changes"]).default_value("latency"))
        .arg(Arg::new("collect").long("collect").help("perform the mapped live reads now").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("analyze")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("service").long("service").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("question").long("question").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("start").long("from").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("end").long("to").help("ISO time with timezone").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("playbook").long("playbook").value_parser(clap::builder::StringValueParser::new()).value_parser(["latency","errors","workload","changes"]).default_value("latency"))
        .arg(Arg::new("collect").long("collect").help("perform the mapped live reads now").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("task")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("question").long("question").help("what this work is about (a workflow supplies one)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("workflow").long("workflow").help("start from a workflow recipe (reckon workflows lists them)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("param").long("param").help("a workflow parameter; repeat for each").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("area").long("area").help("dev, product, gtm, sales, ops or your own word").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("budget").long("budget").help("most reads this session may record (protects quotas and production)").value_parser(clap::builder::StringValueParser::new()).allow_negative_numbers(true))
        .arg(Arg::new("start").long("from").help("optional window start (ISO time with timezone)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("end").long("to").help("optional window end").value_parser(clap::builder::StringValueParser::new()))
    )
    .subcommand(
        Command::new("workflows")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("watch")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("action").value_parser(clap::builder::StringValueParser::new()).required(true).value_parser(["add","list","remove","run"]))
        .arg(Arg::new("name").value_parser(clap::builder::StringValueParser::new()).num_args(0..))
        .arg(Arg::new("connection").long("connection").help("connection or tool to read").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("items").long("items").help("dotted path to the list of items in the JSON output (default: the top level)").value_parser(clap::builder::StringValueParser::new()).default_value(""))
        .arg(Arg::new("key").long("key").help("field that identifies an item (default id)").value_parser(clap::builder::StringValueParser::new()).default_value("id"))
        .arg(Arg::new("title").long("title").help("field to show for an item").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("production").long("production").help("confirm a watch in a production environment").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("inbox")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("days").long("days").value_parser(clap::builder::StringValueParser::new()).default_value("7").allow_negative_numbers(true))
        .arg(Arg::new("all_envs").long("all-envs").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("spend")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("days").long("days").value_parser(clap::builder::StringValueParser::new()).default_value("30").allow_negative_numbers(true))
        .arg(Arg::new("all_orgs").long("all-orgs").help("every org (operator only)").action(ArgAction::SetTrue))
        .arg(Arg::new("set_budget").long("set-budget").help("warn when the org passes this many tokens (0 clears it)").value_parser(clap::builder::StringValueParser::new()).allow_negative_numbers(true))
        .arg(Arg::new("budget_days").long("budget-days").help("the budget's window in days (default 30)").value_parser(clap::builder::StringValueParser::new()).default_value("30").allow_negative_numbers(true))
    )
    .subcommand(
        Command::new("routine")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("action").value_parser(clap::builder::StringValueParser::new()).required(true).value_parser(["add","list","remove","run","install"]))
        .arg(Arg::new("name").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("workflow").long("workflow").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("param").long("param").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
        .arg(Arg::new("at").long("at").help("daily HH:MM, weekdays HH:MM or mon HH:MM (local time)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("production").long("production").help("confirm a routine in a production environment").action(ArgAction::SetTrue))
        .arg(Arg::new("timeout_minutes").long("timeout-minutes").value_parser(clap::builder::StringValueParser::new()).default_value("45").allow_negative_numbers(true))
    )
    .subcommand(
        Command::new("services")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("action").value_parser(clap::builder::StringValueParser::new()).required(true).value_parser(["list","show","validate"]))
        .arg(Arg::new("name").value_parser(clap::builder::StringValueParser::new()).num_args(1))
    )
    .subcommand(
        Command::new("capabilities")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("collect")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("operation").long("operation").value_parser(clap::builder::StringValueParser::new()).value_parser(["cubeapm.metrics.range","cubeapm.logs.search","cubeapm.traces.search","cubeapm.trace.get","grafana.metrics.range","grafana.annotations","jenkins.builds","jenkins.build.get","github.runs","github.run.get","git.show","elasticsearch.logs.search","kubernetes.pods","kubernetes.events","jira.search","nginxpm.proxy.get","nginxpm.cert.get","aws.logs.search"]))
        .arg(Arg::new("params").long("params").help("operation parameters as a JSON object").value_parser(clap::builder::StringValueParser::new()).default_value("{}"))
        .arg(Arg::new("refresh").long("refresh").action(ArgAction::SetTrue))
        .arg(Arg::new("timeout").long("timeout").value_parser(clap::builder::StringValueParser::new()).default_value("15").allow_negative_numbers(true))
        .arg(Arg::new("max_bytes").long("max-bytes").value_parser(clap::builder::StringValueParser::new()).default_value("1048576").allow_negative_numbers(true))
    )
    .subcommand(
        Command::new("check")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
    )
    .subcommand(
        Command::new("resume")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
    )
    .subcommand(
        Command::new("report")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
    )
    .subcommand(
        Command::new("note")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("kind").long("kind").value_parser(clap::builder::StringValueParser::new()).value_parser(["finding","action","hypothesis","rejected","question"]))
        .arg(Arg::new("text").long("text").help("the note, or with --withdraw why it no longer stands").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("withdraw").long("withdraw").help("withdraw a note: it stays on record but no longer counts").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("evidence").long("evidence").value_parser(clap::builder::StringValueParser::new()).num_args(0..))
        .arg(Arg::new("next_check").long("next-check").value_parser(clap::builder::StringValueParser::new()).default_value(""))
        .arg(Arg::new("follows").long("follows").help("a finding that says what happened to an action from the last review (repeatable)").value_parser(clap::builder::StringValueParser::new()).action(ArgAction::Append))
    )
    .subcommand(
        Command::new("history")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("service").long("service").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("query").long("query").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("all_envs").long("all-envs").action(ArgAction::SetTrue))
        .arg(Arg::new("limit").long("limit").value_parser(clap::builder::StringValueParser::new()).default_value("20").allow_negative_numbers(true))
    )
    .subcommand(
        Command::new("promote")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("slug").long("slug").value_parser(clap::builder::StringValueParser::new()).required(true))
    )
    .subcommand(
        Command::new("close")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("session").value_parser(clap::builder::StringValueParser::new()).required(true))
        .arg(Arg::new("outcome").long("outcome").value_parser(clap::builder::StringValueParser::new()).required(true).value_parser(["diagnosed","done","inconclusive"]))
    )
    .subcommand(
        Command::new("act")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("action").value_parser(clap::builder::StringValueParser::new()).required(true).value_parser(["prepare","apply","list","show","reconcile"]))
        .arg(Arg::new("target").help("prepare: the write operation (github.issue.create, jira.issue.create); apply/show: the action ID").value_parser(clap::builder::StringValueParser::new()).num_args(1))
        .arg(Arg::new("params").long("params").help("prepare: parameters as a JSON object").value_parser(clap::builder::StringValueParser::new()).default_value("{}"))
        .arg(Arg::new("body_stdin").long("body-stdin").help("prepare: read the long text (GitHub body, Jira description) from standard input").action(ArgAction::SetTrue))
        .arg(Arg::new("session").long("session").help("prepare: the session this write comes from, so its next review sees the receipt").value_parser(clap::builder::StringValueParser::new()))
    )
    .subcommand(
        Command::new("brief")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("days").long("days").value_parser(clap::builder::StringValueParser::new()).default_value("7").allow_negative_numbers(true))
        .arg(Arg::new("save").long("save").help("also write it to the org folder's briefs/<date>-<env>.md").action(ArgAction::SetTrue))
        .arg(Arg::new("all_envs").long("all-envs").help("every environment of the org").action(ArgAction::SetTrue))
        .arg(Arg::new("all_orgs").long("all-orgs").help("every org and environment, one digest (operator only)").action(ArgAction::SetTrue))
    )
    .subcommand(
        Command::new("log")
        .arg(Arg::new("org").long("org").help("org (defaults to the agent's pinned org, then the folder's link, then reckon use)").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("env").long("env").help("environment within the org").value_parser(clap::builder::StringValueParser::new()))
        .arg(Arg::new("json").long("json").help("structured output").action(ArgAction::SetTrue))
        .arg(Arg::new("days").long("days").value_parser(clap::builder::StringValueParser::new()).default_value("7").allow_negative_numbers(true))
        .arg(Arg::new("event").long("event").value_parser(clap::builder::StringValueParser::new()).value_parser(["run","refused","verify","act.prepare","act.apply","act.reconcile","routine","watch"]))
        .arg(Arg::new("all_envs").long("all-envs").action(ArgAction::SetTrue))
        .arg(Arg::new("limit").long("limit").value_parser(clap::builder::StringValueParser::new()).default_value("50").allow_negative_numbers(true))
    )
    .subcommand(
        Command::new("demo")
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue))
    )
}

fn secret_commands() -> Command {
    let setup = Command::new("setup")
        .about("Validate service accounts restricted to one 1Password vault")
        .arg(Arg::new("vault").long("vault").required(true))
        .arg(
            Arg::new("reader")
                .long("reader")
                .required(true)
                .help("keychain:<item> containing the vault-only reader token"),
        )
        .arg(
            Arg::new("writer")
                .long("writer")
                .help("keychain:<item> containing a separate vault-only writer token"),
        )
        .arg(
            Arg::new("op")
                .long("op")
                .default_value("op")
                .help("1Password CLI binary, stable 2.41.0 or newer"),
        )
        .arg(Arg::new("json").long("json").action(ArgAction::SetTrue));
    let mut command = Command::new("secrets")
        .about("Store credentials in a single restricted 1Password vault")
        .subcommand_required(true)
        .subcommand(setup);
    for action in [
        "status",
        "set",
        "link",
        "import",
        "check",
        "recover",
        "benchmark",
    ] {
        let mut item = Command::new(action)
            .arg(Arg::new("org").long("org"))
            .arg(Arg::new("env").long("env"))
            .arg(Arg::new("json").long("json").action(ArgAction::SetTrue));
        if action != "status" {
            item = item.arg(Arg::new("connection").required(true));
        }
        if ["set", "link"].contains(&action) {
            item = item.arg(Arg::new("key").required(true));
        }
        if action == "set" {
            item = item.arg(
                Arg::new("update_linked")
                    .long("update-linked")
                    .action(ArgAction::SetTrue),
            );
        }
        if action == "link" {
            item = item
                .arg(Arg::new("item").long("item").required(true))
                .arg(Arg::new("field").long("field").required(true));
        }
        if action == "import" {
            item = item
                .arg(Arg::new("key").long("key").action(ArgAction::Append))
                .arg(
                    Arg::new("dry_run")
                        .long("dry-run")
                        .action(ArgAction::SetTrue),
                );
        }
        if action == "benchmark" {
            item = item.arg(Arg::new("samples").long("samples").default_value("5"));
        }
        command = command.subcommand(item);
    }
    command
}

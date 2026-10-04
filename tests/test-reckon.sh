#!/usr/bin/env bash

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_TMP="$(mktemp -d)"
ORIGINAL_PATH="$PATH"
TEST_INDEX=0

cleanup() {
    rm -rf "$TEST_TMP"
}
trap cleanup EXIT

new_fixture() {
    TEST_INDEX=$((TEST_INDEX + 1))
    FIXTURE="$TEST_TMP/fixture-$TEST_INDEX"
    mkdir -p "$FIXTURE/scripts/lib" "$FIXTURE/bin"
    mkdir -p "$FIXTURE/infra-knowledge/_shared" "$FIXTURE/infra-knowledge/staging"
    mkdir -p "$FIXTURE/incidents" "$FIXTURE/.config/staging"
    cp "$SOURCE_ROOT/.envrc" "$FIXTURE/.envrc"
    cp "$SOURCE_ROOT/scripts/reckon" "$FIXTURE/scripts/reckon"
    cp "$SOURCE_ROOT/scripts/agent.sh" "$FIXTURE/scripts/agent.sh"
    cp "$SOURCE_ROOT/scripts/lib/integrations.sh" "$FIXTURE/scripts/lib/integrations.sh"
    cp "$SOURCE_ROOT/scripts/integrations.tsv" "$FIXTURE/scripts/integrations.tsv"
    cp "$SOURCE_ROOT/scripts/bounded.py" "$FIXTURE/scripts/bounded.py"
    cp -R "$SOURCE_ROOT/scripts/reckonlib" "$FIXTURE/scripts/reckonlib"
    chmod +x "$FIXTURE/scripts/reckon" "$FIXTURE/scripts/agent.sh"
    PATH="$FIXTURE/bin:$ORIGINAL_PATH"
    export PATH
    unset RECKON_ENV RECKON_ROOT DIRENV_DIR XDG_CONFIG_HOME
}

assert_contains() {
    local haystack="$1" needle="$2"
    case "$haystack" in
        *"$needle"*) ;;
        *)
            printf "assertion failed: expected output to contain: %s\n" "$needle" >&2
            printf "%s\n" "$haystack" >&2
            return 1
            ;;
    esac
}

write_selection() {
    printf '%s\n' "${1:-staging}" > "$FIXTURE/.reckon-env"
}

write_staging_env() {
    printf '%s\n' \
        'GRAFANA_URL=https://grafana.internal' \
        'GRAFANA_TOKEN=test-token' \
        > "$FIXTURE/.env.staging"
}

make_fake() {
    local name="$1" body="$2"
    {
        printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
        printf '%s\n' "$body"
    } > "$FIXTURE/bin/$name"
    chmod +x "$FIXTURE/bin/$name"
}

test_requires_explicit_environment() {
    new_fixture
    local output
    if output="$("$FIXTURE/scripts/reckon" preflight 2>&1)"; then
        printf "preflight unexpectedly succeeded without an environment\n" >&2
        return 1
    fi
    assert_contains "$output" "environment failed to resolve"
}

test_preflight_reports_local_configuration() {
    new_fixture
    write_selection
    write_staging_env
    make_fake grafana 'exit 0'
    local output
    output="$("$FIXTURE/scripts/reckon" preflight)"
    assert_contains "$output" "configured:  grafana"
    assert_contains "$output" "configured is local state"
}

test_saved_profile_counts_as_configuration() {
    new_fixture
    write_selection
    mkdir -p "$FIXTURE/.config/staging/gh"
    printf '%s\n' 'github.internal:' > "$FIXTURE/.config/staging/gh/hosts.yml"
    make_fake gh 'exit 0'
    local output
    output="$("$FIXTURE/scripts/reckon" preflight)"
    assert_contains "$output" "configured:  github"
}

test_placeholders_are_not_configuration() {
    new_fixture
    write_selection
    printf '%s\n' \
        'GRAFANA_URL=https://grafana.example.com' \
        'GRAFANA_TOKEN=replace_me' \
        > "$FIXTURE/.env.staging"
    make_fake grafana 'exit 0'
    local output
    output="$("$FIXTURE/scripts/reckon" preflight)"
    assert_contains "$output" "grafana(no creds)"
}

test_invalid_environment_fails_closed() {
    new_fixture
    printf '%s\n' 'prod' > "$FIXTURE/.reckon-env"
    local output
    if output="$("$FIXTURE/scripts/reckon" status 2>&1)"; then
        printf "status unexpectedly accepted an invalid environment\n" >&2
        return 1
    fi
    assert_contains "$output" "environment failed to resolve"
}

test_verify_rejects_unknown_target() {
    new_fixture
    write_selection
    local output
    if output="$("$FIXTURE/scripts/reckon" verify not-real 2>&1)"; then
        printf "verify unexpectedly accepted an unknown target\n" >&2
        return 1
    fi
    assert_contains "$output" "unknown integration: not-real"
}

test_targeted_skip_fails() {
    new_fixture
    write_selection
    local output
    if output="$("$FIXTURE/scripts/reckon" verify mysql 2>&1)"; then
        printf "targeted verify unexpectedly succeeded when mysql was unavailable\n" >&2
        return 1
    fi
    assert_contains "$output" "skipped"
}

test_untargeted_verify_runs() {
    new_fixture
    write_selection
    local output
    # macOS ships Bash 3.2 as /bin/bash, where nounset rejects empty array expansions.
    output="$(/bin/bash "$FIXTURE/scripts/reckon" verify 2>&1)"
    assert_contains "$output" "0 failed"
}

test_kafka_probe_uses_security_settings() {
    new_fixture
    write_selection
    printf '%s\n' \
        'KAFKA_BOOTSTRAP_SERVERS=broker.internal:9092' \
        'KAFKA_SECURITY_PROTOCOL=SASL_SSL' \
        'KAFKA_SASL_MECHANISM=SCRAM-SHA-512' \
        'KAFKA_SASL_USERNAME=reader' \
        'KAFKA_SASL_PASSWORD=test-password' \
        > "$FIXTURE/.env.staging"
    export RECKON_TEST_LOG="$FIXTURE/kcat-args.txt"
    make_fake kcat 'printf "%s\n" "$@" > "$RECKON_TEST_LOG"'

    "$FIXTURE/scripts/reckon" verify kafka >/dev/null
    local args
    args="$(tr '\n' ' ' < "$RECKON_TEST_LOG")"
    assert_contains "$args" "security.protocol=SASL_SSL"
    assert_contains "$args" "sasl.mechanism=SCRAM-SHA-512"
    assert_contains "$args" "sasl.username=reader"
    assert_contains "$args" "sasl.password=test-password"
}

test_agent_launcher_activates_environment() {
    new_fixture
    write_selection
    write_staging_env
    make_fake codex 'printf "env=%s\nroot=%s\ntoken=%s\n" "$RECKON_ENV" "$RECKON_ROOT" "$GRAFANA_TOKEN"'
    local output
    output="$("$FIXTURE/scripts/agent.sh" codex)"
    assert_contains "$output" "env=staging"
    assert_contains "$output" "root=$FIXTURE"
    assert_contains "$output" "token=test-token"
}

test_env_output_works_outside_repo() {
    new_fixture
    write_selection
    write_staging_env
    local output
    output="$(
        cd /
        eval "$("$FIXTURE/scripts/reckon" env)"
        printf '%s|%s\n' "$RECKON_ENV" "$XDG_CONFIG_HOME"
    )"
    assert_contains "$output" "staging|$FIXTURE/.config/staging"
}

test_preflight_handles_fresh_clone_without_knowledge() {
    new_fixture
    write_selection
    rm -rf "$FIXTURE/infra-knowledge" "$FIXTURE/incidents"
    local output
    output="$("$FIXTURE/scripts/reckon" preflight)"
    assert_contains "$output" "no infra-knowledge for staging"
    assert_contains "$output" "none recorded yet"
}

test_control_surface_ignores_another_clone_root() {
    new_fixture
    write_selection
    write_staging_env
    make_fake grafana 'exit 0'
    local output
    output="$(RECKON_ROOT=/nonexistent/other-clone "$FIXTURE/scripts/reckon" preflight)"
    assert_contains "$output" "configured:  grafana"
}

tests=(
    test_requires_explicit_environment
    test_preflight_reports_local_configuration
    test_saved_profile_counts_as_configuration
    test_placeholders_are_not_configuration
    test_invalid_environment_fails_closed
    test_verify_rejects_unknown_target
    test_targeted_skip_fails
    test_untargeted_verify_runs
    test_kafka_probe_uses_security_settings
    test_agent_launcher_activates_environment
    test_env_output_works_outside_repo
    test_preflight_handles_fresh_clone_without_knowledge
    test_control_surface_ignores_another_clone_root
)

for test_name in "${tests[@]}"; do
    "$test_name"
    printf "ok - %s\n" "$test_name"
done

printf "%s tests passed\n" "${#tests[@]}"

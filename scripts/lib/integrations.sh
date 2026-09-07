#!/usr/bin/env bash
#
# Integration registry and safe connection probes shared by the reckon control
# surface and its tests. This file is sourced; callers provide REPO_ROOT and
# XDG_CONFIG_HOME through the normal environment-loading path.

reckon_registry() {
    cat "$REPO_ROOT/scripts/integrations.tsv"
}

reckon_have() {
    command -v "$1" >/dev/null 2>&1
}

# Keep these tokens aligned with .env.example.
reckon_is_placeholder() {
    case "$1" in
        replace_me|REPLACE_ME|*replace_me*) return 0 ;;
        *example.com*|*.example.*|*@example*) return 0 ;;
        *xxxx*|*XXXX*|*changeme*|*CHANGEME*|*your-*|'<'*'>') return 0 ;;
        *) return 1 ;;
    esac
}

# "," means every group is required; "/" means any variable in that group.
reckon_vars_satisfied() {
    local spec="$1" group variable value hit
    local -a groups variables
    [ "$spec" = "-" ] && return 1

    IFS=',' read -r -a groups <<< "$spec"
    for group in "${groups[@]}"; do
        hit=0
        IFS='/' read -r -a variables <<< "$group"
        for variable in "${variables[@]}"; do
            value="${!variable-}"
            if [ -n "$value" ] && ! reckon_is_placeholder "$value"; then
                hit=1
                break
            fi
        done
        [ "$hit" -eq 1 ] || return 1
    done
}

# ";" separates alternative saved-profile files.
reckon_config_satisfied() {
    local spec="$1" candidate
    [ "$spec" = "-" ] && return 1
    [ -n "${XDG_CONFIG_HOME:-}" ] || return 1

    while IFS= read -r candidate; do
        [ -n "$candidate" ] && [ -s "$XDG_CONFIG_HOME/$candidate" ] && return 0
    done < <(printf '%s\n' "$spec" | tr ';' '\n')
    return 1
}

reckon_is_configured() {
    local required="$1" config_files="$2"
    reckon_vars_satisfied "$required" || reckon_config_satisfied "$config_files"
}

reckon_integration_exists() {
    local wanted="$1" name _binary _required _config_files
    while IFS='|' read -r name _binary _required _config_files; do
        [ "$name" = "$wanted" ] && return 0
    done < <(reckon_registry)
    return 1
}

reckon_validate_integrations() {
    local wanted
    for wanted in "$@"; do
        reckon_integration_exists "$wanted" || {
            printf "unknown integration: %s\n" "$wanted" >&2
            printf "valid integrations: " >&2
            reckon_registry | cut -d'|' -f1 | paste -sd' ' - >&2
            return 1
        }
    done
}

reckon_run_bounded() {
    reckon_have python3 || { printf 'python3 is required for bounded verification\n' >&2; return 1; }
    python3 "$REPO_ROOT/scripts/bounded.py" "$@"
}

reckon_verify_one() {
    local name="$1"
    local -a args

    case "$name" in
        grafana)
            reckon_run_bounded grafana user current -o json
            ;;
        jenkins)
            reckon_run_bounded jenkins status -o json
            ;;
        cubeapm)
            reckon_run_bounded cubeapm metrics label-values service -o json
            ;;
        github)
            reckon_run_bounded gh auth status
            ;;
        aws)
            reckon_run_bounded aws sts get-caller-identity --output json
            ;;
        kubernetes)
            reckon_run_bounded kubectl get ns -o name
            ;;
        kafka)
            args=(-L -b "$KAFKA_BOOTSTRAP_SERVERS" -J)
            [ -z "${KAFKA_SECURITY_PROTOCOL:-}" ] ||
                args+=(-X "security.protocol=$KAFKA_SECURITY_PROTOCOL")
            [ -z "${KAFKA_SASL_MECHANISM:-}" ] ||
                args+=(-X "sasl.mechanism=$KAFKA_SASL_MECHANISM")
            [ -z "${KAFKA_SASL_USERNAME:-}" ] ||
                args+=(-X "sasl.username=$KAFKA_SASL_USERNAME")
            [ -z "${KAFKA_SASL_PASSWORD:-}" ] ||
                args+=(-X "sasl.password=$KAFKA_SASL_PASSWORD")
            reckon_run_bounded kcat "${args[@]}"
            ;;
        kafka-rpk)
            reckon_run_bounded rpk cluster info --brokers "$KAFKA_BOOTSTRAP_SERVERS"
            ;;
        redis)
            reckon_run_bounded redis-cli -u "$REDIS_URL" PING
            ;;
        mongodb)
            reckon_run_bounded mongosh "$MONGODB_URI" --quiet \
                --eval 'db.runCommand({ping:1})'
            ;;
        postgres)
            reckon_run_bounded psql -c 'SELECT 1'
            ;;
        mysql)
            reckon_run_bounded mysql \
                --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf" -e 'SELECT 1'
            ;;
        clickhouse)
            args=(client
                --host "$CLICKHOUSE_HOST"
                --port "${CLICKHOUSE_PORT:-9440}"
                --user "$CLICKHOUSE_USER"
                --password "$CLICKHOUSE_PASSWORD")
            [ "${CLICKHOUSE_SECURE:-1}" = "0" ] || args+=(--secure)
            [ -z "${CLICKHOUSE_DATABASE:-}" ] || args+=(--database "$CLICKHOUSE_DATABASE")
            args+=(--readonly=1 --query 'SELECT 1')
            reckon_run_bounded clickhouse "${args[@]}"
            ;;
        jira)
            reckon_run_bounded jira whoami -o json
            ;;
        nginxpm)
            reckon_run_bounded nginxpm proxy list -o json
            ;;
        elasticsearch)
            reckon_run_bounded es cluster health -o json
            ;;
        *)
            return 127
            ;;
    esac
}

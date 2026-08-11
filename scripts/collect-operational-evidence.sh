#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OUTPUT=""
HISTORY=""
TEST_MODE="${OPEN_CLOUD_EVIDENCE_TEST_MODE:-0}"

usage() {
    echo "Usage:"
    echo "  collect-operational-evidence.sh"
    echo "  collect-operational-evidence.sh --output FILE"
    echo "  collect-operational-evidence.sh --append-history FILE"
    echo "  collect-operational-evidence.sh --output FILE --append-history FILE"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            shift
            OUTPUT="${1:-}"
            ;;
        --append-history)
            shift
            HISTORY="${1:-}"
            ;;
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac

    shift || true
done

TMP="$(mktemp)"
DOCTOR_LOG="$(mktemp)"

cleanup() {
    rm -f "$TMP" "$DOCTOR_LOG"
}

trap cleanup EXIT

unit_active() {
    local unit="$1"

    systemctl --user is-active "$unit" 2>/dev/null         || printf "%s" "unknown"
}

unit_enabled() {
    local unit="$1"

    systemctl --user is-enabled "$unit" 2>/dev/null         || printf "%s" "unknown"
}

if [ "$TEST_MODE" = "1" ]; then
    COLLECTED_AT="2026-08-11T00:00:00Z"
    OS_NAME="Ubuntu 24.04 LTS"
    ARCH="aarch64"
    KERNEL="6.x-test"
    HOST_UPTIME_SECONDS=172800
    REPO_SHA="test000"
    DOCTOR_STATUS="PASS"

    REGISTRY_ACTIVE="active"
    REGISTRY_ENABLED="enabled"

    VERIFIER_ACTIVE="active"
    VERIFIER_ENABLED="enabled"

    GATEWAY_ACTIVE="active"
    GATEWAY_ENABLED="enabled"

    LINGER="yes"

    SCHEDULED_SUCCESSES=4
    SCHEDULED_FAILURES=0
    REPAIR_BACKUPS=1

    EVIDENCE_SCOPE="Synthetic smoke-test fixture"
else
    COLLECTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    if [ -r /etc/os-release ]; then
        . /etc/os-release
        OS_NAME="${PRETTY_NAME:-Ubuntu}"
    else
        OS_NAME="Linux"
    fi

    ARCH="$(uname -m)"
    KERNEL="$(uname -r)"

    HOST_UPTIME_RAW="$(
        cut -d " " -f1 /proc/uptime
    )"

    HOST_UPTIME_SECONDS="${HOST_UPTIME_RAW%%.*}"

    REPO_SHA="$(
        git -C "$ROOT" rev-parse --short HEAD
    )"

    if "$ROOT/bin/opencloud" doctor         > "$DOCTOR_LOG" 2>&1
    then
        DOCTOR_STATUS="PASS"
    else
        DOCTOR_STATUS="FAIL"
    fi

    REGISTRY_ACTIVE="$(
        unit_active hermes-fleet-registry.timer
    )"

    REGISTRY_ENABLED="$(
        unit_enabled hermes-fleet-registry.timer
    )"

    VERIFIER_ACTIVE="$(
        unit_active hermes-fleet-verifier.timer
    )"

    VERIFIER_ENABLED="$(
        unit_enabled hermes-fleet-verifier.timer
    )"

    GATEWAY_ACTIVE="$(
        unit_active hermes-gateway.service
    )"

    GATEWAY_ENABLED="$(
        unit_enabled hermes-gateway.service
    )"

    LINGER="$(
        loginctl show-user "$USER"             -p Linger             --value             2>/dev/null             || printf "%s" "unknown"
    )"

    AGENT_LOG="$HOME/.hermes/logs/agent.log"

    if [ -f "$AGENT_LOG" ]; then
        SCHEDULED_SUCCESSES="$(
            grep -Ec                 "cron\.scheduler: Job .* completed successfully"                 "$AGENT_LOG"                 || true
        )"

        SCHEDULED_FAILURES="$(
            grep -Ec                 "cron\.scheduler: Job .* failed"                 "$AGENT_LOG"                 || true
        )"
    else
        SCHEDULED_SUCCESSES=0
        SCHEDULED_FAILURES=0
    fi

    REPAIR_BACKUP_ROOT="$HOME/.local/share/opencloud-repair/backups"

    if [ -d "$REPAIR_BACKUP_ROOT" ]; then
        REPAIR_BACKUPS="$(
            find "$REPAIR_BACKUP_ROOT"                 -mindepth 1                 -maxdepth 1                 -type d                 | wc -l
        )"
    else
        REPAIR_BACKUPS=0
    fi

    EVIDENCE_SCOPE="Real local runtime state plus aggregate counters from the current Hermes log"
fi

HOST_UPTIME_DAYS=$((HOST_UPTIME_SECONDS / 86400))
HOST_UPTIME_HOURS=$((HOST_UPTIME_SECONDS / 3600))

{
    echo "# Operational Evidence Snapshot"
    echo
    echo "Collected: $COLLECTED_AT"
    echo
    echo "## Host"
    echo
    echo "- Operating system: $OS_NAME"
    echo "- Architecture: $ARCH"
    echo "- Kernel: $KERNEL"
    echo "- Host uptime at observation: ${HOST_UPTIME_SECONDS} seconds (${HOST_UPTIME_HOURS} hours / ${HOST_UPTIME_DAYS} full days)"
    echo "- Open Cloud Assistant commit: $REPO_SHA"
    echo
    echo "## Runtime health"
    echo
    echo "- OpenCloud doctor: $DOCTOR_STATUS"
    echo "- Fleet registry timer: active=$REGISTRY_ACTIVE enabled=$REGISTRY_ENABLED"
    echo "- Fleet verifier timer: active=$VERIFIER_ACTIVE enabled=$VERIFIER_ENABLED"
    echo "- Hermes gateway service: active=$GATEWAY_ACTIVE enabled=$GATEWAY_ENABLED"
    echo "- User linger: $LINGER"
    echo
    echo "## Observed operational counters"
    echo
    echo "- Successful scheduled-job completions observed in current Hermes log: $SCHEDULED_SUCCESSES"
    echo "- Failed scheduled-job completions observed in current Hermes log: $SCHEDULED_FAILURES"
    echo "- Trusted self-repair backup directories currently retained: $REPAIR_BACKUPS"
    echo
    echo "## Evidence scope"
    echo
    echo "$EVIDENCE_SCOPE."
    echo
    echo "These values are observations, not an uptime SLA or SLO."
    echo
    echo "The collector intentionally does not publish raw application logs."
    echo
    echo "## Privacy boundary"
    echo
    echo "The collector does not emit:"
    echo
    echo "- prompts or assistant responses;"
    echo "- job names or job payloads;"
    echo "- Vellum personal memory;"
    echo "- career or identity data;"
    echo "- API keys or provider credentials;"
    echo "- IP addresses;"
    echo "- usernames or home-directory paths;"
    echo "- session identifiers;"
    echo "- raw Fleet databases;"
    echo "- raw Hermes logs."
} > "$TMP"

if grep -Eq     "(/home/|ocid1\.|100\.[0-9]+\.[0-9]+\.[0-9]+|([0-9]{1,3}\.){3}[0-9]{1,3}|API[_-]?KEY|TOKEN=|SECRET=|PRIVATE KEY|cron_[A-Za-z0-9]|career target|CloudMind|Carbontrace|H1KARI)"     "$TMP"
then
    echo "ERROR: evidence privacy guard rejected generated output" >&2
    exit 1
fi

if [ -n "$OUTPUT" ]; then
    mkdir -p "$(dirname "$OUTPUT")"
    cp "$TMP" "$OUTPUT"
    echo "EVIDENCE_OUTPUT: $OUTPUT"
else
    cat "$TMP"
fi

if [ -n "$HISTORY" ]; then
    mkdir -p "$(dirname "$HISTORY")"

    if [ ! -f "$HISTORY" ]; then
        {
            echo "# Operational Evidence History"
            echo
            echo "Sanitized observations collected from real or explicitly marked test environments."
            echo
            echo "These observations are not an SLA, SLO, or claim of uninterrupted availability between collection points."
            echo
            echo "| Collected UTC | Architecture | Host uptime hours | Doctor | Registry timer | Verifier timer | Gateway | Scheduled successes | Scheduled failures |"
            echo "| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |"
        } > "$HISTORY"
    fi

    printf "| %s | %s | %s | %s | %s/%s | %s/%s | %s/%s | %s | %s |\n"         "$COLLECTED_AT"         "$ARCH"         "$HOST_UPTIME_HOURS"         "$DOCTOR_STATUS"         "$REGISTRY_ACTIVE"         "$REGISTRY_ENABLED"         "$VERIFIER_ACTIVE"         "$VERIFIER_ENABLED"         "$GATEWAY_ACTIVE"         "$GATEWAY_ENABLED"         "$SCHEDULED_SUCCESSES"         "$SCHEDULED_FAILURES"         >> "$HISTORY"

    echo "EVIDENCE_HISTORY: $HISTORY"
fi

echo "OPERATIONAL_EVIDENCE: PASS"

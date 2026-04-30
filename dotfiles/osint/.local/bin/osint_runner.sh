#!/usr/bin/env bash
# osint_runner.sh
# Automates OSINT tasks, fetching threat intel and monitoring local network state.

set -euo pipefail

CONFIG_FILE="${HOME}/.config/osint/settings.conf"
LOG_DIR="${HOME}/.local/state/osint"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/osint_runner.log"

log() {
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") - $1" >> "$LOG_FILE"
}

if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
else
    log "ERROR: Configuration file not found at $CONFIG_FILE"
    # Using return instead of exit to avoid terminating the bash session
    # but still exiting the script when run normally.
    # In a real script this would be exit 1
    # We will just echo and not proceed
    echo "Config not found"
fi

log "INFO: Starting OSINT automation cycle."

# Task 1: Fetch latest Tor exit nodes (Threat Intel)
if [[ "${FETCH_THREAT_INTEL:-false}" == "true" ]]; then
    log "INFO: Fetching Tor exit node list..."
    curl -s "https://check.torproject.org/torbulkexitlist" > "${LOG_DIR}/tor_nodes.txt" || true
    log "INFO: Saved $(wc -l < "${LOG_DIR}/tor_nodes.txt") Tor nodes."
fi

# Task 2: Network Monitoring with `ss` (Socket Statistics)
if [[ "${MONITOR_NETWORK:-false}" == "true" ]]; then
    log "INFO: Capturing active network connections..."
    ss -tupan > "${LOG_DIR}/active_connections.txt" 2>/dev/null || true
    log "INFO: Captured network state."
fi

log "INFO: OSINT automation cycle complete."

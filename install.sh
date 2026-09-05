#!/usr/bin/env bash
# Hirey Hi installer for Hermes Agent.
#
# Usage:
#   curl -fsSL https://hi.hirey.ai/v1/install/hermes.sh | bash
#
#   …or, the canonical Hermes path:
#     hermes plugins install hirey-ai/hirey-hermes-plugin
#
# What this does:
#   1. `hermes plugins install hirey-ai/hirey-hermes-plugin --enable` (the
#      Hermes-native distribution path; clones the repo into
#      ~/.hermes/plugins/hirey-hi and enables it).
#   2. Drop the bundled SKILL.md files into ~/.hermes/skills/communication/
#      so they appear in <available_skills> at the next session start.
#   3. Anonymously register an agent identity at ~/.config/hi/credentials.json
#      (idempotent — keeps existing creds).
#
# Env overrides:
#   HI_BASE         — Hi platform base URL (default: https://hi.hirey.ai)
#   PLUGIN_REPO     — git repo or owner/name (default: hirey-ai/hirey-hermes-plugin)
#   HERMES_HOME     — Hermes home dir (default: ~/.hermes)
#   HI_CHANNEL_CODE — legacy input; nonempty values block new registration because
#                     the modern API-key bootstrap cannot persist attribution.
#
# Idempotent: re-running is safe.

set -euo pipefail
umask 077

HI_BASE_EXPLICIT="${HI_BASE:-}"
HI_BASE="${HI_BASE:-https://hi.hirey.ai}"
HI_BASE="${HI_BASE%/}"
PLUGIN_REPO="${PLUGIN_REPO:-hirey-ai/hirey-hermes-plugin}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_CATEGORY="${SKILLS_CATEGORY:-communication}"
SKILLS_DIR="$HERMES_HOME/skills/$SKILLS_CATEGORY"
CRED_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hi"
CRED_FILE="$CRED_DIR/credentials.json"

CYAN='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; DIM='\033[2m'; NC='\033[0m'
# (RED is used in the "/reset is NOT sufficient" banner at end-of-install)
step() { printf "${CYAN}▶${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1" >&2; }
fail() { printf "${RED}✗${NC} %s\n" "$1" >&2; exit 1; }

# ─── Preflight ───────────────────────────────────────────────────────────
for bin in curl jq; do
  command -v "$bin" >/dev/null 2>&1 || fail "$bin not found in PATH"
done
command -v hermes >/dev/null 2>&1 \
  || fail "'hermes' command not found in PATH. Install Hermes first (https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)."

# Claude and Hermes share this identity. A broken file must never be treated
# as a new installation, and concurrent installers must not create two agents.
mkdir -p "$CRED_DIR" && chmod 700 "$CRED_DIR"
LOCK_DIR="$CRED_DIR/.register.lock"
LOCK_WAIT=0
until mkdir "$LOCK_DIR" 2>/dev/null; do
  LOCK_WAIT=$((LOCK_WAIT + 1))
  [ "$LOCK_WAIT" -lt 30 ] || fail "Another Hi installer holds $LOCK_DIR; retry after it completes."
  sleep 1
done
STAGED_CRED=""
cleanup() {
  if [ -n "$STAGED_CRED" ]; then rm -f "$STAGED_CRED"; fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
if [ -e "$CRED_FILE" ] || [ -L "$CRED_FILE" ]; then
  jq -e 'type == "object" and
    (.client_id | type == "string" and length > 0) and
    (.client_secret | type == "string" and length > 0) and
    (.audience | type == "string" and length > 0)' "$CRED_FILE" >/dev/null 2>&1 \
    || fail "Existing Hi credentials are unusable; restore them before retrying. Refusing to register another identity."
  STORED_BASE=$(jq -er '.platform_base_url // "https://hi.hirey.ai" | select(type == "string" and length > 0)' "$CRED_FILE") \
    || fail "Stored Hi environment is invalid."
  STORED_BASE="${STORED_BASE%/}"
  if [ -n "$HI_BASE_EXPLICIT" ] && [ "$HI_BASE" != "$STORED_BASE" ]; then
    fail "HI_BASE differs from the existing credential environment; use a separate XDG_CONFIG_HOME."
  fi
  HI_BASE="$STORED_BASE"
fi
printf '%s' "$HI_BASE" | jq -R -e 'test("^https://[^/?#@]+$|^http://(localhost|127\\.0\\.0\\.1|\\[::1\\])(:[0-9]+)?$")' >/dev/null \
  || fail "Hi requires HTTPS except for an explicit loopback test endpoint."

step "Installing hirey-hi for Hermes Agent"

# ─── 1. Hermes plugin install ────────────────────────────────────────────
# `--force` in hermes-agent CLI is documented as "Remove existing plugin and
# reinstall": harmless when nothing's installed, recovers cleanly from stale
# state (e.g. user `rm -rf`'d ~/.hermes/plugins/hirey-hi/ but config.yaml
# still references it — in that state `hermes plugins list` reports absent
# but `hermes plugins install` rejects with "Plugin 'hirey-hi' already exists"
# and a plain install dies). One unconditional --force is more robust than
# a fragile list-output grep gating install vs update — the table-formatted
# `plugins list` output is not a stable parsing surface anyway.
HERMES_HOME="$HERMES_HOME" hermes plugins install "$PLUGIN_REPO" --force --enable \
  || fail "hermes plugins install $PLUGIN_REPO failed"
ok "Plugin installed + enabled"

PLUGIN_DIR="$HERMES_HOME/plugins/hirey-hi"
[ -d "$PLUGIN_DIR" ] || fail "Plugin directory missing: $PLUGIN_DIR"

# ─── 2. Drop SKILL.md files into the user's skill tree ───────────────────
step "Installing SKILL.md files into $SKILLS_DIR"
mkdir -p "$SKILLS_DIR"
for name in hi-onboard hi-use hi-events hi-repair; do
  if [ -f "$PLUGIN_DIR/skills/$name/SKILL.md" ]; then
    mkdir -p "$SKILLS_DIR/$name"
    cp "$PLUGIN_DIR/skills/$name/SKILL.md" "$SKILLS_DIR/$name/SKILL.md"
  fi
done
ok "Skills installed (hi-onboard, hi-use, hi-events, hi-repair)"

# ─── 3. Anonymous Hi identity ────────────────────────────────────────────
step "Bootstrapping anonymous Hi identity at $CRED_FILE"
mkdir -p "$CRED_DIR" && chmod 700 "$CRED_DIR"

if [ ! -f "$CRED_FILE" ]; then
  [ -z "${HI_CHANNEL_CODE:-}" ] || fail "Referral channel metadata is not supported by current bootstrap; no registration was attempted."
  PENDING_FILE="$CRED_DIR/.registration-pending.json"
  [ ! -e "$PENDING_FILE" ] || fail "Previous registration outcome is uncertain; reconcile it before retrying."
  # The server does not promise idempotent registration. Keep this non-secret
  # marker on every failure, including timeouts and malformed success responses.
  printf '%s\n' '{"status":"outcome_unknown","host":"hermes"}' > "$PENDING_FILE"
  REG_BODY='{"agent_type":"hermes","client_version":"0.2.4","display_name":"Hermes Agent (hirey-hi installer)"}'
  REG=$(curl -fsS --connect-timeout 5 --max-time 30 -X POST "$HI_BASE/v1/agents/api-keys" \
    -H 'content-type: application/json' \
    --data "$REG_BODY") \
    || fail "Registration outcome uncertain; reconcile the pending attempt before retrying."
  STAGED_CRED=$(mktemp "$CRED_DIR/credentials.stage.XXXXXX")
  printf '%s' "$REG" | jq -e --arg base "$HI_BASE" '
    . as $body | select(type == "object" and .status == "pending"
      and (.agent_id | type == "string" and length > 0)
      and (.api_key | type == "string" and test("^hi_ak_[A-Za-z0-9_-]+$"))) |
    (.api_key[6:] | gsub("-"; "+") | gsub("_"; "/") |
      . + ("=" * ((4 - (length % 4)) % 4)) | @base64d | fromjson) as $key |
    select($key | type == "object" and .v == 1
      and (.id | type == "string" and length > 0 and length <= 100)
      and (.secret | type == "string" and length > 0 and length <= 500)) |
    {client_id:$key.id, client_secret:$key.secret, agent_id:$body.agent_id,
     status:"pending", audience:"hirey-hi", token_url:($base + "/oauth/token"),
     platform_base_url:$base, access_token:null,
     access_token_issued_at:0, access_token_expires_in:0}
  ' > "$STAGED_CRED" 2>/dev/null \
    || fail "Invalid registration response; reconcile the pending attempt before retrying."
  chmod 600 "$STAGED_CRED"
  mv "$STAGED_CRED" "$CRED_FILE"
  STAGED_CRED=""
  rm -f "$PENDING_FILE"
  ok "Anonymous agent registered: $(jq -r .agent_id "$CRED_FILE")"
else
  ok "Existing credentials kept — agent_id=$(jq -r .agent_id "$CRED_FILE")"
fi

# Mint or refresh the access token (5-min skew).
NOW=$(date +%s)
ISSUED_AT=$(jq '.access_token_issued_at // 0' "$CRED_FILE")
EXPIRES_IN=$(jq '.access_token_expires_in // 0' "$CRED_FILE")
EXP_AT=$(( ISSUED_AT + EXPIRES_IN - 300 ))
if [ "$NOW" -ge "$EXP_AT" ]; then
  CID=$(jq -r .client_id "$CRED_FILE")
  CSEC=$(jq -r .client_secret "$CRED_FILE")
  AUD=$(jq -r .audience "$CRED_FILE")
  TOK=$(curl -fsS --connect-timeout 5 --max-time 30 -X POST "$HI_BASE/oauth/token" \
    --data-urlencode 'grant_type=client_credentials' --data-urlencode "client_id=$CID" \
    --data-urlencode "client_secret=$CSEC" --data-urlencode "audience=$AUD") \
    || fail "token endpoint unreachable"
  printf '%s' "$TOK" | jq -e '
    (.access_token | type == "string" and length > 0) and
    (.expires_in | type == "number" and . > 0)' >/dev/null \
    || fail "token endpoint returned an invalid token response"
  STAGED_CRED=$(mktemp "$CRED_DIR/credentials.stage.XXXXXX")
  jq --argjson tok "$TOK" --arg now "$NOW" '
    .access_token            = $tok.access_token
    | .access_token_issued_at  = ($now | tonumber)
    | .access_token_expires_in = $tok.expires_in
  ' "$CRED_FILE" > "$STAGED_CRED"
  chmod 600 "$STAGED_CRED"
  mv "$STAGED_CRED" "$CRED_FILE"
  STAGED_CRED=""
  ok "Access token refreshed (expires in $(jq -r .access_token_expires_in "$CRED_FILE")s)"
else
  ok "Cached token still valid"
fi

# ─── Done ───────────────────────────────────────────────────────────────
AGENT_ID=$(jq -r .agent_id "$CRED_FILE")
# Read the actual installed plugin version from the cloned manifest.
# Previously this banner hard-coded VERSION="0.1.0" at the top of the
# script; that drifted from the published plugin (0.2.x at time of writing)
# because nobody bumped it. Reading from plugin.yaml means the banner always
# matches whatever `hermes plugins install` just put on disk.
PLUGIN_VERSION=$(awk '/^version:[[:space:]]/{print $2; exit}' "$PLUGIN_DIR/plugin.yaml" 2>/dev/null || true)
echo
ok "hirey-hi${PLUGIN_VERSION:+ v$PLUGIN_VERSION} is ready (agent_id=${GREEN}${AGENT_ID}${NC})"
echo
echo "  Plugin:       $PLUGIN_DIR"
echo "  Skills:       $SKILLS_DIR/hi-{onboard,use,events,repair}/"
echo "  Credentials:  $CRED_FILE (mode 600)"
echo
# ─── IMPORTANT: TUI / gateway must restart to pick up the new tools ─────
# Hermes plugin tool registry is built ONCE per process at startup. If you
# installed this from inside a running TUI session, that TUI process's
# registry is frozen — it cannot see hi_* tools until you exit + relaunch.
# `/reset` only clears session history + re-scans skills, NOT plugin tools.
# `hermes gateway restart` only restarts the daemon, not the TUI client.
# Tracker: https://github.com/NousResearch/hermes-agent/issues/15626
printf "${YELLOW}╭─ ONE MORE STEP ──────────────────────────────────────╮${NC}\n"
printf "${YELLOW}│${NC} If you installed from inside a running Hermes TUI:    ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC}                                                       ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC}   1. Exit the TUI:  ${GREEN}/quit${NC} (or Ctrl+D)                ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC}   2. Relaunch:      ${GREEN}hermes${NC}                          ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC}                                                       ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC} The new ${GREEN}hi_*${NC} tools only appear in a fresh TUI       ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC} process. ${DIM}/reset${NC} alone is ${RED}NOT${NC} sufficient.            ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC}                                                       ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC} (If you ran this from outside Hermes, just start one  ${YELLOW}│${NC}\n"
printf "${YELLOW}│${NC} now with ${GREEN}hermes${NC} — you're good.)                     ${YELLOW}│${NC}\n"
printf "${YELLOW}╰───────────────────────────────────────────────────────╯${NC}\n"
echo
echo "  In a fresh Hermes session, just ask:"
echo "    \"find me a founder in San Francisco\""
echo "    \"post a listing for a fintech cofounder in SF\""
echo "    \"any replies from yesterday's SF pairings?\""
echo
printf "  ${DIM}To uninstall (KEEPS your Hi identity so a reinstall reuses the SAME agent):${NC}\n"
printf "      hermes plugins remove hirey-hi && rm -rf $SKILLS_DIR/hi-{onboard,use,events,repair}\n"
printf "  ${DIM}To ALSO erase your Hi identity (next install registers a brand-new agent): rm -rf $CRED_DIR${NC}\n"

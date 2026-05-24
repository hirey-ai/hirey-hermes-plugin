#!/usr/bin/env bash
# Hirey Hi installer for Hermes Agent.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/hirey-ai/hirey-hermes-plugin/master/install.sh | bash
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
#
# Idempotent: re-running is safe.

set -euo pipefail

VERSION="0.1.0"
HI_BASE="${HI_BASE:-https://hi.hirey.ai}"
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

step "Installing hirey-hi v${VERSION} for Hermes Agent"

# ─── 1. Hermes plugin install ────────────────────────────────────────────
if HERMES_HOME="$HERMES_HOME" hermes plugins list 2>/dev/null | grep -q "hirey-hi"; then
  ok "Plugin already installed — updating"
  HERMES_HOME="$HERMES_HOME" hermes plugins update hirey-hi || warn "plugin update returned non-zero (continuing)"
else
  HERMES_HOME="$HERMES_HOME" hermes plugins install "$PLUGIN_REPO" --enable \
    || fail "hermes plugins install $PLUGIN_REPO failed"
  ok "Plugin installed + enabled"
fi

PLUGIN_DIR="$HERMES_HOME/plugins/hirey-hi"
[ -d "$PLUGIN_DIR" ] || fail "Plugin directory missing: $PLUGIN_DIR"

# ─── 2. Drop SKILL.md files into the user's skill tree ───────────────────
step "Installing SKILL.md files into $SKILLS_DIR"
mkdir -p "$SKILLS_DIR"
for name in hi-onboard hi-use hi-events; do
  if [ -f "$PLUGIN_DIR/skills/$name/SKILL.md" ]; then
    mkdir -p "$SKILLS_DIR/$name"
    cp "$PLUGIN_DIR/skills/$name/SKILL.md" "$SKILLS_DIR/$name/SKILL.md"
  fi
done
ok "Skills installed (hi-onboard, hi-use, hi-events)"

# ─── 3. Anonymous Hi identity ────────────────────────────────────────────
step "Bootstrapping anonymous Hi identity at $CRED_FILE"
mkdir -p "$CRED_DIR" && chmod 700 "$CRED_DIR"

if [ ! -f "$CRED_FILE" ] || [ -z "$(jq -er '.client_id // empty' "$CRED_FILE" 2>/dev/null)" ]; then
  REG=$(curl -fsS -X POST "$HI_BASE/v1/agents/register" \
    -H 'content-type: application/json' \
    --data '{"display_name":"Hermes Agent (hirey-hi installer)","agent_kind":"external"}') \
    || fail "register failed at $HI_BASE/v1/agents/register"
  printf '%s' "$REG" | jq --arg base "$HI_BASE" '{
    client_id:          .auth.client_id,
    client_secret:      .auth.client_secret,
    agent_id:           .agent.agent_id,
    installation_id:    .installation.installation_id,
    issuer:             .auth.issuer,
    audience:           .auth.audience,
    token_url:          .auth.token_url,
    platform_base_url:  $base,
    access_token:           null,
    access_token_issued_at: 0,
    access_token_expires_in: 0
  }' > "$CRED_FILE"
  chmod 600 "$CRED_FILE"
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
  TOK=$(curl -fsS -X POST "$HI_BASE/oauth/token" \
    --data "grant_type=client_credentials&client_id=$CID&client_secret=$CSEC&audience=$AUD") \
    || fail "token endpoint unreachable"
  [ -n "$(printf '%s' "$TOK" | jq -r '.access_token // empty')" ] \
    || fail "token endpoint returned no access_token: $TOK"
  jq --argjson tok "$TOK" --arg now "$NOW" '
    .access_token            = $tok.access_token
    | .access_token_issued_at  = ($now | tonumber)
    | .access_token_expires_in = $tok.expires_in
  ' "$CRED_FILE" > "$CRED_FILE.tmp" && mv "$CRED_FILE.tmp" "$CRED_FILE"
  ok "Access token refreshed (expires in $(jq -r .access_token_expires_in "$CRED_FILE")s)"
else
  ok "Cached token still valid"
fi

TOKEN=$(jq -r .access_token "$CRED_FILE")
curl -fsS -X POST "$HI_BASE/v1/agents/activate" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' --data '{}' >/dev/null \
  || warn "activation returned non-zero (likely already-active — non-fatal)"

# ─── Done ───────────────────────────────────────────────────────────────
AGENT_ID=$(jq -r .agent_id "$CRED_FILE")
echo
ok "hirey-hi is ready (agent_id=${GREEN}${AGENT_ID}${NC})"
echo
echo "  Plugin:       $PLUGIN_DIR"
echo "  Skills:       $SKILLS_DIR/hi-{onboard,use,events}/"
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
echo "    \"find me 5 backend engineers in Tokyo\""
echo "    \"post a listing for a fintech cofounder\""
echo "    \"any replies on yesterday's pairings?\""
echo
printf "  ${DIM}To uninstall: hermes plugins remove hirey-hi && rm -rf $SKILLS_DIR/hi-{onboard,use,events} $CRED_DIR${NC}\n"

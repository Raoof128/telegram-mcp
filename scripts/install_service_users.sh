#!/bin/bash
# One-time privileged install of the two service accounts and the admin group.
#
# Accounts (design §1): `telegram-mcpd` runs the runtime, `telegram-mcp-tunnel`
# runs the tunnel client. Both are non-login accounts with shell
# /usr/bin/false and home /var/empty, so a compromised project shell cannot
# become either of them (spec §11 service-account boundary). The group
# `telegram-mcp-admin` owns the socket directory; the operator is its member,
# which grants *reachability* to the admin socket and nothing else.
#
# This script is idempotent: every object is checked before it is created, and
# reruns report "present" instead of failing. `uninstall` removes what this
# script created; `repair` re-asserts shell/home/group membership on existing
# accounts. Nothing is created without an interactive admin authentication,
# and --dry-run prints the exact plan without touching the host.
#
# macOS only. UID/GID allocation is collision-safe: the first free id at or
# above the base is used and printed for review.
set -euo pipefail

RUNTIME_USER="telegram-mcpd"
TUNNEL_USER="telegram-mcp-tunnel"
ADMIN_GROUP="telegram-mcp-admin"
ID_BASE=${TELEGRAM_MCP_ID_BASE:-401}
NOLOGIN_SHELL="/usr/bin/false"
SERVICE_HOME="/var/empty"
DSCL="/usr/bin/dscl"
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: install_service_users.sh <install|uninstall|repair|check> [--dry-run]

  install    create both service accounts and the admin group (idempotent)
  uninstall  delete what this script created
  repair     re-assert shell, home and group membership on existing accounts
  check      report current state only; never mutates
USAGE
}

log() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: $*"
  else
    "$@"
  fi
}

require_macos() {
  if [ "$(uname -s)" != "Darwin" ]; then
    log "error: this installer is macOS-only"
    exit 2
  fi
}

user_exists() { $DSCL . -read "/Users/$1" >/dev/null 2>&1; }
group_exists() { $DSCL . -read "/Groups/$1" >/dev/null 2>&1; }

# Ids handed out during this run, so two creations in one run cannot collide
# (and so --dry-run shows the same ids a real run would use).
ALLOCATED_IDS=""
NEXT_ID=""

# Sets NEXT_ID rather than printing it: a command substitution would run in a
# subshell and lose the ALLOCATED_IDS bookkeeping.
alloc_id() {
  # $1: "Users" or "Groups"; $2: the id attribute to scan
  local domain="$1" attribute="$2" candidate="$ID_BASE"
  local taken
  taken=$($DSCL . -list "/$domain" "$attribute" 2>/dev/null | awk '{print $2}' | sort -n)
  taken="$taken
$(printf '%s\n' $ALLOCATED_IDS)"
  while printf '%s\n' "$taken" | grep -qx "$candidate"; do
    candidate=$((candidate + 1))
  done
  ALLOCATED_IDS="$ALLOCATED_IDS $candidate"
  NEXT_ID="$candidate"
}

create_group() {
  local name="$1"
  if group_exists "$name"; then
    log "group $name: present"
    return
  fi
  local gid
  alloc_id Groups PrimaryGroupID
  gid="$NEXT_ID"
  log "group $name: creating with gid $gid"
  run "$DSCL" . -create "/Groups/$name"
  run "$DSCL" . -create "/Groups/$name" PrimaryGroupID "$gid"
  run "$DSCL" . -create "/Groups/$name" RealName "Telegram MCP $name"
}

create_user() {
  local name="$1"
  if user_exists "$name"; then
    log "user $name: present"
    return
  fi
  local uid gid
  alloc_id Users UniqueID
  uid="$NEXT_ID"
  gid=$({ $DSCL . -read "/Groups/$name" PrimaryGroupID 2>/dev/null || true; } | awk '{print $2}')
  if [ -z "$gid" ]; then
    gid="$uid"
  fi
  log "user $name: creating with uid $uid, gid $gid, shell $NOLOGIN_SHELL"
  run "$DSCL" . -create "/Users/$name"
  run "$DSCL" . -create "/Users/$name" UserShell "$NOLOGIN_SHELL"
  run "$DSCL" . -create "/Users/$name" RealName "Telegram MCP $name"
  run "$DSCL" . -create "/Users/$name" UniqueID "$uid"
  run "$DSCL" . -create "/Users/$name" PrimaryGroupID "$gid"
  run "$DSCL" . -create "/Users/$name" NFSHomeDirectory "$SERVICE_HOME"
  run "$DSCL" . -create "/Users/$name" IsHidden 1
  # No password is set: the account cannot be authenticated into at all.
  run "$DSCL" . -delete "/Users/$name" AuthenticationAuthority || true
}

add_operator_to_admin_group() {
  local operator="${SUDO_USER:-$(id -un)}"
  if $DSCL . -read "/Groups/$ADMIN_GROUP" GroupMembership 2>/dev/null | grep -qw "$operator"; then
    log "group $ADMIN_GROUP: operator $operator already a member"
    return
  fi
  log "group $ADMIN_GROUP: adding operator $operator"
  run "$DSCL" . -append "/Groups/$ADMIN_GROUP" GroupMembership "$operator"
}

do_check() {
  for name in "$RUNTIME_USER" "$TUNNEL_USER"; do
    if user_exists "$name"; then
      log "user $name: present shell=$({ $DSCL . -read "/Users/$name" UserShell || true; } | awk '{print $2}')"
    else
      log "user $name: absent"
    fi
  done
  for name in "$RUNTIME_USER" "$TUNNEL_USER" "$ADMIN_GROUP"; do
    if group_exists "$name"; then
      log "group $name: present"
    else
      log "group $name: absent"
    fi
  done
}

do_install() {
  create_group "$RUNTIME_USER"
  create_group "$TUNNEL_USER"
  create_group "$ADMIN_GROUP"
  create_user "$RUNTIME_USER"
  create_user "$TUNNEL_USER"
  add_operator_to_admin_group
  log "install complete; run doctor --production to verify separation"
}

do_repair() {
  for name in "$RUNTIME_USER" "$TUNNEL_USER"; do
    if ! user_exists "$name"; then
      log "user $name: absent (run install)"
      continue
    fi
    log "user $name: re-asserting shell and home"
    run "$DSCL" . -create "/Users/$name" UserShell "$NOLOGIN_SHELL"
    run "$DSCL" . -create "/Users/$name" NFSHomeDirectory "$SERVICE_HOME"
  done
  if group_exists "$ADMIN_GROUP"; then
    add_operator_to_admin_group
  fi
}

do_uninstall() {
  for name in "$RUNTIME_USER" "$TUNNEL_USER"; do
    if user_exists "$name"; then
      log "user $name: deleting"
      run "$DSCL" . -delete "/Users/$name"
    fi
  done
  for name in "$RUNTIME_USER" "$TUNNEL_USER" "$ADMIN_GROUP"; do
    if group_exists "$name"; then
      log "group $name: deleting"
      run "$DSCL" . -delete "/Groups/$name"
    fi
  done
  log "uninstall complete"
}

main() {
  local verb="${1:-}"
  shift || true
  for argument in "$@"; do
    case "$argument" in
      --dry-run) DRY_RUN=1 ;;
      *) usage; exit 2 ;;
    esac
  done
  case "$verb" in
    install) require_macos; do_install ;;
    uninstall) require_macos; do_uninstall ;;
    repair) require_macos; do_repair ;;
    check) require_macos; do_check ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"

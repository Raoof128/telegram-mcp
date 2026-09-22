#!/bin/bash
# One-time privileged install of the runtime paths and the tunnel certificates.
#
# Layout (design §2, spec §12.4):
#
#   /private/var/run/telegram-mcp/   telegram-mcpd:telegram-mcp-admin  0770
#     consent.sock, admin.sock       created by the runtime              0660
#   <state>/db/                      telegram-mcpd                      0700
#   <state>/keys/                    telegram-mcpd                      0700
#
# Certificates: the tunnel ingress (8767) needs a server certificate with
# SAN 127.0.0.1,::1 and EKU serverAuth, and the tunnel client needs a
# certificate with EKU clientAuth. Both are 90-day, 0600, owned by the
# service account that uses them, and both SPKI digests are printed so the
# operator can record the pin (`telegram-mcp tunnel rotate-binding`).
#
# Private keys are generated here and never leave the host. Nothing is
# printed except public material: subjects, validity and SPKI digests.
#
# Idempotent: existing objects are verified, not recreated. --dry-run prints
# the plan. macOS only.
set -euo pipefail

RUNTIME_USER="telegram-mcpd"
TUNNEL_USER="telegram-mcp-tunnel"
ADMIN_GROUP="telegram-mcp-admin"
SOCKET_DIR="${TELEGRAM_MCP_RUNTIME_DIR:-/private/var/run/telegram-mcp}"
STATE_DIR="${TELEGRAM_MCP_STATE_DIR:-/var/db/telegram-mcp}"
CERT_DAYS=90
OPENSSL="/usr/bin/openssl"
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: install_paths.sh <install|uninstall|repair|check> [--dry-run]

  install    create the socket/state directories and both tunnel certificates
  uninstall  remove the socket directory and certificates (state DB is kept)
  repair     re-assert ownership and permissions on existing paths
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

ensure_dir() {
  local path="$1" mode="$2" owner="$3"
  if [ -L "$path" ]; then
    log "error: $path is a symlink"
    exit 1
  fi
  if [ ! -d "$path" ]; then
    log "dir $path: creating mode $mode owner $owner"
    run /bin/mkdir -p "$path"
  else
    log "dir $path: present"
  fi
  run /bin/chmod "$mode" "$path"
  run /usr/sbin/chown "$owner" "$path"
}

print_spki() {
  local cert="$1" label="$2"
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: would print $label SPKI digest for $cert"
    return
  fi
  local digest
  digest=$($OPENSSL x509 -in "$cert" -noout -pubkey \
    | $OPENSSL pkey -pubin -outform DER \
    | $OPENSSL dgst -sha256 -hex \
    | awk '{print $NF}')
  log "$label SPKI pin: spki:sha256:$digest"
  log "$label validity: $($OPENSSL x509 -in "$cert" -noout -enddate)"
}

make_cert() {
  # $1 label, $2 key path, $3 cert path, $4 CN, $5 extension block, $6 owner
  local label="$1" key="$2" cert="$3" cn="$4" extensions="$5" owner="$6"
  if [ -s "$cert" ] && [ -s "$key" ]; then
    log "cert $label: present"
    print_spki "$cert" "$label"
    return
  fi
  local config
  config=$(/usr/bin/mktemp -t telegram-mcp-cert)
  cat >"$config" <<CONF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = $cn
[ext]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
$extensions
CONF
  # -nodes, not -noenc: /usr/bin/openssl on macOS is LibreSSL, which rejects
  # the OpenSSL 3 spelling. Both mean "do not encrypt the private key".
  log "cert $label: generating P-256 key + ${CERT_DAYS}-day certificate (CN=$cn)"
  run "$OPENSSL" req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -nodes -days "$CERT_DAYS" -config "$config" -keyout "$key" -out "$cert"
  run /bin/chmod 600 "$key" "$cert"
  run /usr/sbin/chown "$owner" "$key" "$cert"
  /bin/rm -f "$config"
  print_spki "$cert" "$label"
}

do_install() {
  ensure_dir "$SOCKET_DIR" 0770 "$RUNTIME_USER:$ADMIN_GROUP"
  ensure_dir "$STATE_DIR" 0700 "$RUNTIME_USER:$RUNTIME_USER"
  ensure_dir "$STATE_DIR/db" 0700 "$RUNTIME_USER:$RUNTIME_USER"
  ensure_dir "$STATE_DIR/keys" 0700 "$RUNTIME_USER:$RUNTIME_USER"
  ensure_dir "$STATE_DIR/tls" 0700 "$RUNTIME_USER:$RUNTIME_USER"
  make_cert "tunnel-server" "$STATE_DIR/tls/tunnel-server.key" \
    "$STATE_DIR/tls/tunnel-server.crt" "telegram-mcp tunnel ingress" \
    "extendedKeyUsage = critical,serverAuth
subjectAltName = IP:127.0.0.1,IP:::1" \
    "$RUNTIME_USER:$RUNTIME_USER"
  make_cert "tunnel-client" "$STATE_DIR/tls/tunnel-client.key" \
    "$STATE_DIR/tls/tunnel-client.crt" "telegram-mcp tunnel client" \
    "extendedKeyUsage = critical,clientAuth" \
    "$TUNNEL_USER:$TUNNEL_USER"
  log "install complete; record the tunnel-client SPKI pin with"
  log "  telegram-mcp tunnel rotate-binding <spki:sha256:...>"
}

do_repair() {
  for path in "$SOCKET_DIR" "$STATE_DIR" "$STATE_DIR/db" "$STATE_DIR/keys" "$STATE_DIR/tls"; do
    [ -d "$path" ] || { log "dir $path: absent (run install)"; continue; }
    log "dir $path: re-asserting permissions"
    if [ "$path" = "$SOCKET_DIR" ]; then
      run /bin/chmod 0770 "$path"
      run /usr/sbin/chown "$RUNTIME_USER:$ADMIN_GROUP" "$path"
    else
      run /bin/chmod 0700 "$path"
      run /usr/sbin/chown "$RUNTIME_USER:$RUNTIME_USER" "$path"
    fi
  done
}

do_check() {
  for path in "$SOCKET_DIR" "$STATE_DIR" "$STATE_DIR/db" "$STATE_DIR/keys" "$STATE_DIR/tls"; do
    if [ -d "$path" ]; then
      log "dir $path: $(/usr/bin/stat -f '%Sp %Su:%Sg' "$path")"
    else
      log "dir $path: absent"
    fi
  done
  for cert in "$STATE_DIR/tls/tunnel-server.crt" "$STATE_DIR/tls/tunnel-client.crt"; do
    if [ -s "$cert" ]; then
      print_spki "$cert" "$(/usr/bin/basename "$cert")"
    else
      log "cert $cert: absent"
    fi
  done
}

do_uninstall() {
  if [ -d "$SOCKET_DIR" ]; then
    log "dir $SOCKET_DIR: removing"
    run /bin/rm -rf "$SOCKET_DIR"
  fi
  if [ -d "$STATE_DIR/tls" ]; then
    log "dir $STATE_DIR/tls: removing certificates"
    run /bin/rm -rf "$STATE_DIR/tls"
  fi
  log "uninstall complete; $STATE_DIR/db was kept (delete it deliberately)"
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

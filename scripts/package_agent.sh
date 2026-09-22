#!/bin/bash
# Assemble and sign the consent agent bundle (Plan 2b Task 4 Step 3).
#
# There is no Xcode project: the bundle is laid out by hand so every byte in
# it is reviewable. Layout:
#
#   build/consent/TelegramMCPConsent.app/Contents/
#     MacOS/telegram-mcp-consent     the swiftc-built binary
#     Info.plist                     bundle identity (not the LaunchAgent plist)
#     embedded.provisionprofile      only when one is supplied
#     _CodeSignature/                written by codesign
#
# Signing identity comes from TELEGRAM_MCP_SIGN_IDENTITY. Pairing requires a
# Developer ID Application certificate; anything else still produces a
# runnable bundle for the headless tests, and `pairing generate` refuses it.
# The script prints which identity it used so evidence cannot overstate it.
set -euo pipefail

SOURCE="agent/consent-agent.swift"
BUILD_DIR="${TELEGRAM_MCP_BUNDLE_DIR:-build/consent}"
BUNDLE="$BUILD_DIR/TelegramMCPConsent.app"
BUNDLE_ID="com.telegram-mcp.consent"
VERSION="0.1.10"
IDENTITY="${TELEGRAM_MCP_SIGN_IDENTITY:--}"
PROFILE="${TELEGRAM_MCP_PROVISION_PROFILE:-}"
ENTITLEMENTS="agent/ConsentAgent.entitlements"

log() { printf '%s\n' "$*"; }

[ -f "$SOURCE" ] || { log "error: $SOURCE is absent"; exit 2; }

mkdir -p "$BUNDLE/Contents/MacOS"
swiftc -O "$SOURCE" -o "$BUNDLE/Contents/MacOS/telegram-mcp-consent"

cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>
    <string>Telegram MCP Consent</string>
    <key>CFBundleExecutable</key>
    <string>telegram-mcp-consent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>LSUIElement</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
</dict>
</plist>
PLIST
plutil -lint "$BUNDLE/Contents/Info.plist" >/dev/null

if [ -n "$PROFILE" ]; then
  [ -f "$PROFILE" ] || { log "error: provisioning profile $PROFILE is absent"; exit 2; }
  cp "$PROFILE" "$BUNDLE/Contents/embedded.provisionprofile"
  log "profile: embedded $PROFILE"
else
  rm -f "$BUNDLE/Contents/embedded.provisionprofile"
  log "profile: none (restricted entitlements such as keychain-access-groups are unavailable)"
fi

rm -rf "$BUNDLE/Contents/_CodeSignature"
SIGN_ARGS=(--force --options runtime --timestamp=none --sign "$IDENTITY")
if [ -n "$PROFILE" ] && [ -f "$ENTITLEMENTS" ]; then
  SIGN_ARGS+=(--entitlements "$ENTITLEMENTS")
  log "entitlements: $ENTITLEMENTS"
fi
codesign "${SIGN_ARGS[@]}" "$BUNDLE"

if [ "$IDENTITY" = "-" ]; then
  log "signature: ad-hoc — pairing will refuse this bundle by design"
else
  log "signature: $IDENTITY"
fi
codesign --verify --strict "$BUNDLE"
codesign -dv "$BUNDLE" 2>&1 | sed 's/^/  /'
log "bundle: $BUNDLE"

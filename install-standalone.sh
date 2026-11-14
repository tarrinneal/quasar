#!/usr/bin/env bash
# Zero-install 1-line bootstrapper for Quasar Standalone (Flutter Web App + `quasar` CLI + Antigravity `agy` plugin)
#
# Usage:
#   curl -fsSL https://github.com/tarrinneal/quasar/releases/latest/download/install-standalone.sh | bash
#   QUASAR_VERSION=1.5.0 curl -fsSL https://raw.githubusercontent.com/tarrinneal/quasar/main/install-standalone.sh | bash
set -euo pipefail

REPO="${QUASAR_GITHUB_REPO:-tarrinneal/quasar}"
REQ_VERSION="${QUASAR_VERSION:-}"

resolve_version() {
  if [ -n "${REQ_VERSION}" ]; then
    echo "${REQ_VERSION#v}"
    return 0
  fi

  # 1. Follow GitHub /releases/latest redirect (works without API rate limits or auth)
  local effective_url=""
  effective_url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/${REPO}/releases/latest" 2>/dev/null || true)"
  if [[ "${effective_url}" =~ /releases/tag/v?([^/?#]+) ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  # 2. Fallback to GitHub Releases API
  python3 - "${REPO}" <<'PY'
import json, sys, urllib.request
repo = sys.argv[1]
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases/latest",
    headers={"Accept": "application/vnd.github+json", "User-Agent": "Quasar-Standalone-Installer"},
)
with urllib.request.urlopen(req, timeout=15) as resp:
    tag = (json.loads(resp.read().decode("utf-8")).get("tag_name") or "").lstrip("v")
if not tag:
    sys.exit(1)
print(tag)
PY
}

VERSION="$(resolve_version)"
if [ -z "${VERSION}" ]; then
  echo "❌ Could not resolve latest Quasar release version from https://github.com/${REPO}/releases" >&2
  exit 1
fi

TARBALL_NAME="quasar-standalone-${VERSION}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/v${VERSION}/${TARBALL_NAME}"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

echo "⬇️  Downloading Quasar Standalone v${VERSION} (${DOWNLOAD_URL})..."
curl -fL --retry 2 -o "${TMP_DIR}/${TARBALL_NAME}" "${DOWNLOAD_URL}"

echo "📦 Extracting ${TARBALL_NAME}..."
tar -xzf "${TMP_DIR}/${TARBALL_NAME}" -C "${TMP_DIR}"

EXTRACTED_DIR="${TMP_DIR}/quasar-standalone-${VERSION}"
if [ ! -d "${EXTRACTED_DIR}" ]; then
  EXTRACTED_DIR="$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

chmod +x "${EXTRACTED_DIR}/install.sh" "${EXTRACTED_DIR}/bin/quasar" 2>/dev/null || true
"${EXTRACTED_DIR}/install.sh" --standalone-only

echo ""
if ! command -v quasar >/dev/null 2>&1; then
  echo "💡 Tip: Add ~/.gemini/bin to your PATH so you can run 'quasar' from anywhere:"
  echo "   export PATH=\"\$HOME/.gemini/bin:\$PATH\""
fi
echo "🚀 Run '${HOME}/.gemini/bin/quasar' (or 'quasar') to open the Quasar Web App!"

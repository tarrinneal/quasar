#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.2.3"
EXT_NAME="codeium.codeium-dev-${VERSION}"
OLD_CODEIUM_EXT_NAME="codeium.codeium-dev-1.0.0"
OLD_EXT_NAME="antigravity.antigravity-code-review-1.0.0"
OLD_QUASAR_EXT_NAME="quasar.quasar-1.0.0"
PLUGIN_DEST="${HOME}/.gemini/config/plugins/quasar"
OLD_PLUGIN_DEST="${HOME}/.gemini/config/plugins/code-review"
STATE_DIR="${HOME}/.gemini/quasar"

CHECK_ONLY=0
if [ "${1:-}" = "--check" ] || [ "${1:-}" = "--verify" ]; then
  CHECK_ONLY=1
fi

run_doctor() {
  echo "=================================================================="
  echo "🔍 Quasar v${VERSION} — Installation & Prerequisite Doctor"
  echo "=================================================================="
  local ok=1

  if command -v git >/dev/null 2>&1; then
    echo "  ✅ git: $(git --version)"
  else
    echo "  ❌ git: NOT FOUND (Required for local branch & PR diffs)"
    ok=0
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "  ✅ python3: $(python3 --version 2>&1)"
  else
    echo "  ❌ python3: NOT FOUND (Required for Quasar scripts)"
    ok=0
  fi

  if [ -f "${STATE_DIR}/github_auth.json" ]; then
    echo "  ✅ GitHub API Key: Configured in ${STATE_DIR}/github_auth.json (Private PRs + Publish Review as You enabled)"
  elif command -v gh >/dev/null 2>&1; then
    if gh auth status >/dev/null 2>&1; then
      echo "  ✅ gh (GitHub CLI): Installed & authenticated (Private PRs + Publish Review enabled)"
    else
      echo "  ℹ️  GitHub Auth: Not configured yet (Public PRs & Local Branches work now; click '🔑 Configure GitHub API Key' in Quasar to publish reviews as you)"
    fi
  elif [ -n "${GITHUB_TOKEN:-}" ] || [ -n "${GH_TOKEN:-}" ]; then
    echo "  ✅ GitHub Token: Found in environment (Private PRs + Publish Review enabled)"
  else
    echo "  ℹ️  GitHub Auth: Optional (Public PRs & Local Branches work out-of-the-box; click '🔑 Configure GitHub API Key' in Quasar to publish reviews as you)"
  fi

  if [ -f "${PLUGIN_DEST}/plugin.json" ] && [ -f "${PLUGIN_DEST}/skills/quasar/SKILL.md" ]; then
    echo "  ✅ Quasar Agent Plugin (v${VERSION}): Installed at ${PLUGIN_DEST}"
  else
    echo "  ❌ Quasar Agent Plugin: Missing at ${PLUGIN_DEST}"
    ok=0
  fi

  if [ -f "${PLUGIN_DEST}/sidecars/quasar-web/sidecar.json" ] && [ -f "${PLUGIN_DEST}/sidecars/quasar-web/server.py" ]; then
    echo "  ✅ Quasar Web UI Sidecar: Installed at ${PLUGIN_DEST}/sidecars/quasar-web (sidecar://quasar/quasar-web/ & http://127.0.0.1:49341)"
  else
    echo "  ⚠️  Quasar Web UI Sidecar: Missing at ${PLUGIN_DEST}/sidecars/quasar-web"
  fi

  local ext_found=0
  for EXT_ROOT in "${HOME}"/.*/extensions; do
    if [ -f "${EXT_ROOT}/${EXT_NAME}/extension.js" ]; then
      echo "  ✅ Quasar Native Extension (v${VERSION}): Installed at ${EXT_ROOT}/${EXT_NAME}"
      ext_found=1
    fi
  done
  if [ "${ext_found}" -eq 0 ]; then
    echo "  ℹ️  Quasar Native Extension: Not found in local IDE extensions (Jetski Web UI & Standalone HTML still active)"
  fi

  echo "=================================================================="
  if [ "${ok}" -eq 1 ]; then
    echo "🎉 Quasar v${VERSION} is installed and ready!"
    echo "👉 Next step in Antigravity / VS Code / Jetski Web:"
    echo "   • Desktop IDE: Press Cmd+Shift+P -> 'Developer: Reload Window', then click 'Quasar' in the Status Bar"
    echo "   • Jetski Web / Browser: Open sidecar://quasar/quasar-web/ or http://127.0.0.1:49341"
    return 0
  else
    echo "❌ Quasar installation is incomplete. Run ./install.sh to fix."
    return 1
  fi
}

if [ "${CHECK_ONLY}" -eq 1 ]; then
  run_doctor
  exit $?
fi

echo "==> Installing Quasar v${VERSION} plugin to ${PLUGIN_DEST}..."
rm -rf "${OLD_PLUGIN_DEST}" 2>/dev/null || true
mkdir -p "${PLUGIN_DEST}/skills/quasar/scripts"
mkdir -p "${PLUGIN_DEST}/agents"
mkdir -p "${PLUGIN_DEST}/sidecars/quasar-web"
mkdir -p "${STATE_DIR}/seed_skills"
mkdir -p "${STATE_DIR}/workspaces"

cp "${SCRIPT_DIR}/plugin/plugin.json" "${PLUGIN_DEST}/plugin.json"
cp "${SCRIPT_DIR}/plugin/skills/quasar/SKILL.md" "${PLUGIN_DEST}/skills/quasar/SKILL.md"
cp "${SCRIPT_DIR}/plugin/skills/quasar/scripts/"*.py "${PLUGIN_DEST}/skills/quasar/scripts/"
chmod +x "${PLUGIN_DEST}/skills/quasar/scripts/"*.py

if [ -d "${SCRIPT_DIR}/plugin/skills/quasar/references" ]; then
  mkdir -p "${PLUGIN_DEST}/skills/quasar/references"
  cp -R "${SCRIPT_DIR}/plugin/skills/quasar/references/"* "${PLUGIN_DEST}/skills/quasar/references/" 2>/dev/null || true
fi

if [ -d "${SCRIPT_DIR}/plugin/agents" ]; then
  cp -R "${SCRIPT_DIR}/plugin/agents/"* "${PLUGIN_DEST}/agents/" 2>/dev/null || true
fi

if [ -d "${SCRIPT_DIR}/plugin/sidecars/quasar-web" ]; then
  cp "${SCRIPT_DIR}/extension/media/mermaid-preview.js" "${SCRIPT_DIR}/plugin/sidecars/quasar-web/mermaid-preview.js" 2>/dev/null || true
  cp -R "${SCRIPT_DIR}/plugin/sidecars/quasar-web/"* "${PLUGIN_DEST}/sidecars/quasar-web/" 2>/dev/null || true
  chmod +x "${PLUGIN_DEST}/sidecars/quasar-web/server.py" 2>/dev/null || true
  # Auto-enable the Quasar plugin and start its sidecar in any running Jetski / Antigravity Language Server
  SIDECAR_MANAGED=0
  if python3 - "${PLUGIN_DEST}/sidecars/quasar-web" <<'PY' 2>/dev/null; then
import json
import sys
import urllib.request

sys.path.insert(0, sys.argv[1])
import server

env = server.discover_ls_env()
addr = env.get("ANTIGRAVITY_LS_ADDRESS")
csrf = env.get("ANTIGRAVITY_CSRF_TOKEN")
if not (addr and csrf):
    sys.exit(1)

def rpc(method: str, body: dict):
    req = urllib.request.Request(
        f"http://{addr}/exa.language_server_pb.LanguageServerService/{method}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read().decode("utf-8"))

rpc("JetboxWriteState", {"userConfig": {"plugins": {"quasar": {"enabled": True}}}})
rpc("GetAllPlugins", {})
PY
    SIDECAR_MANAGED=1
  fi
  # Ensure standalone localhost:49341 Web UI server is running immediately if not started by SidecarManager
  if [ "${SIDECAR_MANAGED}" -eq 0 ]; then
    pkill -f "quasar-web/server.py --standalone-daemon" 2>/dev/null || true
    if ! lsof -iTCP:49341 -sTCP:LISTEN >/dev/null 2>&1; then
      nohup python3 "${PLUGIN_DEST}/sidecars/quasar-web/server.py" --standalone-daemon >/dev/null 2>&1 &
    fi
  fi
fi

# Migrate any existing review session state to ~/.gemini/quasar
if [ -d "${HOME}/.gemini/antigravity-code-review" ] && [ ! -d "${STATE_DIR}/snapshots" ]; then
  cp -R "${HOME}/.gemini/antigravity-code-review/"* "${STATE_DIR}/" 2>/dev/null || true
fi

# Migrate and remove any legacy global ~/.gemini/quasar/active_session.json so it never leaks across windows
if [ -f "${STATE_DIR}/active_session.json" ]; then
  python3 - "${STATE_DIR}" "${PLUGIN_DEST}/skills/quasar/scripts" <<'PY' || true
import json
import os
import shutil
import sys

state_dir, scripts_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)
from collect_pr_context import get_session_paths

legacy_sess = os.path.join(state_dir, "active_session.json")
legacy_md = os.path.join(state_dir, "cliffs_notes.md")
try:
    with open(legacy_sess, "r", encoding="utf-8") as f:
        sess = json.load(f)
    repo_dir = sess.get("workspaceDir") or sess.get("repoDir")
    if repo_dir and os.path.isdir(repo_dir):
        ws_state, ws_sess, ws_md = get_session_paths(repo_dir)
        os.makedirs(ws_state, exist_ok=True)
        sess["workspaceDir"] = repo_dir
        sess["stateDir"] = ws_state
        with open(ws_sess, "w", encoding="utf-8") as wf:
            json.dump(sess, wf, indent=2)
        if os.path.isfile(legacy_md):
            shutil.copy2(legacy_md, ws_md)
except Exception:
    pass
for p in [legacy_sess, legacy_md, os.path.join(state_dir, "pr_context.json"), os.path.join(state_dir, "pr.diff")]:
    if os.path.isfile(p):
        try:
            os.unlink(p)
        except Exception:
            pass
PY
fi

# Initialize default preferences.json & PREFERENCES.md if not present
python3 "${PLUGIN_DEST}/skills/quasar/scripts/manage_preferences.py" --show >/dev/null 2>&1 || true

# Package VSIX for official CLI installation (includes README.md, CHANGELOG.md, and bundled_plugin/ so VSIX-only installs work standalone)
VSIX_PATH="/tmp/quasar-${VERSION}.vsix"
python3 - "${SCRIPT_DIR}/extension" "${SCRIPT_DIR}/plugin" "${SCRIPT_DIR}" "${VSIX_PATH}" "${VERSION}" <<'PY'
import os
import sys
import zipfile

ext_dir, plugin_dir, repo_root, vsix_path, version = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
content_types = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".py" ContentType="text/x-python"/>
  <Default Extension=".md" ContentType="text/markdown"/>
  <Default Extension=".svg" ContentType="image/svg+xml"/>
  <Default Extension=".png" ContentType="image/png"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>"""

vsixmanifest = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="codeium-dev" Version="{version}" Publisher="codeium"/>
    <DisplayName>Quasar</DisplayName>
    <Description xml:space="preserve">Comprehension-first, native GitHub PR &amp; local branch review tool.</Description>
    <Categories>Other,SCM Providers</Categories>
    <Icon>extension/media/quasar-icon.png</Icon>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Changelog" Path="extension/CHANGELOG.md" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/media/quasar-icon.png" Addressable="true"/>
  </Assets>
</PackageManifest>"""

with zipfile.ZipFile(vsix_path, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("[Content_Types].xml", content_types)
    zf.writestr("extension.vsixmanifest", vsixmanifest)
    for root, _, files in os.walk(ext_dir):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, ext_dir)
            zf.write(full_path, os.path.join("extension", rel_path))
    for doc_name in ("README.md", "CHANGELOG.md"):
        doc_path = os.path.join(repo_root, doc_name)
        if os.path.isfile(doc_path):
            zf.write(doc_path, os.path.join("extension", doc_name))
    if os.path.isdir(plugin_dir):
        import subprocess
        tracked_res = subprocess.run(
            ["git", "ls-files", "plugin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        tracked_files = [
            line.strip()
            for line in (tracked_res.stdout or "").splitlines()
            if line.strip()
        ]
        if tracked_files:
            for rel_repo_path in tracked_files:
                full_path = os.path.join(repo_root, rel_repo_path)
                if os.path.isfile(full_path):
                    rel_plugin_path = os.path.relpath(full_path, plugin_dir)
                    zf.write(full_path, os.path.join("extension", "bundled_plugin", rel_plugin_path))
        else:
            for root, _, files in os.walk(plugin_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, plugin_dir)
                    zf.write(full_path, os.path.join("extension", "bundled_plugin", rel_path))
PY

# Try installing via any discovered Antigravity / VS Code CLI binary
for CLI_BIN in \
  "/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity" \
  "${HOME}/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity"; do
  if [ -x "${CLI_BIN}" ]; then
    echo "==> Installing Quasar VSIX via ${CLI_BIN}..."
    "${CLI_BIN}" --uninstall-extension "antigravity.antigravity-code-review" 2>/dev/null || true
    "${CLI_BIN}" --uninstall-extension "quasar.quasar" 2>/dev/null || true
    "${CLI_BIN}" --install-extension "${VSIX_PATH}" --force || true
  fi
done

# Ensure ~/.antigravity/extensions exists if no extension directory exists yet
if ! ls "${HOME}"/.*/extensions/extensions.json >/dev/null 2>&1; then
  mkdir -p "${HOME}/.antigravity/extensions"
  echo "[]" > "${HOME}/.antigravity/extensions/extensions.json"
fi

# Also sync directly into extension directories & ensure extensions.json has the entry registered
for EXT_ROOT in "${HOME}"/.*/extensions; do
  if [ -d "${EXT_ROOT}" ] && [ -f "${EXT_ROOT}/extensions.json" ]; then
    rm -rf "${EXT_ROOT}/${OLD_EXT_NAME}" "${EXT_ROOT}/${OLD_QUASAR_EXT_NAME}" "${EXT_ROOT}/${OLD_CODEIUM_EXT_NAME}" "${EXT_ROOT}"/codeium.codeium-dev-* 2>/dev/null || true
    TARGET_EXT_DIR="${EXT_ROOT}/${EXT_NAME}"
    echo "==> Syncing native Quasar extension files to ${TARGET_EXT_DIR}..."
    mkdir -p "${TARGET_EXT_DIR}/media"
    mkdir -p "${TARGET_EXT_DIR}/bundled_plugin"
    cp "${SCRIPT_DIR}/extension/package.json" "${TARGET_EXT_DIR}/package.json"
    cp "${SCRIPT_DIR}/extension/extension.js" "${TARGET_EXT_DIR}/extension.js"
    cp "${SCRIPT_DIR}/extension/media/"* "${TARGET_EXT_DIR}/media/"
    cp "${SCRIPT_DIR}/README.md" "${TARGET_EXT_DIR}/README.md" 2>/dev/null || true
    cp "${SCRIPT_DIR}/CHANGELOG.md" "${TARGET_EXT_DIR}/CHANGELOG.md" 2>/dev/null || true
    cp -R "${SCRIPT_DIR}/plugin/"* "${TARGET_EXT_DIR}/bundled_plugin/" 2>/dev/null || true

    python3 - "${EXT_ROOT}/extensions.json" "${TARGET_EXT_DIR}" "${EXT_NAME}" "${VERSION}" <<'PY'
import json
import os
import sys
import time

p, target_dir, ext_name, version = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        data = []
except Exception:
    data = []

data = [
    e for e in data
    if (e.get("identifier") or {}).get("id") not in (
        "antigravity.antigravity-code-review",
        "quasar.quasar",
        "codeium.codeium-dev",
    )
]

data.append(
    {
        "identifier": {"id": "codeium.codeium-dev"},
        "version": version,
        "location": {
            "$mid": 1,
            "fsPath": target_dir,
            "external": f"file://{target_dir}",
            "path": target_dir,
            "scheme": "file",
        },
        "relativeLocation": ext_name,
        "metadata": {
            "installedTimestamp": int(time.time() * 1000),
            "pinned": False,
            "source": "vsix",
        },
    }
)

with open(p, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
  fi
done

# Clear cached extension scanner lists so Reload Window picks up Quasar immediately
rm -f "${HOME}/Library/Application Support/"*/CachedProfilesData/*/extensions.user.cache 2>/dev/null || true

echo ""
run_doctor

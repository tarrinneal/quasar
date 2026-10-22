#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_NAME="antigravity.antigravity-code-review-1.0.0"
PLUGIN_DEST="${HOME}/.gemini/config/plugins/code-review"

echo "==> Installing Antigravity Code Review plugin to ${PLUGIN_DEST}..."
mkdir -p "${PLUGIN_DEST}/skills/code-review/scripts"
mkdir -p "${PLUGIN_DEST}/agents"

cp "${SCRIPT_DIR}/plugin/plugin.json" "${PLUGIN_DEST}/plugin.json"
cp "${SCRIPT_DIR}/plugin/skills/code-review/SKILL.md" "${PLUGIN_DEST}/skills/code-review/SKILL.md"
cp "${SCRIPT_DIR}/plugin/skills/code-review/scripts/"*.py "${PLUGIN_DEST}/skills/code-review/scripts/"
chmod +x "${PLUGIN_DEST}/skills/code-review/scripts/"*.py

if [ -d "${SCRIPT_DIR}/plugin/agents" ]; then
  cp -R "${SCRIPT_DIR}/plugin/agents/"* "${PLUGIN_DEST}/agents/" 2>/dev/null || true
fi

# Install into Antigravity's extensions directory
for EXT_ROOT in "${HOME}"/.*/extensions; do
  if [ -d "${EXT_ROOT}" ] && [ -f "${EXT_ROOT}/extensions.json" ]; then
    TARGET_EXT_DIR="${EXT_ROOT}/${EXT_NAME}"
    echo "==> Installing native extension to ${TARGET_EXT_DIR}..."
    mkdir -p "${TARGET_EXT_DIR}/media"
    cp "${SCRIPT_DIR}/extension/package.json" "${TARGET_EXT_DIR}/package.json"
    cp "${SCRIPT_DIR}/extension/extension.js" "${TARGET_EXT_DIR}/extension.js"
    cp "${SCRIPT_DIR}/extension/media/review-icon.svg" "${TARGET_EXT_DIR}/media/review-icon.svg"

    EXT_JSON="${EXT_ROOT}/extensions.json"
    if [ -f "${EXT_JSON}" ]; then
      python3 - "${EXT_JSON}" "${TARGET_EXT_DIR}" "${EXT_NAME}" <<'PY'
import json
import sys
import time

ext_json_path, target_dir, ext_name = sys.argv[1], sys.argv[2], sys.argv[3]
with open(ext_json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

ext_id = "antigravity.antigravity-code-review"
data = [entry for entry in data if (entry.get("identifier") or {}).get("id") != ext_id]
data.append({
    "identifier": {"id": ext_id},
    "version": "1.0.0",
    "location": {
        "$mid": 1,
        "fsPath": target_dir,
        "external": f"file://{target_dir}",
        "path": target_dir,
        "scheme": "file"
    },
    "relativeLocation": ext_name,
    "metadata": {
        "isApplicationScoped": False,
        "isMachineScoped": False,
        "isBuiltin": False,
        "installedTimestamp": int(time.time() * 1000),
        "pinned": False,
        "source": "vsix",
        "targetPlatform": "universal",
        "updated": True,
        "private": False,
        "isPreReleaseVersion": False,
        "hasPreReleaseVersion": False,
        "preRelease": False
    }
})

with open(ext_json_path, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
    fi
  fi
done

echo "==> Antigravity Code Review installed successfully!"

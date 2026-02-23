#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"

cat > "${BIN_DIR}/openclaw-self-improve" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${ROOT}"
python3 scripts/self_improve_cycle.py "\$@"
EOF
chmod +x "${BIN_DIR}/openclaw-self-improve"

cat > "${BIN_DIR}/openclaw-skill-capture" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${ROOT}"
python3 scripts/skills/capture_skill.py "\$@"
EOF
chmod +x "${BIN_DIR}/openclaw-skill-capture"

cat > "${BIN_DIR}/openclaw-skill-validate" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${ROOT}"
python3 scripts/skills/validate_skill.py "\$@"
EOF
chmod +x "${BIN_DIR}/openclaw-skill-validate"

echo "Installed commands to ${BIN_DIR}:"
echo "  openclaw-self-improve"
echo "  openclaw-skill-capture"
echo "  openclaw-skill-validate"
echo "If needed, add this to your shell profile:"
echo "  export PATH=\"${BIN_DIR}:\$PATH\""

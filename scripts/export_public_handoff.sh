#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_REPO="Elijah-Wallis/eve-toc-build-review"
TARGET_REPO="$DEFAULT_REPO"
PUSH_CHANGES="false"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/export_public_handoff.sh [--repo OWNER/REPO] [--push]

Behavior:
  - clones the public handoff repo into /tmp
  - syncs a sanitized copy of the current repo
  - preserves .env.example (intentional public template)
  - excludes local secret env files, logs, runtime artifacts
  - runs a secret-pattern scan
  - commits changes locally and optionally pushes with --push
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      TARGET_REPO="${2:-}"
      shift 2
      ;;
    --push)
      PUSH_CHANGES="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TARGET_REPO" ]]; then
  echo "Target repo cannot be empty" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required" >&2
  exit 1
fi
if ! command -v rg >/dev/null 2>&1; then
  echo "rg (ripgrep) is required" >&2
  exit 1
fi

tmp_dir="$(mktemp -d "/tmp/eve-public-handoff-XXXXXX")"
repo_name="$(basename "$TARGET_REPO")"
worktree="$tmp_dir/$repo_name"
repo_url="https://github.com/${TARGET_REPO}.git"

echo "Cloning public handoff repo: $repo_url"
git clone --depth 1 "$repo_url" "$worktree" >/dev/null 2>&1

echo "Resetting worktree contents (preserving .git)"
find "$worktree" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

echo "Syncing sanitized snapshot from: $ROOT_DIR"
rsync -a --delete \
  --exclude='.git' \
  --exclude='.DS_Store' \
  --exclude='node_modules' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='.env.*.local' \
  --exclude='.env.retell.local' \
  --exclude='.env.cloudflare.local' \
  --exclude='logs' \
  --exclude='*.log' \
  --exclude='data/retell_calls' \
  --exclude='artifacts' \
  --exclude='*.tar.gz' \
  "$ROOT_DIR/" "$worktree/"

if [[ ! -f "$worktree/.env.example" ]]; then
  echo "ERROR: .env.example missing in exported handoff snapshot" >&2
  exit 1
fi

echo "Running secret scan on exported snapshot"
secret_pattern='(Isaiah5511|gmail_app_password|apify_api_[A-Za-z0-9]+|gho_[A-Za-z0-9_]+|eyJhbGciOiJIUzI1Ni|AC[a-f0-9]{32}|SK[a-f0-9]{32}|RETELL_API_KEY=key_[A-Za-z0-9]+|SUPABASE_SERVICE_KEY\s*=\s*\S+|TWILIO_AUTH_TOKEN\s*=\s*\S+|N8N_API_KEY\s*=\s*eyJ|transhumanism1!)'
if rg -n -S "$secret_pattern" \
  "$worktree/." \
  -g '!**/scripts/export_public_handoff.sh' \
  -g '!*.png' -g '!*.jpg' -g '!*.jpeg' -g '!*.gif' \
  >/tmp/eve_public_handoff_secret_hits.txt; then
  echo "ERROR: Secret scan hit(s) detected. Export aborted." >&2
  cat /tmp/eve_public_handoff_secret_hits.txt >&2
  exit 1
fi

cd "$worktree"
git config user.name "Codex Bot"
git config user.email "codex@example.com"

git add -A
if git diff --cached --quiet; then
  echo "No changes to export."
  echo "Public repo URL: https://github.com/${TARGET_REPO}"
  echo "Worktree: $worktree"
  exit 0
fi

commit_msg="Update sanitized public handoff snapshot"
git commit -m "$commit_msg" >/dev/null
echo "Committed handoff snapshot in temp clone."

if [[ "$PUSH_CHANGES" == "true" ]]; then
  git push origin main
  echo "Pushed to: https://github.com/${TARGET_REPO}"
else
  echo "Push skipped (pass --push to publish)."
fi

echo "Worktree: $worktree"
echo "Head commit: $(git rev-parse HEAD)"

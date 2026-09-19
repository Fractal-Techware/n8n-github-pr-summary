#!/usr/bin/env sh
# Send a signed, GitHub-shaped pull_request delivery to the workflow's webhook — no repository needed.
#
#   FTW_GH_WEBHOOK_SECRET=... ./examples/send-test-delivery.sh
#   FTW_GH_WEBHOOK_SECRET=... ./examples/send-test-delivery.sh https://n8n.example.com examples/samples/pull_request.opened.json
#
# The pull request in the sample payload must exist in a repository your GitHub token can read and
# must match github.repo_allowlist in the Settings node — the workflow fetches its real diff.
set -eu
BASE="${1:-http://localhost:5678}"
PAYLOAD="${2:-$(dirname "$0")/samples/pull_request.opened.json}"

if [ -z "${FTW_GH_WEBHOOK_SECRET:-}" ]; then
  echo "set FTW_GH_WEBHOOK_SECRET to the secret stored in the n8n credential 'GitHub webhook secret'" >&2
  exit 1
fi

SIG="sha256=$(openssl dgst -sha256 -hmac "$FTW_GH_WEBHOOK_SECRET" -hex < "$PAYLOAD" | awk '{print $NF}')"

echo "POST $BASE/webhook/ftw-gh-pr-summary  <- $PAYLOAD"
curl -sS -X POST "$BASE/webhook/ftw-gh-pr-summary" \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: pull_request' \
  -H "X-GitHub-Delivery: $(uuidgen 2>/dev/null || date +%s)" \
  -H "X-Hub-Signature-256: $SIG" \
  --data-binary "@$PAYLOAD"
echo
echo "Now open n8n -> Executions. The comment follows within a few seconds."

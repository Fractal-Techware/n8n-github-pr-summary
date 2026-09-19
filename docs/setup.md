# Setup

Everything the workflow needs: one n8n instance GitHub can reach, a GitHub token, a webhook secret
and an LLM endpoint. No database, no sub-workflows, no GitHub App required.

## 1. Requirements

| | Version tested |
|---|---|
| n8n | 2.39.7 (self-hosted, Docker; Cloud works too) |
| GitHub | github.com REST API `2022-11-28`, and GitHub Enterprise Server via `github.api_url` |
| LLM endpoint | any OpenAI-compatible `POST /v1/chat/completions` — OpenAI, Azure OpenAI, OpenRouter, Together, vLLM, Ollama (`/v1/chat/completions`) |

Core n8n nodes only (Webhook, Crypto, Set, Code, If, HTTP Request, Respond to Webhook), so it also
runs on n8n versions without the LangChain nodes.

## 2. Import the workflow

n8n UI → **Workflows** → **Import from File** → `workflows/github-pr-summary.json`, or inside the
container: `n8n import:workflow --input=/path/to/github-pr-summary.json`.

Create the three credentials it references (**Credentials** → **New**), keeping the names:

| Credential | Type | Value |
|---|---|---|
| `GitHub webhook secret` | Crypto | Secret: the same random string you paste into GitHub's webhook form (`openssl rand -hex 24`) |
| `GitHub API token` | Header Auth | Name `Authorization`, value `Bearer <token>` |
| `PR summary LLM API key` | Header Auth | Name `Authorization`, value `Bearer <your LLM key>` |

**GitHub token permissions.** A fine-grained personal access token scoped to the repositories you
allowlist, with exactly two repository permissions:

| Permission | Access | Why |
|---|---|---|
| Pull requests | Read and write | Read the changed files, write the summary comment |
| Contents | Read-only | Required by GitHub for pull request reads |

A classic token needs `repo` (or `public_repo` for public repositories only). A machine user or a
GitHub App installation token works the same way — the workflow only sends the `Authorization`
header you configure.

If your provider does not use bearer tokens (Azure OpenAI uses `api-key`), change the header name in
the LLM credential.

## 3. Fill in the Settings node

| Field | Meaning |
|---|---|
| `github.api_url` | `https://api.github.com`, or `https://github.example.com/api/v3` for Enterprise Server |
| `github.repo_allowlist` | `["org/repo", "org/*"]`. **Empty processes nothing** — this is the guard against a leaked webhook URL |
| `github.ignore_bot_senders` | Skip pull requests opened by bots (default `true`) |
| `github.ignore_senders` | Extra logins to skip |
| `llm.url` / `llm.model` | Chat-completions URL and the model id. Pin the model — a "latest" alias changes your output without warning |
| `llm.timeout_seconds` | HTTP timeout for the model call (default 90) |
| `llm.max_diff_bytes` | Hard cap on diff bytes sent to the model (default 100000). Files over the cap are named, not sent |
| `llm.max_files` | Maximum number of files whose patches are considered (default 300) |
| `llm.ignore_file_globs` | Paths never sent to the model: lockfiles, `dist/`, `vendor/`, images, snapshots |
| `prompt` | The system prompt |
| `comment.marker` | The hidden HTML marker that identifies this workflow's comment. Change it only if you also change it everywhere it was already posted |
| `comment.on_llm_failure` | `note` (post an honest "unavailable" comment) or `skip` (post nothing) |

## 4. Point GitHub at it

Activate the workflow and copy the production webhook URL:
`https://<your-n8n>/webhook/ftw-gh-pr-summary`.

In the repository (or organisation) → **Settings** → **Webhooks** → **Add webhook**:

| Field | Value |
|---|---|
| Payload URL | `https://<your-n8n>/webhook/ftw-gh-pr-summary` |
| Content type | `application/json` |
| Secret | the same string as the `GitHub webhook secret` credential |
| Events | "Let me select individual events" → **Pull requests** only |

The workflow acts on `opened`, `synchronize`, `reopened` and `ready_for_review`, and answers `200`
with a reason for everything else. Deliveries without a valid signature get `401`.

Try it without GitHub:

```bash
FTW_GH_WEBHOOK_SECRET=<your webhook secret> ./examples/send-test-delivery.sh http://localhost:5678
```

(the pull request in `examples/samples/pull_request.opened.json` must exist in a repository your
token can read and be inside the allowlist, because the workflow fetches its real diff).

## 5. Environment variables

The workflow reads no environment variables — configuration lives in the Settings node and the three
credentials. These are used by the supporting files:

| Variable | Used by | Purpose |
|---|---|---|
| `N8N_ENCRYPTION_KEY` | `examples/docker-compose.yml` | Encrypts credentials in the n8n volume. Replace the placeholder before storing a real token |
| `WEBHOOK_URL` | `examples/docker-compose.yml` | The public base URL GitHub delivers to |
| `FTW_GH_WEBHOOK_SECRET` | `examples/send-test-delivery.sh` | The webhook secret, used to sign the test delivery |

No file in this repository contains a token, key or webhook URL — only placeholders.

## 6. What the workflow does, node by node

| Node | What it does |
|---|---|
| `GitHub webhook` | `POST /webhook/ftw-gh-pr-summary`, raw body kept so the signature can be checked byte for byte |
| `Compute HMAC` | HMAC-SHA256 of the raw body with the webhook secret |
| `Settings` | The JSON above |
| `Verify delivery` | Constant-time signature comparison, required headers, event and action filter, repository allowlist, bot and ignored senders, drafts and closed pull requests |
| `Respond` | Answers GitHub immediately (`202` accepted, `200` with a reason, `401` bad signature) — GitHub's delivery timeout is never hit by a slow model |
| `List changed files` | `GET /repos/{repo}/pulls/{n}/files`, following GitHub's `Link` pagination |
| `Build prompt` | Applies `ignore_file_globs`, caps the diff at `max_diff_bytes`, redacts secrets, records what was left out |
| `Ask LLM` | One `POST` to `llm.url`. An HTTP error is passed on instead of failing the workflow |
| `Render comment` | Parses the JSON answer, sanitises the model's text (no forged marker, no headings, no mass `@mentions`) and adds the disclosure line |
| `List existing comments` | `GET /repos/{repo}/issues/{n}/comments`, paginated |
| `Create or update?` | Finds a previous comment carrying the marker |
| `Post comment` | `POST` a new comment or `PATCH` the existing one |

## 7. Costs and limits

One LLM call per pull request event (`opened`, `synchronize`, `reopened`, `ready_for_review`), capped
at `max_diff_bytes` of diff. Lockfiles and generated files never reach the model, which is where most
of the tokens would otherwise go. A repository with 50 pull requests a week and a few pushes each is
a few hundred calls a week, on the model you choose.

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| GitHub shows `401` in Recent Deliveries | The webhook secret in GitHub and in the Crypto credential differ |
| GitHub shows `200` with `repo-not-allowlisted` | Add the repository to `github.repo_allowlist` |
| `200` with `action-not-handled` | Normal — that action is not summarised (for example `labeled`) |
| `200` with `draft-pull-request` | Drafts are skipped; mark ready for review and the `ready_for_review` event summarises it |
| Comment says "AI pull request summary unavailable (`llm-http-401`)" | The LLM credential is wrong for that provider |
| Comment says `unparseable-answer` | The model ignored the JSON instruction; try a stronger model |
| `List changed files` returns 403/404 | The token cannot read that repository, or `github.api_url` is wrong for Enterprise Server |
| Two comments on one pull request | `comment.marker` was changed after the first comment was posted |

Run `./run-tests.sh` after any change: it imports your edited workflow into a throwaway n8n and
checks all of the above end to end.

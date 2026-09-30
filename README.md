# GitHub pull request → LLM summary → PR comment: one importable n8n workflow, tested end to end

[![e2e tests](https://github.com/Fractal-Techware/n8n-github-pr-summary/actions/workflows/test.yml/badge.svg)](https://github.com/Fractal-Techware/n8n-github-pr-summary/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![n8n 2.39.7](https://img.shields.io/badge/n8n-2.39.7-ea4b71?logo=n8n&logoColor=white)
![Workflow: 1](https://img.shields.io/badge/workflow-1%20importable%20JSON-informational)
![e2e tests: 12](https://img.shields.io/badge/e2e%20tests-12%20passing-brightgreen)

A pull request arrives with the title "fix stuff" and 40 changed files. **This workflow reads the
diff and posts one comment**: what changed, why it matters, the risks it can see, and where the
reviewer should look first — updated on every new commit, never duplicated.

One file you import into n8n. No sub-workflows, no database, no GitHub App, no LangChain nodes —
core nodes only (Webhook, Crypto, Set, Code, If, HTTP Request, Respond to Webhook).

What makes it safe to point at a real repository:

- **Deliveries are verified.** HMAC-SHA256 over the raw body, compared in constant time, before
  anything is fetched or any token is spent. Bad signature → `401`, nothing else happens.
- **A repository allowlist**, so a leaked webhook URL cannot make you summarise someone else's code.
  Drafts, closed pull requests and bot senders are skipped.
- **Secrets in a diff never reach the model** — AWS keys, tokens, JWTs, private keys, connection
  strings and `api_key = …` assignments are redacted first, and the comment says how many.
- **Lockfiles, `dist/`, `vendor/`, images and snapshots are never sent**, and the diff is capped at
  a byte budget you set. The comment discloses what was left out.
- **One comment per pull request.** It is matched by a hidden marker and updated in place; human
  comments are never touched.
- **Nothing is invented.** If the model fails or answers something unparseable, the comment says so
  (or posts nothing, your choice).
- **Model output is sanitised** before it becomes a GitHub comment: it cannot forge the marker or
  `@mention` your whole organisation.

Every one of those claims has an end-to-end test in this repository ([see below](#how-it-is-tested)).

By [Fractal Techware](https://github.com/Fractal-Techware). MIT licensed.

## What's included

| File | What it is |
|---|---|
| `workflows/github-pr-summary.json` | The workflow: webhook → HMAC verify → diff → LLM → upserted PR comment |
| `tests/run_tests.py` | 12 end-to-end tests that run the real workflow inside a pinned n8n container |
| `tests/mock/mock.py` | Stdlib mock of the GitHub REST subset (with real `Link` pagination) and the LLM |
| `tests/lib/stack.py` | The Docker harness: imports the shipped JSON, publishes it, signs real deliveries |
| `tests/lib/payloads.py` | `pull_request` webhook payloads and HMAC delivery signing |
| `tests/fixtures/pr-files.json` | A realistic changed-file list: source, tests, docs, a lockfile, a binary, a file with no patch |
| `examples/docker-compose.yml` | A pinned n8n to try it in |
| `examples/send-test-delivery.sh` | Sign and send a delivery yourself — no repository needed |
| `docs/setup.md` | Credentials, token permissions, every settings field, costs, troubleshooting |

1 workflow · 14 nodes · 12 end-to-end tests · pinned to n8n 2.39.7.

## 60-second quick start

```bash
git clone https://github.com/Fractal-Techware/n8n-github-pr-summary.git
cd n8n-github-pr-summary
./run-tests.sh      # proves it works: pinned n8n + mocked GitHub and LLM, no API keys needed
```

```
stack ready in 16s (http://127.0.0.1:5698)
PASS  test_drafts_bots_and_foreign_repos_are_skipped  (3.3s)
PASS  test_human_comments_are_never_touched  (0.6s)
...
12/12 tests passed in 53s
```

Then, in your own n8n:

1. **Import** `workflows/github-pr-summary.json` (Workflows → Import from File).
2. **Create three credentials**: `GitHub webhook secret` (Crypto — `openssl rand -hex 24`),
   `GitHub API token` (Header Auth, `Authorization: Bearer <token>`) and `PR summary LLM API key`
   (Header Auth, `Authorization: Bearer <llm key>`).
3. **Edit the Settings node**: `github.repo_allowlist` (it is empty of your repos by default, so
   nothing is processed until you add them), `llm.url` and `llm.model`.
4. **Activate**, then add a repository webhook pointing at
   `https://<your-n8n>/webhook/ftw-gh-pr-summary`, content type `application/json`, the same secret,
   and the **Pull requests** event only.

The GitHub token needs just two repository permissions: **Pull requests: read and write** and
**Contents: read-only**. Full walkthrough and troubleshooting: [docs/setup.md](docs/setup.md).

## What the comment looks like

```markdown
<!-- ftw-pr-summary -->
### AI pull request summary

**What changed**
- The payment client retries idempotent charges up to three times on 5xx responses
- Every attempt now carries the same Idempotency-Key header
- New unit tests cover the retry and the header

**Why it matters**
Checkout stops failing on transient 503s from the payment provider, without risking a double charge.

**Risk notes**
- Retrying a non-idempotent POST would double charge if the key is ever omitted

**Suggested review focus**
- src/payments/client.py: the retry predicate and the backoff bounds

<sub>commit `d34db33` · 6 files (+1333/-174) · 2 ignored (package-lock.json, payment-flow.png) ·
1 file with no diff (binary or too large) · gpt-5-mini · AI-generated, verify before relying on it.</sub>
```

(Everything in the `<sub>` line is computed from the diff, not from the model.)

## How it is tested

`./run-tests.sh` starts one **n8n 2.39.7** container and one Python container that pretends to be the
GitHub REST API and your LLM endpoint. The workflow JSON is imported exactly as it ships — only the
Settings node is overlaid so the two URLs point at the mock — then published, and the tests deliver
**real, HMAC-signed GitHub webhooks** to it and assert on what n8n sent and stored:

| Test | Proves |
|---|---|
| `test_pull_request_is_summarised_and_commented` | Diff and patches reach the model with the model from Settings; the comment carries the marker, sections, counts and the AI disclosure |
| `test_second_delivery_updates_the_same_comment` | A new commit `PATCH`es the existing comment — one comment, not two |
| `test_human_comments_are_never_touched` | Matching is on the hidden marker only |
| `test_invalid_signature_is_rejected_before_any_work` | Bad HMAC → `401`, no GitHub call, no model call |
| `test_ping_and_unhandled_actions_are_ignored` | `ping` and unhandled actions answer `200` and stop |
| `test_drafts_bots_and_foreign_repos_are_skipped` | Drafts, bot senders and repositories outside the allowlist cost nothing |
| `test_ignored_files_are_not_sent_to_the_model` | The lockfile and the image stay out of the prompt, and the comment discloses it |
| `test_secrets_in_a_diff_are_redacted_before_the_prompt` | An AWS key and an `sk-…` key added in the diff never reach the model |
| `test_paginated_file_lists_are_followed` | A 150-file pull request is fetched across pages and fully counted |
| `test_llm_failure_posts_an_honest_note` | LLM 500 → a comment that says so, nothing guessed |
| `test_malformed_and_fenced_answers` | Prose is refused; JSON inside a ```json fence is accepted |
| `test_model_output_cannot_forge_the_marker_or_mass_mention` | Model text cannot inject a second marker or `@everyone` |

No test reaches the internet and no API key is involved; CI runs the same script
([`.github/workflows/test.yml`](.github/workflows/test.yml)).

## Want all the workflows?

This repository is the free edition of the
**[n8n GitHub AI Workflows](https://store.fractaltechware.com/l/n8n-github-ai-workflows?utm_source=github&utm_medium=readme&utm_campaign=free-repo)**
pack — same build, same test standard, the rest of the repository chores:

| | **Free** (this repo) | **Starter** $19 | **Pro** $49 | **Studio** $99 |
|---|:---:|:---:|:---:|:---:|
| Importable workflows | 1 | 9 | 15 | 18 |
| AI pull request summaries | yes | yes | yes | yes |
| LLM providers | any OpenAI-compatible endpoint | + Anthropic, Ollama, OpenAI Responses API (router) | same | same |
| Auth | personal access token | + GitHub App, shared webhook verification with replay protection | same | same |
| Release notes into the draft release, CI failure explanations | – | yes | yes | yes |
| PR risk labels, issue triage, dependency digest, stale PR nudge, daily digest, error notifier | – | – | yes | yes |
| Per-repo `.github/ftw-ai.yml` config, Conventional Commit title status check, weekly security digest, prompt eval kit (16 golden cases), GitHub App + GHES guides | – | – | – | yes |
| License | MIT | own organization | own organization | client / agency use |

[See the full pack on Gumroad →](https://store.fractaltechware.com/l/n8n-github-ai-workflows?utm_source=github&utm_medium=readme&utm_campaign=free-repo)

More free, tested building blocks: [github.com/Fractal-Techware](https://github.com/Fractal-Techware).

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Every behaviour change
needs a test in `tests/run_tests.py`.

## License

[MIT](LICENSE) © 2026 Fractal Techware SRL

# Contributing

Thanks for helping improve this workflow.

- **Bug reports** (the workflow errors, comments the wrong thing, or stays silent): open an issue with
  your n8n version, the model and endpoint you use, github.com or Enterprise Server, the failing node and its error — and please
  remove tokens, webhook secrets and anything else secret from what you paste.
- **Pull requests**: every behaviour change needs a test in `tests/run_tests.py` that fails without
  your change. Tests must stay offline: the workflow may only talk to the mock in `tests/mock/mock.py`,
  and no test may require an API key.
- Run `./run-tests.sh` before opening the PR; CI runs the same script.
- Edit `workflows/github-pr-summary.json` in n8n and export it back, or edit the JSON directly —
  either way keep the node ids, the credential ids and the `Settings` node name, because the test
  harness imports the file as it ships and overlays only that node.

Conventions: a delivery is verified before anything is fetched, no secret from a diff ever reaches
the model, and nothing is invented when the model's answer cannot be parsed.

By contributing you agree that your contribution is licensed under the MIT License.

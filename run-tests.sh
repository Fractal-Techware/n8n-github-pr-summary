#!/usr/bin/env bash
# Run the end-to-end test suite for the workflow in this repository.
#
#   ./run-tests.sh            # all tests
#   ./run-tests.sh -k redact  # only tests whose name matches
#
# Needs Docker and Python 3.9+. Nothing leaves your machine: the workflow is imported into a
# throwaway, pinned n8n container and only talks to a local mock of the GitHub API and the LLM.
# No API keys are required or used.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "the docker daemon is not reachable" >&2; exit 1; }

for image in n8nio/n8n:2.39.7 python:3.12-alpine; do
  docker image inspect "$image" >/dev/null 2>&1 || docker pull --quiet "$image"
done

exec python3 tests/run_tests.py "$@"

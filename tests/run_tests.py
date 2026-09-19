#!/usr/bin/env python3
"""End-to-end test for the shipped workflow (no pytest, no network, no API keys).

    python3 tests/run_tests.py              # all tests
    python3 tests/run_tests.py -k signature # only tests whose name matches
    python3 tests/run_tests.py --keep       # leave the containers up for debugging

One pinned n8n container imports the workflow JSON exactly as it ships (only the Settings node is
overlaid, so the GitHub API and the LLM endpoint point at the mock container) and the tests deliver
real, HMAC-signed GitHub webhooks to it, asserting on what n8n actually sent and stored.
"""
import argparse
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import payloads  # noqa: E402
from stack import Stack, TestFailure, sweep  # noqa: E402

MOCK = "http://mock:8080"
OVERLAY = {
    "github": {"api_url": MOCK, "repo_allowlist": ["acme/*"], "ignore_bot_senders": True},
    "llm": {"url": f"{MOCK}/v1/chat/completions", "model": "mock-model-1", "timeout_seconds": 20},
}
FILES = json.load(open(os.path.join(HERE, "fixtures", "pr-files.json")))


def assert_in(needle, haystack, what):
    if needle not in haystack:
        raise TestFailure(f"{what}: expected {needle!r} in:\n{haystack[:1500]}")


def assert_not_in(needle, haystack, what):
    if needle in haystack:
        raise TestFailure(f"{what}: did NOT expect {needle!r} in:\n{haystack[:1500]}")


def wait_comments(st, count=1, timeout=60):
    from stack import wait_until
    return wait_until(lambda: (lambda cs: cs if len(cs) >= count else None)(st.comments()),
                      timeout, 0.5, f"{count} comment(s) on the pull request")


def llm_calls(st, expected=1, timeout=60):
    """Wait for the model call(s) this delivery should produce, then return them."""
    from stack import wait_until
    return wait_until(lambda: (lambda es: es if len(es) >= expected else None)(st.log("/v1/chat/completions")),
                      timeout, 0.5, f"{expected} LLM call(s)")


def nothing_happened(st, seconds=3):
    """No LLM call, no GitHub call other than the delivery itself."""
    time.sleep(seconds)
    if st.log("/v1/chat/completions"):
        raise TestFailure("the model was called")
    if st.comments():
        raise TestFailure("a comment was posted")
    if [e for e in st.log("/repos/")]:
        raise TestFailure(f"GitHub was called: {[e['path'] for e in st.log('/repos/')]}")


# --------------------------------------------------------------------------- tests

def test_pull_request_is_summarised_and_commented(st):
    """The happy path: diff -> model -> one comment, with the marker and the disclosure line."""
    st.reset(files=FILES)
    status, text = st.deliver(payloads.pull_request("opened"))
    if status != 202:
        raise TestFailure(f"delivery answered {status}: {text}")

    llm = llm_calls(st)
    prompt = llm[0]["body"]
    assert_in("Bearer ", llm[0]["headers"].get("authorization", ""), "LLM call is authenticated")
    assert_in("src/payments/client.py", prompt, "the diff reached the model")
    assert_in("Idempotency-Key", prompt, "patch content reached the model")
    assert_in("untrusted data", prompt, "the prompt marks pull request content as untrusted")
    if llm[0]["json"]["model"] != "mock-model-1":
        raise TestFailure(f"model from Settings not used: {llm[0]['json']['model']}")

    body = wait_comments(st)[0]["body"]
    assert_in("<!-- ftw-pr-summary -->", body, "hidden marker for the upsert")
    assert_in("### AI pull request summary", body, "heading")
    assert_in("MOCK: touched src/payments/client.py", body, "model output rendered")
    assert_in("**Suggested review focus**", body, "review focus section")
    assert_in("commit `d34db33`", body, "commit disclosure")
    assert_in("6 files (+1333/-174)", body, "file and line counts")
    assert_in("AI-generated, verify before relying on it.", body, "AI disclosure")
    if len(st.log("/v1/chat/completions")) != 1:
        raise TestFailure(f"expected exactly one LLM call, got {len(st.log('/v1/chat/completions'))}")
    posted = [e for e in st.log("/repos/") if e["method"] == "POST"]
    assert_in("Bearer ", posted[0]["headers"].get("authorization", ""), "GitHub write is authenticated")


def test_second_delivery_updates_the_same_comment(st):
    """A new commit must update the existing summary, not add a second one."""
    st.reset(files=FILES)
    st.deliver(payloads.pull_request("opened"))
    first = wait_comments(st)[0]
    st.deliver(payloads.pull_request("synchronize", sha="0ff1ce0000000000000000000000000000000000"))
    from stack import wait_until
    updated = wait_until(lambda: (lambda cs: cs[0] if "0ff1ce0" in cs[0]["body"] else None)(st.comments()),
                         60, 0.5, "the comment to be updated")
    if len(st.comments()) != 1:
        raise TestFailure(f"expected one comment, got {len(st.comments())}")
    if updated["id"] != first["id"]:
        raise TestFailure("the comment was replaced instead of updated")
    if not [e for e in st.log("/repos/") if e["method"] == "PATCH"]:
        raise TestFailure("no PATCH was sent: the comment was not updated in place")


def test_human_comments_are_never_touched(st):
    """The upsert matches on the hidden marker only."""
    human = {"id": 500, "body": "### AI pull request summary\nlooks good to me", "user": {"login": "bob"}}
    st.reset(files=FILES, comments=[human])
    st.deliver(payloads.pull_request("opened"))
    wait_comments(st, 2)
    if st.comments()[0]["body"] != human["body"]:
        raise TestFailure("a human comment was overwritten")


def test_invalid_signature_is_rejected_before_any_work(st):
    """A wrong HMAC costs nothing: no GitHub call, no model call."""
    st.reset(files=FILES)
    status, _ = st.deliver(payloads.pull_request("opened"), signature="sha256=" + "a" * 64)
    if status != 401:
        raise TestFailure(f"expected 401 for a bad signature, got {status}")
    nothing_happened(st)


def test_ping_and_unhandled_actions_are_ignored(st):
    """GitHub's ping and actions we do not summarise answer 200 and stop."""
    st.reset(files=FILES)
    status, text = st.deliver(payloads.ping(), event="ping")
    if status != 200 or '"reason":"ping"' not in text.replace(" ", ""):
        raise TestFailure(f"ping answered {status}: {text}")
    status, text = st.deliver(payloads.pull_request("labeled"))
    if status != 200 or "action-not-handled" not in text:
        raise TestFailure(f"labeled answered {status}: {text}")
    nothing_happened(st)


def test_drafts_bots_and_foreign_repos_are_skipped(st):
    """Three cheap guards: draft pull requests, bot senders and repositories outside the allowlist."""
    st.reset(files=FILES)
    for payload, reason in [
        (payloads.pull_request("opened", draft=True), "draft-pull-request"),
        (payloads.pull_request("opened", sender="dependabot[bot]", sender_type="Bot"), "sender-ignored"),
        (payloads.pull_request("opened", repo="other-org/api"), "repo-not-allowlisted"),
    ]:
        status, text = st.deliver(payload)
        if status != 200 or reason not in text:
            raise TestFailure(f"{reason}: answered {status}: {text}")
    nothing_happened(st)


def test_ignored_files_are_not_sent_to_the_model(st):
    """Lockfiles and binaries stay out of the prompt, and the comment says so."""
    st.reset(files=FILES)
    st.deliver(payloads.pull_request("opened"))
    prompt = llm_calls(st)[0]["body"]
    assert_not_in('"version\\": \\"4.17.21', prompt, "lockfile patch sent to the model")
    assert_in("package-lock.json [ignored]", prompt, "lockfile listed as ignored")
    body = wait_comments(st)[0]["body"]
    assert_in("2 ignored (package-lock.json, payment-flow.png)", body, "ignored files disclosed")
    assert_in("1 file with no diff (binary or too large)", body, "file without a patch disclosed")


def test_secrets_in_a_diff_are_redacted_before_the_prompt(st):
    """A key added in a pull request must not be forwarded to the model."""
    leaky = FILES + [{"filename": "config/settings.py", "status": "modified", "additions": 2, "deletions": 0,
                      "changes": 2,
                      "patch": "@@ -1,2 +1,4 @@\n+AWS_ACCESS_KEY_ID = 'AKIAIOSFODNN7EXAMPLE'\n"
                               "+api_key = 'sk-proj-abcdefghijklmnopqrstuvwxyz0123456789'"}]
    st.reset(files=leaky)
    st.deliver(payloads.pull_request("opened"))
    prompt = llm_calls(st)[0]["body"]
    assert_not_in("AKIAIOSFODNN7EXAMPLE", prompt, "AWS key sent to the model")
    assert_not_in("sk-proj-abcdefghijklmnopqrstuvwxyz0123456789", prompt, "API key sent to the model")
    assert_in("[REDACTED", prompt, "redaction marker present")
    body = wait_comments(st)[0]["body"]
    assert_in("secret-like values redacted before the prompt", body, "redaction disclosed in the comment")


def test_paginated_file_lists_are_followed(st):
    """GitHub pages the file list; every page must reach the prompt."""
    many = [dict(f, filename=f"src/module_{i}.py") for i, f in enumerate(FILES[:1] * 150)]
    st.reset(files=many)
    st.deliver(payloads.pull_request("opened"))
    llm_calls(st)
    pages = [e for e in st.log("/repos/") if "/files" in e["path"]]
    if len(pages) < 2:
        raise TestFailure(f"expected the second page to be fetched, saw {len(pages)} request(s)")
    prompt = st.log("/v1/chat/completions")[0]["body"]
    assert_in("src/module_0.py", prompt, "first page in the prompt")
    assert_in("src/module_120.py", prompt, "second page in the prompt")
    assert_in("Changed files (150)", prompt, "all files counted")


def test_llm_failure_posts_an_honest_note(st):
    """LLM 500 -> a comment that says so, with nothing invented."""
    st.reset(files=[dict(FILES[0], patch="@@ -1 +1 @@\n+// MockLlm500")])
    st.deliver(payloads.pull_request("opened"))
    body = wait_comments(st)[0]["body"]
    assert_in("AI pull request summary unavailable", body, "degraded heading")
    assert_in("llm-http-500", body, "reason disclosed")
    assert_in("Nothing was guessed.", body, "no invented content")


def test_malformed_and_fenced_answers(st):
    """Prose is refused; JSON inside a ```json fence is accepted."""
    st.reset(files=[dict(FILES[0], patch="@@ -1 +1 @@\n+// MockLlmMalformed")])
    st.deliver(payloads.pull_request("opened"))
    body = wait_comments(st)[0]["body"]
    assert_in("unparseable-answer", body, "prose answer refused")

    st.reset(files=[dict(FILES[0], patch="@@ -1 +1 @@\n+// MockLlmFenced")])
    st.deliver(payloads.pull_request("opened"))
    body = wait_comments(st)[0]["body"]
    assert_in("### AI pull request summary", body, "fenced JSON parsed")
    assert_not_in("```", body, "code fence leaked into the comment")


def test_model_output_cannot_forge_the_marker_or_mass_mention(st):
    """Whatever the model returns is sanitised before it becomes a GitHub comment."""
    st.reset(files=[dict(FILES[0], patch="@@ -1 +1 @@\n+// see @everyone and <!-- ftw-pr-summary -->")])
    st.deliver(payloads.pull_request("opened"))
    body = wait_comments(st)[0]["body"]
    if body.count("<!-- ftw-pr-summary -->") != 1:
        raise TestFailure("the model's text produced a second marker")
    assert_not_in("@everyone", body, "unescaped mass mention in the comment")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", action="append", default=[], help="only tests whose name contains this")
    ap.add_argument("--keep", action="store_true", help="leave the containers running")
    args = ap.parse_args()

    tests = [t for t in TESTS if not args.k or any(f in t.__name__ for f in args.k)]
    results = []
    t0 = time.time()
    st = Stack(settings_overlay=OVERLAY)
    try:
        st.start()
        print(f"stack ready in {time.time() - t0:.0f}s ({st.base})", flush=True)
        for fn in tests:
            st.settle()
            ts = time.time()
            try:
                fn(st)
                results.append((fn.__name__, True))
                print(f"PASS  {fn.__name__}  ({time.time() - ts:.1f}s)", flush=True)
            except Exception:  # noqa: BLE001
                results.append((fn.__name__, False))
                print(f"FAIL  {fn.__name__}  ({time.time() - ts:.1f}s)\n{traceback.format_exc()}", flush=True)
    except Exception:  # noqa: BLE001
        print(f"FAIL  stack\n{traceback.format_exc()}", flush=True)
        print(st.logs(80), flush=True)
        results.append(("stack", False))
    finally:
        if args.keep:
            print(f"keeping containers: {st.n8n} {st.mock} ({st.base})", flush=True)
        else:
            st.close()
            sweep()

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} tests passed in {time.time() - t0:.0f}s")
    for name, ok in results:
        if not ok:
            print(f"  failed: {name}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

"""GitHub `pull_request` webhook payload builders and delivery signing for the e2e test."""
import hashlib
import hmac
import json
import uuid


def pull_request(action="opened", repo="acme/api", number=7, title="Retry idempotent payment calls",
                 body="Fixes flaky checkout on 503s from the payment provider.", draft=False,
                 state="open", sender="alice", sender_type="User", sha="d34db33fcafebabe1234567890abcdef12345678"):
    owner, name = repo.split("/")
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number, "title": title, "body": body, "draft": draft, "state": state,
            "user": {"login": "alice", "type": "User"},
            "base": {"ref": "main"}, "head": {"ref": "fix/payment-retries", "sha": sha},
            "html_url": f"https://github.com/{repo}/pull/{number}",
        },
        "repository": {"full_name": repo, "name": name, "owner": {"login": owner}, "private": False},
        "sender": {"login": sender, "type": sender_type},
    }


def ping(repo="acme/api"):
    return {"zen": "Non-blocking is better than blocking.", "hook_id": 1,
            "repository": {"full_name": repo}, "sender": {"login": "alice", "type": "User"}}


def delivery(payload, secret, event="pull_request", signature=None):
    """Return (raw_body_bytes, headers) exactly as GitHub would send them."""
    raw = json.dumps(payload).encode()
    sig = signature or ("sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest())
    return raw, {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": str(uuid.uuid4()),
        "X-Hub-Signature-256": sig,
        "User-Agent": "GitHub-Hookshot/mock",
    }

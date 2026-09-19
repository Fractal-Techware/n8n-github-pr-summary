#!/usr/bin/env python3
"""Mock of the GitHub REST subset this workflow uses, plus an OpenAI-compatible LLM (stdlib only).

Runs inside a python:3.12-alpine container on the test network:

    python3 mock.py        # listens on :8080

GitHub endpoints (every one requires an Authorization header, like github.com):
    GET   /repos/{owner}/{repo}/pulls/{n}/files       paginated with a real Link header
    GET   /repos/{owner}/{repo}/issues/{n}/comments   paginated with a real Link header
    POST  /repos/{owner}/{repo}/issues/{n}/comments   creates a comment
    PATCH /repos/{owner}/{repo}/issues/comments/{id}  updates a comment

LLM endpoint:
    POST  /v1/chat/completions

Control endpoints (test harness only):
    POST /_reset {"files": [...], "comments": [...]}   replace the fixture state and clear the log
    GET  /_state      the comments currently stored
    GET  /_log        every request received (method, path, headers, body)
    GET  /healthz     readiness

Failure modes are triggered by markers in the prompt, so one running stack can exercise them:
    MockLlm500        the LLM answers 500
    MockLlmMalformed  the LLM answers prose that is not JSON
    MockLlmFenced     valid JSON wrapped in prose and a ```json fence
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.Lock()
LOG = []
STATE = {"files": [], "comments": [], "next_id": 1001}
PUBLIC_URL = "http://mock:8080"


def llm_answer(prompt):
    """Return (status, text). status is None for a normal answer."""
    if "MockLlm500" in prompt:
        return 500, None
    if "MockLlmMalformed" in prompt:
        return None, "Sure! Here's the summary: what_changed = some files, risks: none,,"
    seen = re.findall(r"^--- (\S+) \(", prompt, re.M)
    obj = {
        "what_changed": [f"MOCK: touched {p}" for p in seen[:4]] or ["MOCK: no diff was sent"],
        "why_it_matters": "MOCK: the reviewer can see the intent of the change at a glance.",
        "risks": ["MOCK: retrying a non-idempotent call could double charge"],
        "review_focus": [f"MOCK: read {seen[0]} first" if seen else "MOCK: nothing to focus on"],
    }
    if "MockLlmFenced" in prompt:
        return None, "Here is the JSON:\n```json\n" + json.dumps(obj, indent=2) + "\n```\nHope this helps."
    return None, json.dumps(obj)


def prompt_text(body):
    parts = []
    for m in body.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(str(p.get("text", "")) for p in c if isinstance(p, dict))
    return "\n".join(parts)


def page(items, query, base_path):
    """Slice `items` like GitHub does and build the Link header for the next/last page."""
    per = max(1, min(100, int(query.get("per_page", ["30"])[0])))
    num = max(1, int(query.get("page", ["1"])[0]))
    total = (len(items) + per - 1) // per or 1
    chunk = items[(num - 1) * per: num * per]
    links = []
    if num < total:
        links.append(f'<{PUBLIC_URL}{base_path}?per_page={per}&page={num + 1}>; rel="next"')
        links.append(f'<{PUBLIC_URL}{base_path}?per_page={per}&page={total}>; rel="last"')
    return chunk, ", ".join(links)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        u = urlparse(self.path)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        entry = {"ts": time.time(), "method": self.command, "path": u.path, "query": parse_qs(u.query),
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "body": raw, "json": parsed}
        if not u.path.startswith("/_"):
            with LOCK:
                LOG.append(entry)
        return u, entry

    def _send(self, status, obj=None, text=None, ctype="application/json", headers=None):
        data = (json.dumps(obj) if obj is not None else (text or "")).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            if v:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _unauthorized(self, e):
        return not e["headers"].get("authorization")

    def do_GET(self):
        u, e = self._read()
        p = u.path
        if p == "/healthz":
            return self._send(200, {"ok": True})
        if p == "/_log":
            with LOCK:
                return self._send(200, list(LOG))
        if p == "/_state":
            with LOCK:
                return self._send(200, {"comments": list(STATE["comments"])})
        if self._unauthorized(e):
            return self._send(401, {"message": "Requires authentication"})
        m = re.match(r"^/repos/([^/]+)/([^/]+)/pulls/(\d+)/files$", p)
        if m:
            with LOCK:
                items, link = page(list(STATE["files"]), e["query"], p)
            return self._send(200, items, headers={"Link": link})
        m = re.match(r"^/repos/([^/]+)/([^/]+)/issues/(\d+)/comments$", p)
        if m:
            with LOCK:
                items, link = page(list(STATE["comments"]), e["query"], p)
            return self._send(200, items, headers={"Link": link})
        return self._send(404, {"message": "Not Found", "path": p})

    def do_POST(self):
        u, e = self._read()
        p = u.path
        j = e["json"] if isinstance(e["json"], dict) else {}
        if p == "/_reset":
            with LOCK:
                STATE["files"] = j.get("files", [])
                STATE["comments"] = j.get("comments", [])
                STATE["next_id"] = 1001
                LOG.clear()
            return self._send(200, {"ok": True})
        if p == "/v1/chat/completions":
            if not e["headers"].get("authorization", "").startswith("Bearer "):
                return self._send(401, {"error": {"message": "missing bearer token"}})
            status, text = llm_answer(prompt_text(j))
            if status:
                return self._send(status, {"error": {"message": "mock upstream failure", "type": "server_error"}})
            return self._send(200, {"id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
                                    "model": j.get("model", "mock"),
                                    "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": text}}],
                                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        if self._unauthorized(e):
            return self._send(401, {"message": "Requires authentication"})
        m = re.match(r"^/repos/([^/]+)/([^/]+)/issues/(\d+)/comments$", p)
        if m:
            with LOCK:
                cid = STATE["next_id"]
                STATE["next_id"] += 1
                c = {"id": cid, "body": j.get("body", ""), "user": {"login": "ftw-bot", "type": "Bot"},
                     "html_url": f"https://github.example/{m.group(1)}/{m.group(2)}/pull/{m.group(3)}#issuecomment-{cid}",
                     "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                STATE["comments"].append(c)
            return self._send(201, c)
        return self._send(404, {"message": "Not Found", "path": p})

    def do_PATCH(self):
        u, e = self._read()
        j = e["json"] if isinstance(e["json"], dict) else {}
        if self._unauthorized(e):
            return self._send(401, {"message": "Requires authentication"})
        m = re.match(r"^/repos/([^/]+)/([^/]+)/issues/comments/(\d+)$", u.path)
        if m:
            cid = int(m.group(3))
            with LOCK:
                for c in STATE["comments"]:
                    if c["id"] == cid:
                        c["body"] = j.get("body", "")
                        c["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        return self._send(200, c)
            return self._send(404, {"message": "Not Found"})
        return self._send(404, {"message": "Not Found", "path": u.path})


if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    print("mock listening on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

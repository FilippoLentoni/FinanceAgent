"""Local beta Cognito PKCE login. No passwords or tokens are printed."""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CALLBACK = "http://localhost:8765/callback"
REPO = Path(__file__).resolve().parents[1]


def validate_metadata(meta):
    match = re.fullmatch(r"https://cognito-idp\.([a-z0-9-]+)\.amazonaws\.com/([A-Za-z0-9_-]+)", meta["issuer"])
    if not match:
        raise ValueError("Invalid Cognito issuer")
    hosts = []
    for key, path in (("authorize_endpoint", "/oauth2/authorize"), ("token_endpoint", "/oauth2/token")):
        url = urllib.parse.urlsplit(meta[key])
        if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(f".auth.{match[1]}.amazoncognito.com")
                or url.username or url.password or url.port or url.path != path or url.query or url.fragment):
            raise ValueError("Invalid Cognito endpoint")
        hosts.append(url.hostname)
    if hosts[0] != hosts[1] or meta["public_client_id"] not in meta["allowed_clients"] or meta["scopes"]["user"] != "finplan-agent/invoke":
        raise ValueError("Invalid public client metadata")
    return meta


def authorize(meta):
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urllib.parse.urlencode({"response_type": "code", "client_id": meta["public_client_id"], "redirect_uri": CALLBACK,
        "scope": "openid email profile " + meta["scopes"]["user"], "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return meta["authorize_endpoint"] + "?" + query, state, verifier


def callback_code(path, expected_state):
    url = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    if url.path != "/callback" or url.scheme or url.netloc or url.fragment:
        raise ValueError("Invalid callback path")
    if len(query.get("state", [])) != 1 or not query["state"][0].isascii() or not secrets.compare_digest(query["state"][0], expected_state):
        raise ValueError("Invalid callback state")
    if "error" in query:
        raise ValueError("Sign-in was declined or failed")
    if set(query) != {"code", "state"} or len(query["code"]) != 1 or not query["code"][0]:
        raise ValueError("Invalid authorization response")
    return query["code"][0]


def wait_for_code(server, result, timeout=300):
    deadline = time.monotonic() + min(timeout, 300)
    while not result and time.monotonic() < deadline:
        server.timeout = max(0.01, min(1, deadline - time.monotonic()))
        server.handle_request()
    if "code" not in result:
        raise ValueError("Sign-in failed or timed out")
    return result["code"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange(meta, code, verifier, opener=None):
    validate_metadata(meta)
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": meta["public_client_id"],
        "redirect_uri": CALLBACK, "code": code, "code_verifier": verifier}).encode()
    request = urllib.request.Request(meta["token_endpoint"], data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with (opener or urllib.request.build_opener(NoRedirect)).open(request, timeout=30) as response:
            token = json.loads(response.read(65537))
        expiry = token.get("expires_in")
        if (token.get("token_type", "").lower() != "bearer" or not isinstance(token.get("access_token"), str)
                or not token["access_token"] or type(expiry) is not int or not 0 < expiry <= 86400):
            raise ValueError("Invalid token response")
        return {**token, "expires_at": int(time.time()) + expiry}
    except Exception:
        raise ValueError("Token exchange failed; sign in again") from None


def save_token(token, destination):
    path = Path(destination).expanduser().resolve()
    if path == REPO or REPO in path.parents or any((parent / ".git").exists() for parent in path.parents):
        raise ValueError("Token files must be outside the repository")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(token, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["beta"], default="beta")
    parser.add_argument("--metadata-file", type=Path, help="Use previously exported public sign-in metadata instead of AWS CLI discovery")
    parser.add_argument("--output", type=Path, default=Path.home() / ".finplan/beta-token.json")
    args = parser.parse_args()
    try:
        raw = args.metadata_file.read_text() if args.metadata_file else subprocess.check_output([
            "aws", "ssm", "get-parameter", "--region", "us-east-2", "--name",
            "/finplan/beta/financeagent/agent/authorizer-metadata-ref", "--query", "Parameter.Value", "--output", "text"], stderr=subprocess.DEVNULL, timeout=30).decode()
        meta = validate_metadata(json.loads(raw))
        url, state, verifier = authorize(meta)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback URLs contain authorization codes: never log them.

            def do_GET(self):
                try:
                    result["code"] = callback_code(self.path, state)
                    status, message = 200, b"Sign-in complete. You may close this tab."
                except ValueError:
                    result["error"] = True
                    status, message = 400, b"Sign-in failed. Restart the local login command."
                self.send_response(status)
                self.end_headers()
                self.wfile.write(message)

        with HTTPServer(("127.0.0.1", 8765), Handler) as server:
            if not webbrowser.open(url):
                raise ValueError("Could not open the local browser")
            print("Complete beta sign-in in your browser within five minutes.")
            code = wait_for_code(server, result)
        path = save_token(exchange(meta, code, verifier), args.output)
        print(f"Saved short-lived credentials to {path}. Follow docs/beta-access.md to load the access token.")
        return 0
    except Exception:
        print("Beta login failed. Check AWS metadata access, your beta account, the local browser and callback port; then retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline checks for the human beta PKCE handoff; never start a browser or contact AWS."""

import base64
import hashlib
import io
import json
import stat
import urllib.parse

import pytest

from scripts import beta_login as login


@pytest.fixture
def metadata():
    base = "https://finplan-beta.auth.us-east-2.amazoncognito.com/oauth2/"
    return {"issuer": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_TEST", "authorize_endpoint": base + "authorize",
            "token_endpoint": base + "token", "public_client_id": "public", "allowed_clients": ["public", "machine"],
            "scopes": {"user": "finplan-agent/invoke"}}


def test_authorization_generates_unique_state_s256_and_user_scope(metadata):
    url, state, verifier = login.authorize(login.validate_metadata(metadata))
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert params["redirect_uri"] == ["http://localhost:8765/callback"]
    assert params["scope"] == ["openid email profile finplan-agent/invoke"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()]
    assert len(verifier) >= 43 and verifier not in url and state != login.authorize(metadata)[1]


@pytest.mark.parametrize("path", ["/callback?code=SECRET&state=wrong", "/callback?code=SECRET&state=right&state=right",
    "/evil?code=SECRET&state=right", "http://evil/callback?code=SECRET&state=right", "/callback?error=SECRET&state=right",
    "/callback?code=SECRET&state=%E2%98%83"])
def test_callback_rejects_state_path_and_error_without_exposing_response(path):
    with pytest.raises(ValueError) as error:
        login.callback_code(path, "right")
    assert "SECRET" not in str(error.value) and path not in str(error.value)
    assert login.callback_code("/callback?code=ok&state=right", "right") == "ok"


def test_wait_expires_without_network():
    class Server:
        def handle_request(self):
            pytest.fail("No request should be handled after timeout")
    with pytest.raises(ValueError, match="timed out"):
        login.wait_for_code(Server(), {}, timeout=0)


@pytest.mark.parametrize("endpoint", ["http://bad.example/oauth2/token", "https://evil.example/oauth2/token",
    "https://user:secret@finplan-beta.auth.us-east-2.amazoncognito.com/oauth2/token",
    "https://finplan-beta.auth.us-east-2.amazoncognito.com/oauth2/token?code=SECRET"])
def test_untrusted_token_endpoints_rejected(metadata, endpoint):
    metadata["token_endpoint"] = endpoint
    with pytest.raises(ValueError):
        login.validate_metadata(metadata)


def test_exchange_sends_verifier_and_rejects_expired_or_error_tokens(metadata):
    class Opener:
        token = {"access_token": "secret-token", "token_type": "Bearer", "expires_in": 3600}
        def open(self, request, timeout):
            body = urllib.parse.parse_qs(request.data.decode())
            assert body["code_verifier"] == ["verifier"] and body["client_id"] == ["public"]
            assert "client_secret" not in body and timeout == 30
            return io.BytesIO(json.dumps(self.token).encode())
    opener = Opener()
    token = login.exchange(metadata, "code", "verifier", opener)
    assert token["expires_at"] > login.time.time()
    for invalid in ({"error": "SECRET error URL"}, {**opener.token, "expires_in": 0}):
        opener.token = invalid
        with pytest.raises(ValueError, match="^Token exchange failed; sign in again$"):
            login.exchange(metadata, "code", "verifier", opener)


def test_token_file_atomic_private_and_outside_repository(tmp_path):
    path = tmp_path / "credentials/token.json"
    path.parent.mkdir()
    path.write_text("old")
    path.chmod(0o644)
    login.save_token({"access_token": "secret"}, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"access_token": "secret"}
    assert list(path.parent.iterdir()) == [path]
    with pytest.raises(ValueError, match="outside"):
        login.save_token({}, login.REPO / "token.json")


@pytest.mark.parametrize("worktree", [False, True])
def test_token_file_cannot_be_saved_in_other_git_repositories(tmp_path, worktree):
    sibling = tmp_path / "AnotherRepo"
    sibling.mkdir()
    marker = sibling / ".git"
    marker.write_text("gitdir: /some/worktree") if worktree else marker.mkdir()
    destination = sibling / "nested/token.json"
    with pytest.raises(ValueError, match="outside"):
        login.save_token({"access_token": "secret"}, destination)
    assert not destination.exists() and not destination.parent.exists()


def test_cli_error_never_prints_sensitive_browser_url(metadata, tmp_path, monkeypatch, capsys):
    source = tmp_path / "metadata.json"
    source.write_text(json.dumps(metadata))
    monkeypatch.setattr(login.argparse.ArgumentParser, "parse_args", lambda _: login.argparse.Namespace(metadata_file=source, output=tmp_path / "token.json", env="beta"))
    monkeypatch.setattr(login, "HTTPServer", lambda *a: (_ for _ in ()).throw(ValueError("https://error.example/?code=SECRET")))
    assert login.main() == 1
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err and "error.example" not in output.out + output.err


def test_token_exchange_does_not_follow_redirects():
    assert login.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.example") is None

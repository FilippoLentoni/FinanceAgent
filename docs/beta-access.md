# Connecting to the beta agent and MCP Gateway

The hosted agent and direct MCP clients use the same beta Cognito identity and Gateway policy.
The hosted agent is an authenticated HTTP API; there is no separate chat website in this release.
Direct MCP lets Codex or Claude Code call the tools using its own model, whereas the hosted API
runs the deployed LangGraph agent with its packaged skills and configured Bedrock model.

## Identity and endpoint discovery

An administrator creates a beta Cognito user and assigns `viewer` for recommendations and evidence
reads. Sign in using the public authorization-code/PKCE client in the beta identity stack. Self
sign-up is disabled. The registered callback is `http://localhost:8765/callback`; it must match the
client's callback exactly. The pipeline's `ci_test` credentials are for verification, not user login.
Interactive browser sign-in and client onboarding remain unverified until a human account is set up.

As checked on 2026-10-09, the beta pool contains **zero human users**, and this verification host's
Codex MCP configuration is empty. The deployed machine-principal checks do not complete that
onboarding. The remaining steps are: create the requested human account, assign `viewer`, complete
first sign-in, obtain an access token, and verify a recommendation from the user's chosen client.
No invitation or user account has been created by this release.

The public client has no secret. After an administrator creates your account, run this command on
your local machine with a browser, Python 3 and AWS CLI permission to read the public beta metadata:

```bash
python3 scripts/beta_login.py --env beta
```

The helper opens Cognito sign-in, validates OAuth `state` and an S256 PKCE exchange, and listens only
on `127.0.0.1:8765` for the registered `localhost` callback. It waits at most five minutes and writes
credentials atomically to `~/.finplan/beta-token.json` with mode `0600`, outside the repository. It
does not print tokens, codes or verifiers. An administrator can provide the same public metadata as
a JSON file to a machine without AWS access: add `--metadata-file /path/to/beta-metadata.json`.
The helper is covered by offline tests; human browser sign-in remains unverified until onboarding.
Native `codex mcp login` / Claude OAuth discovery against this Gateway has not been verified.

With AWS read access, discover the current endpoints and sign-in metadata:

```bash
aws ssm get-parameter --region us-east-2 \
  --name /finplan/beta/financeagent/agent/authorizer-metadata-ref \
  --query Parameter.Value --output text

export FINPLAN_BETA_MCP_URL="$(aws ssm get-parameter --region us-east-2 \
  --name /finplan/beta/financeagent/agent/gateway-endpoint-ref \
  --query Parameter.Value --output text)"
```

Use a short-lived beta **access token** from that sign-in as `FINPLAN_BETA_ACCESS_TOKEN` in the
client process environment. Current beta access tokens expire after 60 minutes; obtain a fresh token
and restart the client when needed. Use the access token, rather than the sign-in ID token.
Do not store a token in a repository or paste it into a chat.

Load the saved access token into the shell that will start the client:

```bash
export FINPLAN_BETA_ACCESS_TOKEN="$(python3 -c 'import json,pathlib; print(json.loads((pathlib.Path.home()/".finplan/beta-token.json").read_text())["access_token"])')"
```

Run the login command again after token expiry; this initial helper does not refresh tokens in a
running Codex or Claude process.

## Codex

The installed Codex CLI supports this bearer-token configuration:

```bash
codex mcp add finplan_beta --url "$FINPLAN_BETA_MCP_URL" \
  --bearer-token-env-var FINPLAN_BETA_ACCESS_TOKEN
```

Set `tool_timeout_sec = 330` in the existing `[mcp_servers.finplan_beta]` table in the client's
`config.toml`. Start Codex with the token environment variable available and use `/mcp` to inspect
the connection. The CLI/IDE configuration and bearer-token setting are documented in the
[official OpenAI MCP guide](https://developers.openai.com/codex/mcp).

## Claude Code

Configure an HTTP MCP server with the beta URL and an Authorization header read from the client
environment. For example, in the client's `.mcp.json` configuration:

```json
{
  "mcpServers": {
    "finplan_beta": {
      "type": "http",
      "url": "${FINPLAN_BETA_MCP_URL}",
      "timeout": 330000,
      "headers": {"Authorization": "Bearer ${FINPLAN_BETA_ACCESS_TOKEN}"}
    }
  }
}
```

Use `/mcp` to inspect the connection. Follow the installed client's
[official MCP setup documentation](https://code.claude.com/docs/en/mcp) for configuration scope,
environment expansion and call deadlines. Claude Code is not installed on the verification host;
its application connection has not been tested here. Neither client has been configured with a
human beta account by this release.

## Hosted LangGraph agent

Use the Runtime ARN from `/finplan/beta/financeagent/agent/runtime-ref` to construct the invocation
URL. Send a beta bearer token and a fresh session ID of
33–100 allowed characters. `{"action":"describe","stream":false}` reports the deployed release,
provider and skills. For recommendations, send the approved snapshot, completed-session date and
current portfolio state in `recommendation`, or supply those same values in a natural-language
`prompt`. Never infer actual holdings from the demonstration below.

After obtaining the token, this example makes the same authenticated HTTP request as the deployed
test client. It requires AWS permission only to discover the Runtime ARN; the agent call itself uses
the bearer token. The token is read inside Python and is not placed in the command arguments.

```bash
export FINPLAN_BETA_RUNTIME_ARN="$(aws ssm get-parameter --region us-east-2 \
  --name /finplan/beta/financeagent/agent/runtime-ref \
  --query Parameter.Value --output text)"

python3 - <<'PY'
import json, os, urllib.parse, urllib.request, uuid
arn = urllib.parse.quote(os.environ["FINPLAN_BETA_RUNTIME_ARN"], safe="")
url = f"https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/{arn}/invocations?qualifier=DEFAULT"
request = urllib.request.Request(url, method="POST",
    data=json.dumps({"action": "describe", "stream": False}).encode(),
    headers={"Authorization": "Bearer " + os.environ["FINPLAN_BETA_ACCESS_TOKEN"],
             "Content-Type": "application/json", "Accept": "application/json",
             "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "finplan_beta_" + uuid.uuid4().hex})
with urllib.request.urlopen(request, timeout=330) as response:
    print(response.read().decode())
PY
```

Replace the `data` JSON above with the recommendation body below to request an allocation.
See [agent-api.md](agent-api.md) for streaming, sessions, confirmations and response fields.

An example **hypothetical paper** request uses $10,000 in cash and the approved snapshot through
2026-10-08:

```json
{
  "recommendation": {
    "input_snapshot_id": "snap_01M4G3BWYX7WRADXCS33PEZH35",
    "as_of": "2026-10-08",
    "holdings": {
      "weights": [],
      "cash_weight": 1,
      "portfolio_value": 10000,
      "high_watermark": 10000
    }
  },
  "stream": false
}
```

For direct MCP, the tool is `recommend_portfolio` and its arguments are the inner recommendation
object. The Gateway may display a target prefix on the tool name; use the name returned by `/mcp`
tool discovery. A newer date requires an approved snapshot with corresponding completed-session coverage.
Recommendations are advisory/paper outputs and do not execute orders.

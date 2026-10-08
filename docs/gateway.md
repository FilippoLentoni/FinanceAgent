# Gateway, registration and tool policy (tasks 5.1-5.4, 5.7)

## One Gateway per environment

`finplan-<env>-financeagent-agent` declares one MCP `AWS::BedrockAgentCore::Gateway` named
`finplan-<env>-financeagent-gateway`:

- inbound `CUSTOM_JWT`: discovery URL and allowed clients (the PKCE client and the `ci_test` client)
  imported from the SAME environment's identity stack; a token from another environment's pool is
  rejected (FA-POL-07);
- service role `finplan-<env>-financeagent-gateway-service-role` (bootstrap-created; published at
  `/finplan/<env>/financeagent/agent/gateway-principal-ref` for the FinanceLambdasTool invoke grant);
- the policy engine `finplan_<env>_financeagent_tools` attached in `ENFORCE` mode;
- `MCP-Protocol-Version` from `config/<env>.json` `gateway.mcp_protocol_version`.

The hosted agent and direct MCP clients (Claude Code, Codex) use the same Gateway, authorizer and policy.
The Runtime role has no Lambda or execute-api permission (FA-POL-01).

## Registration from released references (FA-GW-02, FA-GW-03)

The `Resolve` action of each environment stage (before any deploy):

1. reads `/finplan/<env>/financelambdastool/release/manifest`; absent -> **dependency missing**, the stage
   stops, nothing is deployed; a served contract major other than FinanceAgent's pinned major ->
   **incompatible**, stage stops, previous targets unchanged;
2. reads the tool catalog named by the manifest's `tool-catalog` output (must be this environment's,
   validated against the pinned contract). The deployed FinanceLambdasTool stores a
   `tool-catalog-pointer` there instead of the catalog (the full catalog exceeds the SSM size limit):
   `s3_uri` (its pipeline store, `releases/<release_id>/tool-catalog/<env>.json`), `sha256`,
   `release_id` and the sorted tool names. The stage role and the Runtime role follow the pointer with a
   regional SigV4 S3 client and read-only `s3:GetObject` on exactly that environment's catalog objects;
   another account, environment, release, digest or tool list stops with **dependency missing**;
3. for every catalog tool, reads its `lambda_ref_parameter` (`/finplan/<env>/financelambdastool/lambda/<tool>-arn`):
   it must be an alias-qualified Lambda of `finplan-<env>-financelambdastool-*` in the same account.

The results are pipeline variables `TARGET_<TOOL>` (ARN or `none`), passed to the agent stack's
`Target<Tool>Arn` parameters. Each `AWS::BedrockAgentCore::GatewayTarget` (name = tool with dashes, since
target names allow no underscores; visible tool name `<tool-with-dashes>___<tool>`) exists only when its
parameter is not `none`, so a tool removed from the catalog disappears on the next deploy. The parameter
pattern itself refuses another environment's functions. The registered set is published at
`/finplan/<env>/financeagent/agent/gateway-targets` and in the ledger; a rollback re-registers the set
recorded for that release.

## Tool schemas (FA-GW-04)

`infra/stacks/tool_schemas.py` generates every target's input and output schema at synth time from the
pinned `finplan-contracts` package (`tools/<tool>-request|response`). The Gateway `SchemaDefinition`
carries only `type`, `description`, `properties`, `required`, `items`; `$ref`s are resolved and every
other constraint (`pattern`, `enum`, `const`, `format`, bounds, `oneOf`) is moved into the property
description. This is a documented relaxation: the tool Lambda remains the validator (an invalid
identifier is still rejected by the tool with `INVALID_IDENTIFIER`). No schema is copied into the repo.

## Tool policy (FA-POL-03, FA-POL-05, FA-POL-06)

`policy/tool-policy.yaml` (roles = Cognito groups; the `ci_test` machine client by its `finplan-agent/ci_test`
scope) is rendered to Cedar (`infra/stacks/tool_policy.py`): one `permit` per registered tool that some
role may call, plus a `forbid` per denied argument value (`mode=live` ...) for tools whose schema carries
that argument. Default deny; tools naming live execution, trading, orders, payments or wallets are never
permitted. The decision does not depend on the channel. The digest of the rendered set is published at
`/finplan/<env>/financeagent/agent/policy-digest` and as the stack output `PolicyDigest`; the gamma drift
test compares the deployed policies with it.

| Role | Tools |
|---|---|
| viewer, ci_test | read-only tools |
| researcher | read-only + `refresh_market_data`, `submit_experiment` |
| plan_editor | read-only + `create_override_version`, `validate_plan_version` |
| plan_publisher | read-only + `validate_plan_version`, `publish_plan_version` |

## Limits (FA-GW-06)

- Tool calls must finish within the Gateway invocation timeout; long work uses `submit_experiment` /
  `get_job_status` / `get_experiment_result`.
- The agent's MCP client refuses a response above `gateway.max_response_bytes` (256 KiB by default) with
  `DEPENDENCY_UNAVAILABLE` (`response_too_large`); tools return compact summaries with trusted references.
- Gateway quotas (100 targets, 1,000 tools per target, 6 MB tool payload) are far above the 12 tools.

## Caller identity to tools (FA-OQ-2, open)

The documented Lambda-target context carries no end-user identity. The interceptor Lambda that would add
the `core/v1/caller.json` block is NOT deployed: FinanceLambdasTool does not yet consume such a block
(its LT-OQ-1 treats every Gateway caller as `gateway:<env>`). Until both sides agree on the mechanism,
the per-user authorization is enforced at the Gateway by the policy engine above, which evaluates the
caller's own verified token.

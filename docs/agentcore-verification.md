# AgentCore verification record (task 1.1, FA-OQ-7)

Facts the FinanceAgent foundation code depends on, checked against the official documentation and the
installed SDK/CDK packages on **2026-10-08**. The service changes quickly: re-check before each
infrastructure change. Rows marked *carried* come from the design (read 2026-10-07) and were not
re-checked by the foundation work; their owner re-verifies them.

## Runtime (verified 2026-10-08)

| Fact | Status | Source |
|---|---|---|
| HTTP protocol: container on host `0.0.0.0`, port `8080`, **ARM64** image (A-1 confirmed) | confirmed | [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html) |
| `POST /invocations`: JSON in; JSON (`application/json`) or SSE (`text/event-stream`, `data: <json>` events) out | confirmed | same |
| `GET /ping`: `{"status": "Healthy" \| "HealthyBusy"}`; `time_of_last_update` only on a real status change (an always-advancing value prevents the idle timeout) | confirmed | same |
| `/ws` WebSocket on the same port, optional | confirmed; not registered in phase 1 (design D5) | same |
| Session ID header `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | confirmed | same, and the SDK (`bedrock_agentcore.runtime.app`) |
| Errors are native HTTP (`x-amzn-ErrorType`); an OAuth runtime returns 401 with `WWW-Authenticate: Bearer resource_metadata=...` when the token is missing; container 4xx/5xx surfaces as `424 RuntimeClientError` | confirmed | same |
| OAuth invocation URL `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<escaped ARN>/invocations?qualifier=...` | confirmed (from the 401 resource-metadata example) | same |
| The `Authorization` header reaches agent code only when the runtime has a `customJWTAuthorizer` AND `Authorization` is in `requestHeaderConfiguration.requestHeaderAllowlist` (at most 20 headers, 4 KB each; `x-amz-*` and most `x-amzn-*` restricted) | confirmed | [Pass custom headers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html) |
| `UpdateAgentRuntime` is a full PUT: omitted authorizer/allowlist fields are cleared | confirmed | same |
| SDK `BedrockAgentCoreApp` (bedrock-agentcore 1.23.x): routes `/invocations`, `/ping`, `/ws`; a generator result is streamed as SSE; `RequestContext.session_id` and `.request_headers` (Authorization normalised); `/ws` without a handler is closed | confirmed by reading the installed package | `bedrock_agentcore/runtime/app.py` |
| Quotas (15 min sync, 60 min streaming, 8 h async/max lifetime, 15 min idle default, 100 MB payload, 2 vCPU/8 GB, 2 GB image) | *carried* | design.md "Observed facts" |
| Session ID at least 33 characters | *carried*; the agent enforces 33-100 characters of `[A-Za-z0-9_-]` (the Memory `sessionId` pattern, checked in the installed boto3 model) | design.md; botocore `bedrock-agentcore` CreateEvent shape |

## Memory (verified 2026-10-08)

| Fact | Status | Source |
|---|---|---|
| LangGraph checkpointer `langgraph_checkpoint_aws.AgentCoreMemorySaver(memory_id, region_name=...)`; config keys `thread_id` (maps to the Memory session) and `actor_id`; needs `bedrock-agentcore:CreateEvent`, `ListEvents` (and `DeleteEvent` for deletion) | confirmed | [Integrate AgentCore Memory with LangGraph](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html); installed `langgraph-checkpoint-aws` 1.2.x (`delete_thread(thread_id, actor_id)`) |
| Event API shapes: `actorId` pattern `[a-zA-Z0-9][a-zA-Z0-9-_/]*...`, `sessionId` 1-100 chars `[a-zA-Z0-9][a-zA-Z0-9-_]*`, payload items `conversational` / `blob` (document) / `json`, at most 100 per event | confirmed | installed botocore service model |
| Event expiry 7-365 days; CreateEvent 5/s per actor per session | *carried* (task 4.5 load test still open) | design.md |
| Memory does not answer "which actor owns session X"; a caller with another actor ID sees an empty session | design consequence: the agent keeps an owner registry (first claim wins) to return `FORBIDDEN` | `agent/finplan_agent/session/store.py` |

## Gateway (MCP client side, verified 2026-10-08)

| Fact | Status | Source |
|---|---|---|
| Endpoint `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`; `POST` JSON-RPC 2.0; headers `Accept: application/json, text/event-stream`, `Authorization: Bearer <token>`, `MCP-Protocol-Version` (must be in the gateway's `protocolConfiguration.mcp.supportedVersions`; `2025-11-25` used; `2026-07-28` additionally needs `Mcp-Method`/`Mcp-Name` headers and `_meta`) | confirmed | [Call a tool](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-call.html) |
| Tool names are `<target>___<tool>` | confirmed | same |
| HTTP errors 401/403/404/400/500 | confirmed | same |
| Lambda target context, interceptor and policy-engine capabilities (A-2, A-3) | *carried*; owned by the Gateway work | design.md |

## Bedrock (verified 2026-10-08)

| Fact | Status | Source |
|---|---|---|
| Converse prompt caching: `cachePoint {"type": "default"}` in `system`, `messages`, `toolConfig.tools`; processed tools -> system -> messages; usage `cacheReadInputTokens` / `cacheWriteInputTokens`; with caching `inputTokens` excludes cached tokens | confirmed | [Prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html) |
| Claude Opus 5 (`anthropic.claude-opus-5`): explicit caching GA, minimum 512 tokens per checkpoint, at most 4 checkpoints, TTL 5 min or 1 h; cross-region inference profiles support caching | confirmed | same |

## CloudFormation / CDK coverage (A-5, verified 2026-10-08)

The installed `aws-cdk-lib` (2.270.0 in `uv.lock`) has L1 constructs in `aws_cdk.aws_bedrockagentcore`:
`CfnRuntime` (props include `authorizer_configuration` with `CustomJWTAuthorizerConfiguration`
`discovery_url` / `allowed_audience` / `allowed_clients` / `allowed_scopes` / `custom_claims`,
`request_header_configuration.request_header_allowlist`, `lifecycle_configuration`
`idle_runtime_session_timeout` / `max_lifetime`, `environment_variables`, `network_configuration`,
`protocol_configuration`), `CfnRuntimeEndpoint`, `CfnGateway` (incl. `interceptor_configurations`,
`policy_engine_configuration`), `CfnGatewayTarget`, `CfnMemory` (`event_expiry_duration`,
`memory_strategies`), `CfnPolicyEngine`, `CfnPolicy`, `CfnWorkloadIdentity`. No custom resource is
needed for these resource types. (`CfnHarness` also exists; it is NOT used: design D1.)

## Wiring the Runtime stack must provide (interface for the infrastructure work)

- Container image by digest (ARM64), `PUBLIC` network mode.
- `authorizer_configuration.custom_jwt_authorizer` from `/finplan/<env>/financeagent/agent/authorizer-metadata-ref` and `request_header_allowlist: ["Authorization"]`.
- Environment variables `FINPLAN_ENV`, `FINPLAN_RELEASE_ID`, `FINPLAN_MEMORY_ID`, `FINPLAN_GATEWAY_URL`.
- Runtime role: `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` on the configured profile and its routed foundation models only; `bedrock-agentcore:CreateEvent`/`ListEvents`/`DeleteEvent` (and the reads the saver needs) on the environment's Memory; `ssm:GetParameter(s)` on its own `config/explanation-*`, the environment's `financelambdastool/contract/tool-catalog` and the shared `financialplanning/config/budget-*` / `cost-ceiling-usd`; `cloudwatch:PutMetricData` (condition `cloudwatch:namespace` = `FinPlan/FinanceAgent`) and `cloudwatch:GetMetricData`; its own log group writes. **No** `lambda:InvokeFunction`.

## Infrastructure facts (verified 2026-10-08 for the infra/pipeline work)

| Fact | Status | Source |
|---|---|---|
| `AWS::BedrockAgentCore::Runtime`: `AgentRuntimeName` pattern `[a-zA-Z][a-zA-Z0-9_]{0,47}` (replacement on change), `AgentRuntimeArtifact.ContainerConfiguration.ContainerUri`, `RoleArn`, `NetworkConfiguration`, `ProtocolConfiguration` (`HTTP`), `AuthorizerConfiguration.CustomJWTAuthorizer`, `RequestHeaderConfiguration`, `LifecycleConfiguration`, `EnvironmentVariables`, map `Tags`; `Ref` = ARN; GetAtt `AgentRuntimeArn`, `AgentRuntimeId`, `AgentRuntimeVersion` | confirmed | [CFN Runtime](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html) |
| Runtime execution role: trust `bedrock-agentcore.amazonaws.com` with `aws:SourceAccount` / `aws:SourceArn`; ECR pull, logs under `/aws/bedrock-agentcore/runtimes/*`, X-Ray, `cloudwatch:PutMetricData` (namespace `bedrock-agentcore`), `GetWorkloadAccessToken*` on `workload-identity-directory/default[/workload-identity/<name>-*]`; docs recommend denying `GetWorkloadAccessTokenForUserId` when JWTs are available | confirmed (FinanceAgent grants `...ForJWT` only and denies `...ForUserId`) | [Runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) |
| Runtime log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-<endpoint>`; the stack declares it (30 days) and the role gets no `CreateLogGroup` | confirmed name; *assumption*: the service does not need `CreateLogGroup` when the group exists (first deploy verifies) | [View observability data](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-view.html) |
| `AWS::BedrockAgentCore::Gateway`: `AuthorizerType` `CUSTOM_JWT\|AWS_IAM\|NONE\|AUTHENTICATE_ONLY`, `Name` `^([0-9a-zA-Z][-]?){1,48}$`, `RoleArn`, `ProtocolConfiguration.Mcp.SupportedVersions`, `PolicyEngineConfiguration {Arn, Mode: LOG_ONLY\|ENFORCE}`, `InterceptorConfigurations` (1-2); GetAtt `GatewayUrl`, `GatewayArn`, `GatewayIdentifier` | confirmed | [CFN Gateway](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gateway.html) |
| `AWS::BedrockAgentCore::GatewayTarget`: `Name` `^([0-9a-zA-Z][-]?){1,100}$` (NO underscores), `GatewayIdentifier` (replacement), `TargetConfiguration.Mcp.Lambda {LambdaArn, ToolSchema.InlinePayload[]}`, `CredentialProviderConfigurations` (`GATEWAY_IAM_ROLE` for Lambda) | confirmed | [CFN GatewayTarget](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gatewaytarget.html) |
| Gateway service role: trust `bedrock-agentcore.amazonaws.com` (`aws:SourceArn` `gateway/<name>-*`), `lambda:InvokeFunction` on the target functions (same account: no resource policy required; FinanceLambdasTool adds one anyway) | confirmed | [Gateway permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-prerequisites-permissions.html) |
| Policy in AgentCore: the Gateway role needs `GetPolicyEngine`, `AuthorizeAction`, `PartiallyAuthorizeActions` (engine + gateway ARNs); policy CREATION validates against the gateway (`InvokeGateway`) and needs `ManageResourceScopedPolicy` (granted to the deploy execution role through `bedrock-agentcore:*` on its environment) | confirmed | [Policy permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html) |
| Cedar for JWT gateways: principal `AgentCore::OAuthUser` (id = `sub`), every claim a STRING tag (arrays as JSON text: match `cognito:groups` with `like`), action `AgentCore::Action::"<target>___<tool>"`, a policy naming actions must name the gateway ARN, default deny, `tools/list` filtered per caller | confirmed | [Principal-scoped policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/example-policies-principal.html), [Authoring guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-authoring-guide.html) |
| `AWS::BedrockAgentCore::PolicyEngine` / `Policy`: names `^[A-Za-z][A-Za-z0-9_]*$` (<= 48, account-unique, replacement), `Definition.Cedar.Statement`, `EnforcementMode ACTIVE\|LOG_ONLY`, `ValidationMode FAIL_ON_ANY_FINDINGS\|IGNORE_ALL_FINDINGS` | confirmed (FinanceAgent: `ACTIVE`, `IGNORE_ALL_FINDINGS`, one policy per tool, waits for its Gateway's registered target catalog) | [CFN Policy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-policy.html), [CFN PolicyEngine](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-policyengine.html) |
| `AWS::BedrockAgentCore::Memory`: `Name` `^[a-zA-Z][a-zA-Z0-9_]{0,47}$`, `EventExpiryDuration` 3-365 days (required), strategies optional | confirmed (design said 7-365; FinanceAgent keeps 7-365) | [CFN Memory](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-memory.html) |
| `GatewayUrl` ends in `/mcp` (the agent's settings require it) | *assumption*, checked by the first beta deploy (`describe` + `tools/list`) | Gateway call docs above |
| Lambda target context carries no end-user identity; interceptor injection mechanism (A-2) | still open (FA-OQ-2); interceptor not deployed | design D2 |

Gateway targets update a shared action catalog. Their CloudFormation updates run sequentially within each Gateway; the two Gateways can still progress independently. Every policy waits for all enabled targets in its own Gateway through conditional target-ID references in a Cedar comment. This changes deployment ordering without changing the permission statements or their semantic digest. Target descriptions carry the preceding enabled target reference. Conditional references preserve partial catalogs: an unconditional `DependsOn` chain would suppress later resources when an optional target is absent. [CloudFormation conditions](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-condition.html), [conditional resource references](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-conditions.html).

Schema checks always run during policy creation, including with `IGNORE_ALL_FINDINGS`; that setting controls semantic findings rather than allowing unknown actions. The deployment barrier addresses policies starting while parallel target updates are changing the manifest. Post-deployment publication independently checks that both Gateways and every intended target are `READY`, with the exact released schemas and Lambda aliases. [Policy validation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-validate-policies.html).

## Verify pass (2026-10-08, read-only checks of the deployed neighbours)

| Fact | Status | Source |
|---|---|---|
| The ARM64 CodeBuild image `amazonlinux-aarch64-standard:3.0` with `runtime-versions` python 3.12 / nodejs 22 builds successfully | confirmed (the FinancialPlanning build project's latest build on that image SUCCEEDED, `ARM_CONTAINER`) | `codebuild batch-get-builds`, read-only |
| The Runtime dependency closure resolves to binary wheels for `aarch64-manylinux_2_28` / CPython 3.12 (no source build in the image) | confirmed (`uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --only-binary :all:` over `uv export --no-dev`) | local, offline resolution |
| FinanceLambdasTool beta and gamma releases exist (contract 1.0.0, served major 1, 12 tools, alias-qualified `:current` Lambda references); prod has none yet | confirmed | SSM `release/manifest`, read-only |
| The deployed `/finplan/<env>/financelambdastool/contract/tool-catalog` is a `tool-catalog-pointer` (`s3_uri` in the FinanceLambdasTool pipeline store, SSE-S3, `sha256`, `release_id`, tool names), not the catalog: the full catalog exceeds the SSM limit | confirmed; FinanceAgent follows the pointer in `Resolve` and in the Runtime (`finplan_agent.tools.catalog.resolve_catalog_value`), with `s3:GetObject` on `releases/*/tool-catalog/<env>.json` only | SSM + `s3api get-bucket-encryption`, read-only |
| The environment permission boundary lets `<env>` roles read `shared`-tagged resources (the FinanceLambdasTool store) | confirmed (`DenyOtherEnvironmentTagged` exempts `shared`) | IAM `get-policy-version`, read-only |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` is ACTIVE and routes to us-east-1, us-east-2, us-west-2 | confirmed | `bedrock get-inference-profile`, read-only |
| `us.anthropic.claude-opus-5` profile is ACTIVE, but invocation is "not available for this account" (2026-10-08) | profile listed; access not granted (do not switch `model_id` until a capped gamma smoke succeeds) | `bedrock get-inference-profile`, read-only; operator report |

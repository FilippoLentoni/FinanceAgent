# Design

## Context

See proposal.md (Why). The requirements are in `specs/` (agent-runtime-hosting, agent-session-persistence, tool-gateway, mcp-access-policy, explanation-provider, agent-skills, agent-client-integration, agent-release-pipeline). Cross-repo rules come from FinancialPlanning change `establish-cross-repo-contracts`: ownership matrix D1, identifiers D2, contract package D3 (`finplan-contracts`), SSM/manifest convention D4, isolation D5, pipeline standard D6 and phases D7. This design references those rules and does not redefine them.

### Observed facts

From read-only AWS discovery, 2026-10-07:
- Account resources are in us-east-2. The AgentCore control plane responds there. No AgentCore runtimes or gateways exist, and there are no ECR repositories.
- Secrets Manager holds **no OpenAI secret**. None is needed: the explanation provider is Amazon Bedrock with IAM auth (D4).
- The CLI caller during discovery was root. RESOLVED 2026-10-07 (contracts OQ-11): the one-time bootstrap uses the user's existing authenticated AWS CLI session. No pre-existing scoped human role is required and there is no refuse-root rule. The bootstrap still creates the scoped pipeline, deploy and service roles. Recommendation (not a blocker): move the human operator to a scoped/MFA role later.
- Single account (contracts OQ-1 resolved 2026-10-07): beta, gamma and prod share one account in us-east-2, isolated by naming, tags, permission boundaries and environment-tag denies, with separate per-environment resources. Multi-account is only a possible future migration.
- Read-only `aws bedrock list-foundation-models` and `aws bedrock list-inference-profiles` in us-east-2 (2026-10-07): many text models are ACTIVE, and most current Anthropic, Amazon Nova, Meta and OpenAI-on-Bedrock models are invocable there only through a cross-region inference profile (`us.` or `global.` prefix). The inference profile `us.anthropic.claude-opus-5` (foundation model `anthropic.claude-opus-5`) is listed ACTIVE in us-east-2 by `list-inference-profiles` (2026-10-07) and is the chosen explanation model (D4). Whether model access is enabled for it was not checked; enabling it is a bootstrap step (D4).

From official AgentCore documentation (docs.aws.amazon.com/bedrock-agentcore), read 2026-10-07. Recheck at implementation, because the service changes quickly:
- **Region table:** US East (Ohio) lists Runtime (microVMs), Memory, Gateway, Identity, Built-in Tools, Observability, Policy and Evaluations as available. The Web Search Tool and AWS Agent Registry are **not** listed for Ohio. This design uses neither.
- **Runtime quotas:**
  - Synchronous request timeout 15 min.
  - Streaming connection (response streaming and WebSocket) at most 60 min.
  - Asynchronous job at most 8 h.
  - Payload 100 MB; streaming chunk 10 MB; WebSocket frame 64 KB at 250 frames/s.
  - Idle session timeout 15 min by default (adjustable through `idleRuntimeSessionTimeout`). Maximum session lifetime 8 h by default (adjustable through `maxLifetime`).
  - 2 vCPU / 8 GB per session; Docker image at most 2 GB; direct code package at most 250 MB compressed.
  - Session storage 1 GB.
  - 2,500 active sessions per account outside us-east-1/us-west-2.
  - New sessions 25 TPS.
  - Session ID at least 33 characters (stated for `InvokeAgentRuntimeCommand`; assumed to apply generally, to be verified).
- **Runtime WebSocket:** the container serves `/ws` on port 8080 next to HTTP `/invocations`. Authentication is SigV4 or OAuth 2.0.
- **Gateway:**
  - Inbound authorization types: JWT (`CUSTOM_JWT`, any OIDC provider), IAM (SigV4), and offloaded types (`AUTHENTICATE_ONLY`, `NONE`).
  - A **policy engine** (Policy in AgentCore) can be attached to a gateway, and so can an **interceptor Lambda**.
  - The docs recommend on-behalf-of (OBO) token exchange over token passthrough for production.
  - Quotas: 100 targets per gateway; 1,000 tools per target; invocation timeout 15 min; tool-call payload 6 MB; inline schema 1 MB; tool name at most 256 characters; 200 tool-call TPS.
- **Gateway Lambda targets:**
  - The event is the tool's input properties.
  - `context.client_context.custom` carries `bedrockAgentCoreGatewayId`, `bedrockAgentCoreTargetId`, `bedrockAgentCoreToolName` (format `<target>___<tool>`, prefix to be stripped), `bedrockAgentCoreAwsRequestId`, `bedrockAgentCoreMcpMessageId` and `bedrockAgentCoreMessageVersion`.
  - **The documented context carries no end-user identity.**
  - The tool schema format (`SchemaDefinition`) supports type, description, properties, required and items only.
- **Memory:**
  - Event expiration is 7–365 days.
  - Up to 150 Memory resources per Region.
  - CreateEvent allows at most 100 messages, 100 KB per message and 10 MB per event, and is rate-limited to 5 per second per actor per session.
  - The AWS docs describe a LangGraph integration (`langgraph-checkpoint-aws`, `AgentCoreMemorySaver` checkpointer keyed by `actor_id` and `thread_id`).
- **Out of scope:** an "AgentCore harness" (managed agent) and "AgentCore payments" both exist in Ohio. This design **uses neither**. Payments are out of scope, and the harness would replace the code-based LangGraph agent.

### Assumptions (unverified; each has a verification task)

- A-1. The Runtime accepts a container image built for its required CPU architecture. The architecture requirement (believed to be arm64) is confirmed before the first image build.
- A-2. A Gateway interceptor Lambda can add verified caller claims to the request delivered to a Lambda target, or the policy engine can pass them on. If neither works, see FA-OQ-2.
- A-3. The policy engine can condition on caller claims, tool name and argument values (for example, deny `mode = live`). If it cannot, argument-level denials move to the interceptor, and tools enforce them as well.
- A-4. Claude Code and Codex can authenticate to a JWT-protected remote MCP server using the OAuth flow the Gateway advertises (the RFC 9728 protected-resource metadata is documented). If not, a documented local signing or token helper is used.
- A-5. CloudFormation (through CDK, L1 constructs if no L2 exists) supports AgentCore Runtime, Gateway, GatewayTarget, Memory and policy resources. Where it does not, a pipeline-run custom resource calls the control-plane APIs. Contracts assumption A3 (CDK) is kept.

## Goals / Non-Goals

**Goals:**
- One tool path (Gateway) and one policy for every client. The hosted agent gains no privilege that a direct MCP client lacks.
- A phase 1 deployment that is fully fixture-backed (fixture provider, synthetic test portfolio; platform market data may be real phase 2 data in any environment, decision 26, 2026-10-07). It can be promoted beta → gamma → prod with near-zero standing cost and runs deployed MCP invocation tests in each environment.
- A pluggable provider interface that changes from the fixture provider to Amazon Bedrock by configuration only, once model access for the chosen model (Claude Opus 5, D4) is enabled at bootstrap and verified.

**Non-Goals:**
- The explanation workflows' evidence and narratives (change `add-explanation-workflows`).
- Owning any platform or model data, budgets or approvals.
- WebSocket and voice features, long-term memory, A/B testing or AgentCore Evaluations as gates.
- Live trading, Coinbase, AgentCore payments, wallet spending and risk-preference rewriting.

## Decisions

### D1. LangGraph graph in AgentCore Runtime, delivered as a container image

- The graph is code in this repo (Python). It is wrapped by the Runtime HTTP contract (`/invocations`, plus `/ping` health). A `/ws` handler exists in code only behind a disabled flag.
- **Container image over direct code deployment.** An image digest is a natural immutable promotion unit (contracts D6). It also allows the pinned contract wheel and the skill bundles to be baked in. Direct code deployment (ZIP of at most 250 MB) was the alternative. It avoids an ECR repo but makes digest-pinned promotion and native dependencies harder. FinanceAgent owns its ECR repository (one, shared across environments, with immutable tags). This must be added to the ownership matrix: **CONTRACT GAP-4**.
- **Rejected:** the AgentCore harness (a managed agent, which violates the LangGraph requirement), Strands or other frameworks, and Lambda-hosted agents (15-minute cap, no session microVM).
- The graph has these nodes: route → plan (skill selection) → tool-call (MCP client to the Gateway) → confirm (LangGraph interrupt for state-changing tools) → narrate (provider) → claim-check (deterministic) → respond. The tool catalog's `state_changing` flag drives the confirm node.

### D2. Gateway: one per environment, JWT inbound, policy engine plus interceptor

- **Inbound authorization: `CUSTOM_JWT` against an OIDC identity provider.** JWT gives one identity for the hosted agent's users and for Claude Code/Codex users. IAM (SigV4) inbound would give direct clients AWS principals and the agent its role principal. That would make identical per-user policy harder, and every human MCP user would need AWS credentials. `NONE` and `AUTHENTICATE_ONLY` are rejected for these gateways.
- **Identity provider: FinanceAgent owns one Amazon Cognito user pool per environment** (RESOLVED 2026-10-07: FA-OQ-1, CONTRACT GAP-1; user decision item 15a). It is the only inbound identity provider for the Gateway and the Runtime.
  - One pool each for beta, gamma and prod, created by the FinanceAgent per-environment identity stack through the pipeline (never by hand), tagged with the environment and owning repository. A token issued by one environment's pool is never accepted by another environment's Gateway or Runtime.
  - Roles are Cognito groups: `viewer`, `researcher`, `plan_editor`, `plan_publisher`, `ci_test`. The Gateway policy maps groups to tools (D2 Policy).
  - Self sign-up is disabled. Users are created by an administrator only; in phase 1 the only human user is the project owner. Users and their attributes are never written to repository files.
  - App clients: one public authorization-code client (PKCE) for the hosted agent, CLI and direct MCP clients (Claude Code, Codex), and one `ci_test` client-credentials client per environment whose secret lives in Secrets Manager and is referenced through `/finplan/<env>/financeagent/secret-ref/ci-test-client` (name only).
  - References are SSM only, under the FinanceAgent `agent/` namespace: `/finplan/<env>/financeagent/agent/user-pool-ref` (pool identifier reference) and `/finplan/<env>/financeagent/agent/authorizer-metadata-ref` (OIDC discovery URL, issuer and allowed audiences/clients). The Gateway `CUSTOM_JWT` authorizer and the Runtime JWT inbound configuration are synthesized from these parameters for the same environment. Pool IDs, client IDs, issuer URLs containing them, and ARNs never appear in repository files.
  - The website, if separate, must reuse this pool (contracts OQ-10); it does not create its own.
  - The contracts ownership matrix row (contracts D1) still says "provisional"; it must be updated to final in the FinancialPlanning contracts change.
- **Hosted agent to Gateway:** the Runtime uses JWT inbound with the same identity provider. The agent calls the Gateway with a token for the same user, through AgentCore Identity on-behalf-of exchange if the Gateway supports it (A-2/A-4 verification). The fallback is to forward the user token restricted to the Gateway audience. The agent never uses a service token for tool calls. This satisfies "Agent acts as the user".
- **Policy:** a policy engine attached to each Gateway, with the policy source in `policy/` and its digest recorded in the manifest. The engine evaluates (role, tool, selected argument values). The same engine and policy version serve all channels, so parity holds by construction. The policy-parity test still verifies it.
- **Interceptor Lambda (owned by FinanceAgent):**
  - Adds verified caller claims (`sub`, roles, channel `hosted_agent|direct_mcp|ci_test`, `correlation_id`) to the target request, because the documented Lambda context has no user identity.
  - Rejects body fields that try to set identity.
  - The exact payload field is a **CONTRACT GAP-2**: an on-behalf-of caller block in the tool request envelope. FinanceLambdasTool recorded the same gap.
- **Gateway service role:** one per environment, allowed `lambda:InvokeFunction` only on that environment's tool Lambda references. Its reference is published as `/finplan/<env>/financeagent/agent/gateway-principal-ref`, and FinanceLambdasTool grants invoke permission to it. **Ordering conflict (CONTRACT GAP-3):** FinanceLambdasTool releases before FinanceAgent, but it needs this principal for its invoke grant. Resolution proposed: the FinanceAgent bootstrap creates the per-environment Gateway service role and writes its reference before FinanceLambdasTool's first Gateway-facing release. Until then, FinanceLambdasTool allows direct test principals only.
- **Registration:**
  - Each tool is one Gateway target (name = tool name, so the visible name is `<tool>___<tool>`). The alternative is one target per Lambda if FinanceLambdasTool bundles tools. The catalog decides.
  - Schemas are generated from the pinned `finplan-contracts` schemas and projected onto the Gateway `SchemaDefinition` subset. Patterns, enums and `oneOf` that cannot be expressed are moved into descriptions, and the Lambda remains the validator.
  - The SSM name of the tool catalog is taken from the FinanceLambdasTool manifest `outputs` key `tool-catalog`. **CONTRACT GAP-5:** the key and its schema need to be defined in the contract package.
- **Rejected:** API Gateway or a self-hosted MCP server in front of the Lambdas. Either would add a second policy point and a second identity path.

### D3. Sessions and checkpoints in AgentCore Memory (short-term only)

- Checkpointer: `AgentCoreMemorySaver` from `langgraph-checkpoint-aws` with `thread_id` = Runtime `session_id` and `actor_id` = salted hash of the IdP `sub` (avoids PII in Memory and in logs). There is one Memory resource per environment, owned by FinanceAgent, with event expiry from configuration (default 30 days, within the 7–365 range).
- **Alternatives:** a DynamoDB table checkpointer (more control, one more resource to own), or Runtime session storage (1 GB, lost when the session ends). Memory was chosen because it is managed, consumption-billed and documented with LangGraph. If the 5 CreateEvent/s per actor per session limit proves too tight for step-level checkpoints, the fallback is a DynamoDB checkpointer owned by FinanceAgent. A load test task covers this.
- No long-term memory strategies in phase 1. This avoids extraction token cost and prevents long-term memory from becoming an implicit store of risk preferences.
- Caller isolation: the agent checks that the checkpoint's `actor_id` equals the caller's before any read. A mismatch returns `FORBIDDEN`.

### D4. Explanation provider: Amazon Bedrock by default, pluggable interface

RESOLVED 2026-10-07 (contracts OQ-3, D11): the default explanation provider is **Amazon Bedrock** with IAM authentication. There is no API key and no secret.

- **Model (RESOLVED 2026-10-07, contracts OQ-13 / FA-OQ-4).** The explanation model is **Claude Opus 5** through the US cross-region inference profile **`us.anthropic.claude-opus-5`** (foundation model `anthropic.claude-opus-5`), verified ACTIVE in us-east-2 by a read-only `aws bedrock list-inference-profiles` call on 2026-10-07.
  - The `us.` profile is used, not `global.`, so inference stays in US regions. Configuration validation rejects `global.`-prefixed identifiers.
  - The identifier is configuration only: prod holds `us.anthropic.claude-opus-5` in `/finplan/prod/financeagent/config/explanation-model-id`. Code, tests and skill bundles never contain a model identifier; a build-stage scan fails on any literal Bedrock model or profile identifier outside configuration fixtures.
  - Beta and gamma may set a cheaper Bedrock model ID through the same key (for example to keep the gamma smoke inexpensive). The same validation, IAM derivation and cost controls apply to whatever ID is configured.
- **Configuration** (owned by FinanceAgent, per environment, contracts D4/D11):
  - `/finplan/<env>/financeagent/config/explanation-provider`: `{kind: bedrock|fixture, max_tokens_invocation, max_tokens_turn, max_tokens_session, max_tool_calls_turn, max_cost_session_usd, temperature, prompt_caching: enabled|disabled, rates: {input_per_1k_usd, output_per_1k_usd, cache_write_per_1k_usd?, cache_read_per_1k_usd?, source: configured|aws_price_list, retrieved_at}}`. Validation rejects any other `kind`. `max_tokens_invocation` is sent as the Bedrock `maxTokens` inference parameter on every call and must not exceed `max_tokens_turn`.
  - Rates are never hard-coded and no prices are recorded in this design. They come either from configured values entered from current AWS pricing (`source: configured`) or from the AWS Price List API looked up at run time by the configuration step that writes this parameter (`source: aws_price_list`); in both cases `retrieved_at` is stored. A `bedrock` configuration without input and output rates fails validation. When `prompt_caching` is enabled, cache rates must be present, otherwise caching stays disabled.
  - `/finplan/<env>/financeagent/config/explanation-model-id`: the Bedrock model ID or inference-profile ID. Validation rejects identifiers matching the denied patterns (any `qwen` identifier, FinanceModel references and `global.` profiles).
- **Interface.** `ExplanationProvider` exposes one generate call (messages, limits) returning text, token usage (including cache read/write tokens) and a usage record, plus a typed error mapping (`DEPENDENCY_UNAVAILABLE`, `BUDGET_EXCEEDED`). Adapters: `bedrock` (Bedrock Runtime Converse / ConverseStream API through the AWS SDK with the Runtime role) and `fixture` (deterministic templates). An **OpenAI adapter is an optional future addition**. Adding it would need its own adapter code, a provider-kind amendment and a credential reference in a later change. No OpenAI secret is created now, and the retired key `/finplan/<env>/financeagent/secret-ref/openai-api-key` is not written.
- **IAM.** The Runtime role gets `bedrock:InvokeModel` and, because HTTP streaming uses ConverseStream, `bedrock:InvokeModelWithResponseStream`. Nothing else. Resources (written generically; real ARNs are resolved at synth time and never appear in repo files):
  - the inference profile: `arn:aws:bedrock:<region>:<account>:inference-profile/us.anthropic.claude-opus-5` (region us-east-2);
  - each regional foundation model the US profile routes to: `arn:aws:bedrock:<us-region>::foundation-model/anthropic.claude-opus-5`, one per US region listed in the profile's `models` (read-only `get-inference-profile` at synth time).
  The CDK stack derives both from the per-environment model-id parameter, so a model change (including a cheaper beta/gamma model) is a config-only release that re-scopes the grant. No other Bedrock actions (no model-access management, no agents, no knowledge bases). Model-access management stays with the bootstrap caller, not the Runtime role.
- **Model access enablement (bootstrap step).** Bedrock model access is account-level (contracts D1 row "Amazon Bedrock foundation-model access", owner external). Access for Claude Opus 5 is enabled once during bootstrap, either by the user in the Bedrock console or by an API call made by the bootstrap with the user's authenticated CLI session. This falls under the user's in-principle approval of the pipeline bootstrap (contracts decision 2026-10-07 item 11): it runs only after the bootstrap IaC is implemented, the exact stacks and a cost estimate are shown at run time, and nothing is enabled or deployed during spec work. Anthropic models on Bedrock may require a one-time use-case form or acknowledgement; that remains a human console step and is never automated by the pipeline. Verification: the bootstrap pre-check confirms the configured identifier is listed ACTIVE in us-east-2, and the first gamma smoke makes one minimal capped invocation (or, if gamma is set to a cheaper model, a one-off capped prod-identifier check run by the bootstrap).
- **Budget (contracts OQ-7 resolved).** Bedrock spend counts against the `bedrock_explanations` category of `/finplan/shared/financialplanning/config/budget-allocation` (default USD 5 of the USD 50 total). Opus is a high-cost tier, so a few long invocations could consume the allocation. Enforcement layers:
  1. a per-invocation `maxTokens` cap, plus per-turn and per-session token, cost and tool-call caps;
  2. a **per-session budget check before every Bedrock invocation**: refuse when `/finplan/shared/financialplanning/config/budget-state` is set, or when the remaining allocation (allocation minus the estimated month-to-date spend, i.e. the sum of the agent's own estimated-cost usage metrics across all environments in the single account) or the remaining session budget (`max_cost_session_usd` minus the session's spend) is smaller than the invocation's worst case (input tokens of the request plus `max_tokens_invocation`, priced at the configured rates). A refusal is a clear `BUDGET_EXCEEDED` error naming the allocation, remaining amount and worst-case estimate; the agent degrades to tool-only or evidence-only answers and makes no Bedrock call;
  3. **prompt caching** where the configured model supports it: Converse cache points after the stable prefix (system prompt, skill instructions, tool schemas), so repeated turns pay cache-read rather than full input rates. Support is checked against the current Bedrock prompt-caching documentation at implementation; when unsupported or disabled, calls run uncached;
  4. the **fixture/mock provider in CI**: the build stage and beta use `fixture` only and make no Bedrock calls; the build environment has no Bedrock permission;
  5. the platform-owned AWS Budgets budget (alerts at 50/80/100%, deny action at 100%), which names the FinanceAgent Runtime role through `/finplan/<env>/financeagent/config/budget-enforced-role-names`.
  The metric-based estimate is an approximation. The AWS Budgets action is the backstop.
- **Alternative considered:** an AgentCore Identity API-key credential provider with OpenAI. Rejected for now: Bedrock needs no credential, is billed to the same AWS budget and stays in-account.
- **Network:** Runtime public network mode reaches the Bedrock Runtime endpoint. No VPC or NAT, to keep cost near zero. A Bedrock VPC endpoint is a possible later hardening.

### D5. Streaming first, WebSocket gated

HTTP response streaming covers chat, tool progress and CLI rendering. WebSocket is warranted only for true bidirectional needs, such as voice or client-initiated mid-stream cancellation that HTTP cannot express. Any such need must be recorded as a design decision before the flag is turned on. Cancellation in phase 1 is handled by ending the stream, and the checkpoint stays resumable.

### D6. Skills

Skills live in `skills/<name>/` as `SKILL.md` instructions plus `skill.yaml` (version, tools, output contract). The graph loads them at startup. The build validates tool names against the pinned tool catalog fixture and the policy. An export step renders a credential-free bundle for Claude Code/Codex users.

### D7. Pipeline (contracts D6 applied)

- Source: the existing AVAILABLE GitHub CodeConnection for `FilippoLentoni/FinanceAgent` (`main`), referenced through `/finplan/shared/financeagent/config/codeconnection-ref` (FA-OQ-5 = contracts OQ-2, non-blocking).
- Build: unit, graph, policy, skills, conformance, scans, `cdk synth`, image build and push by digest, `release_id`.
- Beta: deploy, register beta targets, integration-beta (MCP list and call, agent invocation, ENV-15 checksum comparison, checkpoint scan).
- Gamma: deploy, register gamma targets, gamma tests (policy parity, isolation, drift, rollback drill).
- Approval.
- Prod: deploy, read-only smoke on the synthetic portfolio (prod platform data may be real or still synthetic during the decision 26 transition; the smoke accepts both).

Every MCP test uses a per-environment `ci_test` client-credentials app whose secret is referenced through `/finplan/<env>/financeagent/secret-ref/ci-test-client`.

### D8. Cost posture (USD 50 project cap)

- No always-on endpoints. Runtime, Gateway, Memory and the per-environment Cognito user pools are consumption-billed (Cognito by monthly active users; phase 1 has one human user plus the `ci_test` client per environment). ECR stores only a few images (lifecycle policy keeps N releases). Logs have short retention.
- No prices are recorded here. They must come from current AWS pricing at decision time.
- USD 50 is the total AWS budget for everything (contracts OQ-7 resolved). Runtime, Gateway, Memory, ECR and logs fall under `platform_infra` (USD 8 default). Bedrock invocations fall under `bedrock_explanations` (USD 5 default) and are guarded as in D4.
- Build and beta tests (CI) use the fixture/mock provider, so CI makes no Bedrock calls. Gamma makes one minimal Bedrock smoke invocation per release once `bedrock` is configured, under a small per-invocation token cap and the per-session budget check; gamma may use a cheaper model ID than prod's Claude Opus 5. Prod smoke stays read-only and makes no Bedrock call unless the user enables one capped invocation.
- The platform owns the AWS Budgets alarm (contracts D1, "Shared budget alarms"). FinanceAgent tags all resources with the contract tag keys.

## Risks / Trade-offs

- [The interceptor or policy engine cannot inject or verify identity as assumed] → fallback: tools validate a short-lived signed caller assertion minted by the interceptor (needs CONTRACT GAP-2 resolved). Gamma parity tests catch regressions.
- [Three Cognito user pools (one per environment) mean the project owner holds a separate login per environment] → accepted for isolation; the CLI and direct-MCP docs name the environment's pool reference. A cross-environment token is rejected by design (FA-POL-07).
- [Direct-client OAuth support differs between Claude Code and Codex] → documented helper per client. The policy is unchanged because the same identity provider and Gateway are used.
- [Memory per-session write rate limits step checkpoints] → batch step writes per turn, with DynamoDB checkpointer fallback (D3).
- [CloudFormation support for AgentCore resources is incomplete] → custom resource calling the control plane, still deployed only by the pipeline.
- [The AgentCore service is evolving fast and the docs facts above may change] → re-verification task before implementation, and the region table check runs in the bootstrap pre-check.
- [Single account for beta, gamma and prod (contracts OQ-1 resolved)] → per-environment roles, permission boundaries, environment-tag denies and separate per-environment resources. Gamma isolation tests. Multi-account is only a possible future migration.
- [Bedrock spend exhausts the USD 5 allocation or the metric-based estimate lags] → token caps, pre-flight check, budget-state flag and the AWS Budgets deny action on the Runtime role. When refused, the agent still returns tool-only and evidence-only answers.
- [Claude Opus 5 is a high-cost tier and a few long invocations could use most of the USD 5 allocation] → per-invocation `maxTokens` cap, per-session budget check before each call, prompt caching, fixture provider in CI, cheaper model IDs allowed in beta/gamma, short evidence-citing narratives.
- [Configured model loses access or is retired] → `DEPENDENCY_UNAVAILABLE` without silent fallback. A config-only release switches the model ID.

## Migration Plan

1. Prerequisites: contracts 1.0.0 published. The identity provider is decided (FA-OQ-1 RESOLVED 2026-10-07: FinanceAgent-owned Cognito user pool per environment); each environment's identity stack deploys before that environment's Gateway and Runtime in the same pipeline stage, and the project owner user is created by an administrator after the first beta deployment. The bootstrap uses the existing authenticated CLI session (OQ-11 resolved). It reuses the existing AVAILABLE CodeConnection through `/finplan/shared/financeagent/config/codeconnection-ref` and proves access with a source-stage dry run. Only if that fails does the user extend the GitHub App installation (OQ-2 non-blocking).
2. Bootstrap the FinanceAgent pipeline, the per-environment Gateway service roles and their `gateway-principal-ref` parameters (GAP-3), the default explanation-provider parameters (`kind: fixture`), and `budget-enforced-role-names`.
3. Wait for the FinanceLambdasTool beta release. Then promote FinanceAgent through beta → gamma → prod with the fixture provider.
4. Model access for Claude Opus 5 is enabled during bootstrap (console or API call, under the user's in-principle bootstrap approval) and verified ACTIVE. `us.anthropic.claude-opus-5` is written to the prod model-id parameter (gamma: the same ID or a cheaper one), with rates from configuration or the AWS Price List API and `retrieved_at`. A config-only release switches gamma to `bedrock`, then prod after approval.
5. Rollback: redeploy the recorded `release_id` (image digest plus recorded target set plus policy digest). Memory contents are not rolled back. Sessions written by a newer graph version carry `graph_version`, and older graphs start a new session rather than misread them.

## Contract gap status (cross-repo review, 2026-10-07)

- GAP-1 (identity provider owner): RESOLVED 2026-10-07: FinanceAgent owns a Cognito user pool per environment, referenced through `/finplan/<env>/financeagent/agent/user-pool-ref` and `authorizer-metadata-ref` (FA-OQ-1, D2). Contracts D1 still marks the row "provisional"; the FinancialPlanning contracts change should drop that qualifier. The website must reuse this provider (contracts OQ-10).
- GAP-2 (caller block): resolved in contracts D10 as `core/v1/caller.json`, carried outside the hashed body. The interceptor-versus-policy-engine mechanism is still verified by the spike (FA-OQ-2).
- GAP-3 (ordering): resolved in contracts (cross-repo-ownership, scenario "Gateway principal published before tool grants"). FinanceAgent bootstrap publishes `gateway-principal-ref` first.
- GAP-4 (matrix rows): resolved in contracts D1 (ECR repository, interceptor Lambda, Memory resource, policy engine, Gateway service role). The ECR repository is an allowed account-level shared resource (contracts ENV-16).
- GAP-5 (tool catalog key): resolved in contracts D4/D10 (`/finplan/<env>/financelambdastool/contract/tool-catalog`, manifest `outputs` key `tool-catalog`, `core/v1/tool-catalog.json`).

## Open Questions

| ID | Question | Blocks | Resolved by | Interim |
|---|---|---|---|---|
| FA-OQ-1 | Which repo owns the OIDC identity provider (users, roles, CI clients)? Shared with the website? (CONTRACT GAP-1) | None | **RESOLVED 2026-10-07:** FinanceAgent owns one Amazon Cognito user pool per environment as the Gateway and Runtime identity provider (groups as roles, self sign-up disabled, project owner as the only phase 1 human user), referenced only through `/finplan/<env>/financeagent/agent/user-pool-ref` and `authorizer-metadata-ref`; the website reuses it (contracts OQ-10); the contracts D1 row is to be marked final (D2) | n/a |
| FA-OQ-2 | Exact mechanism and field for passing verified caller identity from the Gateway to Lambda targets (interceptor versus policy engine; envelope field) (CONTRACT GAP-2) | BLOCKER for state-changing tools through the Gateway | Implementation spike against the docs plus a contract minor adding the caller block | Read-only tools only through the Gateway |
| FA-OQ-3 | Explanation provider and its credentials (= contracts OQ-3) | None | **RESOLVED 2026-10-07:** Amazon Bedrock with IAM auth; no API key or secret; provider stays a pluggable interface; OpenAI is only an optional future adapter (D4) | n/a |
| FA-OQ-4 | Which Bedrock model or inference profile in us-east-2, and is model access enabled? (= contracts OQ-13) | None (model access is a bootstrap task, 3.8) | **RESOLVED 2026-10-07:** Claude Opus 5 via US inference profile `us.anthropic.claude-opus-5` (verified ACTIVE in us-east-2), stored in `config/explanation-model-id`; access enabled at bootstrap; spend within `bedrock_explanations` (USD 5) with the D4 cost controls | `fixture` provider until access is enabled and verified |
| FA-OQ-5 | GitHub CodeConnection coverage for FinanceAgent (= contracts OQ-2) | None (non-blocking) | **RESOLVED 2026-10-07:** reuse an existing AVAILABLE connection in us-east-2 via SSM; the bootstrap verifies with a source-stage dry run; only on failure does the user extend the GitHub App installation | n/a |
| FA-OQ-6 | Does a WebSocket requirement exist (voice, mid-stream control)? | None for phase 1 | User decision | Disabled |
| FA-OQ-7 | Confirm Runtime container architecture, CFN/CDK resource coverage and identity injection (A-1..A-5) | Implementation tasks 1.x/3.x | Verification spike against current docs | none |

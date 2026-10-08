# Tasks

Scope: phase 1 (fixture-backed) only. "Fixture-backed" means the agent's provider and test plan records; platform snapshots and market data may be real phase 2 data in beta and gamma and real or still synthetic in prod (user decision 26, 2026-10-07), and no test may assume otherwise. No GPU or FinanceModel compute and no live trading. The explanation model is Claude Opus 5 via `us.anthropic.claude-opus-5` (FA-OQ-4 = contracts OQ-13 resolved 2026-10-07); the live Bedrock provider is enabled only by tasks 3.8–3.9, after model access is enabled at bootstrap and verified. CI never calls Bedrock. Tasks marked BLOCKED name the open question from design.md that must close first. Bootstrap uses the user's existing authenticated AWS CLI session (contracts OQ-11 resolved 2026-10-07) and reuses the existing GitHub CodeConnection (FA-OQ-5 non-blocking). All environments share one account in us-east-2 (contracts OQ-1 resolved). The identity provider is a FinanceAgent-owned Cognito user pool per environment, referenced through SSM (FA-OQ-1 RESOLVED 2026-10-07). Test IDs are defined in the mapping table at the end.

## 1. Verification spike and scaffolding

- [ ] 1.1 Re-verify the design's AgentCore facts against current official docs: region table for us-east-2, Runtime/Gateway/Memory quotas, Lambda target context, interceptor and policy-engine capabilities, Runtime container architecture, CloudFormation resource coverage (A-1..A-5). Record results in `docs/agentcore-verification.md` with doc URLs and dates; verify every assumption is marked confirmed or replaced by a fallback decision (closes FA-OQ-7)
- [x] 1.2 Create the repo layout (`agent/`, `skills/`, `policy/`, `infra/`, `cli/`, `tests/{unit,graph,contract,integration_beta,gamma,smoke}`); verify an empty build runs lint and an empty test suite
- [ ] 1.3 Pin `finplan-contracts` 1.x by exact version and digest from the CodeArtifact registry referenced by `/finplan/shared/financialplanning/contract/registry-ref`; verify the build fails on a deliberately wrong digest (CS-04 consumer side) (BLOCKED by contracts 1.0.0 publication)
- [x] 1.4 Wire the contract conformance runner in consumer mode, the copied-`$id` detector, the leak scan, the live-permission scan and the ownership check into the build stage; verify each fails on a fixture violation (CS-01, CS-10, OWN-01, OWN-03, ENV-05, ENV-08)

## 2. LangGraph agent core (fixture provider)

- [x] 2.1 Implement the graph (route → plan → tool-call → confirm → narrate → claim-check → respond) with a fake MCP tool client; verify FA-RT-01 graph tests and the architecture check that rejects non-LangGraph entry points
- [x] 2.2 Implement the Runtime HTTP entry point with streaming events (`progress`, `tool_call`, `tool_result_summary`, `token`, `final`) and a non-streaming mode; verify FA-RT-02 (ordering, streamed versus non-streamed equality) locally
- [ ] 2.3 Implement the WebSocket handler behind a disabled flag plus a build check that the flag requires a recorded requirement reference; verify FA-RT-03 unit tests
- [x] 2.4 Implement the confirmation interrupt for catalog-marked state-changing tools, with refusal paths for paid-job approval, live trading and risk-preference changes; verify FA-RT-05 and FA-RT-06 graph tests
- [x] 2.5 Implement turn-level `in_progress` handling for asynchronous `run_id` results and session lifecycle configuration; verify FA-RT-04 graph tests with a fake async job
- [x] 2.6 Implement the `describe` request (framework, `release_id`, contract version, provider kind, skills); verify a unit test and document it in `docs/agent-api.md`

## 3. Explanation provider

- [x] 3.1 Implement the provider config schema and loader for `/finplan/<env>/financeagent/config/explanation-provider` (`kind ∈ {bedrock, fixture}`, `max_tokens_invocation`, turn/session token caps, `max_cost_session_usd`, `prompt_caching`, rates with `source` and `retrieved_at`) and `/finplan/<env>/financeagent/config/explanation-model-id`, with Qwen/FinanceModel/`global.` denial patterns; verify FA-PRV-01, FA-PRV-02, FA-PRV-10 and FA-PRV-11 unit tests (`openai` and unknown kinds rejected; any `qwen` or `global.` identifier rejected; missing rates rejected for `bedrock`; `max_tokens_invocation` above `max_tokens_turn` rejected)
- [x] 3.2 Define the `ExplanationProvider` interface and implement the deterministic fixture provider (template narrative); verify deterministic output across runs and FA-PRV-06 interface conformance for the fixture adapter
- [x] 3.3 Implement the Bedrock adapter (Converse / ConverseStream with the Runtime role, `maxTokens` = `max_tokens_invocation` on every request, no secret read, typed error mapping including access-denied → `DEPENDENCY_UNAVAILABLE` with an enable-model-access hint, no fallback); verify FA-PRV-03, FA-PRV-04 (per-invocation cap sent) and FA-PRV-06 with a stubbed Bedrock client (no network)
- [x] 3.4 Implement token and tool-call guards and usage metrics (provider kind, model ID, tokens, estimated cost); verify FA-PRV-04 unit tests
- [x] 3.5 Implement the deterministic claim check (every number in the narrative must match a session tool or evidence value under declared rounding); verify FA-PRV-05 unit tests with planted unsupported figures
- [x] 3.6 Implement the per-session Bedrock budget check before every invocation (budget-state flag, `bedrock_explanations` category from the shared allocation, month-to-date estimate from usage metrics across environments, remaining session budget, worst-case invocation cost = input tokens + `max_tokens_invocation` at configured rates; refusal returns a clear `BUDGET_EXCEEDED` and degrades to tool-only/evidence-only); verify FA-PRV-07 and FA-PRV-12 unit tests with mocked SSM and metrics (exhausted allocation, flag set, session remainder insufficient, under budget)
- [x] 3.7 Synthesize the Runtime role's Bedrock grant from the model-id parameter: `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` only, on `arn:aws:bedrock:<region>:<account>:inference-profile/<model-id>` and on `arn:aws:bedrock:<us-region>::foundation-model/<foundation-model-id>` for every US region the profile routes to (read-only `get-inference-profile` at synth; no resolved ARNs in repo files); publish the Runtime role in `/finplan/<env>/financeagent/config/budget-enforced-role-names`; verify FA-PRV-08 role-scope synth test (including a fixture profile routed to three US regions), FA-PRV-09 published role names, and FA-POL-01
- [ ] 3.8 Bootstrap step (runs inside the approved one-time bootstrap, task 7.2, only after the bootstrap IaC exists; the exact stacks and cost estimate are shown first; never during spec work): enable Bedrock model access for Claude Opus 5 (`anthropic.claude-opus-5`) in the Bedrock console (human step, including any one-time Anthropic use-case form) or by an API call from the bootstrap; write `us.anthropic.claude-opus-5` to the prod model-id parameter and the chosen gamma ID (same or cheaper) to gamma; write rates (configured, or looked up from the AWS Price List API at run time) with `retrieved_at`; record the decision in `docs/explanation-provider.md` without prices or account-specific values
- [ ] 3.9 Verify model access: re-run read-only `list-inference-profiles` in us-east-2 and confirm `us.anthropic.claude-opus-5` (and the gamma ID) is ACTIVE, then make one minimal invocation capped by `max_tokens_invocation` with the prod identifier under the budget check; verify FA-PRV-15 (access verified before any `kind: bedrock` switch)
- [ ] 3.10 Switch gamma, then prod, to `bedrock` through a config-only release; verify gamma `describe` reports `bedrock` with the model ID, one capped smoke explanation succeeds, and the usage metric records its estimated cost (BLOCKED by 3.9)
- [x] 3.11 Implement prompt caching in the Bedrock adapter (cache points after system prompt, skill instructions and tool schemas when `prompt_caching` is enabled and the model supports it; cache read/write tokens in usage and cost); check current Bedrock prompt-caching support for the configured model and record it in `docs/explanation-provider.md`; verify FA-PRV-13 with a stubbed client (cached and unsupported cases)
- [x] 3.12 Add the CI guards: build-stage scan rejecting literal Bedrock model or profile identifiers outside configuration fixtures, a check that build and beta provider kind is `fixture`, and a build-role policy check with no Bedrock invoke action; verify FA-PRV-11 (scan) and FA-PRV-14 (zero Bedrock calls in CI)

## 4. Sessions and checkpoints

- [ ] 4.1 Integrate the AgentCore Memory checkpointer (`thread_id` = `session_id`, `actor_id` = salted caller hash, `graph_version` recorded); verify FA-SS-01 locally with an in-memory saver, then in beta
- [ ] 4.2 Implement the caller and environment isolation check before every checkpoint read; verify FA-SS-02 unit tests and the beta cross-caller test
- [ ] 4.3 Make tool results stored in checkpoints compact and contract-validated, and require re-reads by ID for figures; verify FA-SS-03 graph test and FA-SS-05 checkpoint scan in beta
- [ ] 4.4 Configure event expiry from configuration, add explicit session deletion, and check that no long-term strategy is configured in synthesized IaC; verify FA-SS-04
- [ ] 4.5 Load-test step checkpoint writes against the per-actor, per-session write limit with a 20-step fixture conversation; verify no throttling, or switch to the DynamoDB fallback (design D3)

## 5. Gateway, registration and policy

- [x] 5.0 Implement the per-environment identity stack: one Cognito user pool per environment (self sign-up disabled, groups `viewer`, `researcher`, `plan_editor`, `plan_publisher`, `ci_test`, environment and owner tags), a public authorization-code (PKCE) app client for the agent, CLI and direct MCP clients, and a `ci_test` client-credentials client whose secret is stored in Secrets Manager and referenced by name through `/finplan/<env>/financeagent/secret-ref/ci-test-client`; publish `/finplan/<env>/financeagent/agent/user-pool-ref` and `/finplan/<env>/financeagent/agent/authorizer-metadata-ref` and record them in the manifest `outputs`; order the stack before the Gateway and Runtime stacks in each stage; verify FA-POL-07 synth tests (one pool per environment, sign-up disabled, groups present, no literal pool or client IDs in repo files via the leak scan) (FA-OQ-1 RESOLVED 2026-10-07)
- [x] 5.1 Implement per-environment IaC for the Gateway (MCP, `CUSTOM_JWT` resolved from the same environment's `authorizer-metadata-ref`), Gateway service role scoped to same-environment tool references, and published `gateway-principal-ref`, Gateway endpoint and authorizer metadata references; configure the Runtime JWT inbound authorizer from the same parameter; verify FA-GW-01, FA-GW-05 and FA-POL-07 (authorizer bound to the same environment's pool) synth tests
- [x] 5.2 Implement the registration step that reads `/finplan/<env>/financelambdastool/release/manifest`, the `tool-catalog` output and `lambda/<tool>-arn`, with the contract-major compatibility gate; verify FA-GW-02 and FA-GW-03 with fixture manifests (catalog key and schema from contracts D4/D10)
- [x] 5.3 Implement the projection from contract schemas to the Gateway tool schema subset, with relaxations documented in descriptions; verify FA-GW-04 unit tests (pattern moved to description; output still validated by the tool)
- [x] 5.4 Write the Gateway policy (roles × tools × denied argument values such as `mode=live`) and its fixture test matrix; verify FA-POL-03, FA-POL-05 and FA-POL-06 unit tests and that the policy digest is emitted to the manifest
- [ ] 5.5 Implement the interceptor Lambda that injects verified caller claims and channel and strips body identity fields; verify FA-POL-04 unit tests (the envelope field is the contracts `core/v1/caller.json` block; the injection mechanism is BLOCKED by FA-OQ-2)
- [ ] 5.6 Configure the hosted agent to call the Gateway as the user (OBO or audience-restricted user token) and build the Runtime role without tool or platform permissions; verify FA-POL-01 (role policy check) and FA-POL-02 in beta with a project-owner token from the beta pool
- [x] 5.7 Enforce asynchronous patterns and response size limits through the tool client and document the limits in `docs/gateway.md`; verify FA-GW-06 with an oversized fixture result

## 6. Skills and clients

- [ ] 6.1 Add skill bundles `capability-overview`, `plan-lookup` and `experiment-status` with `skill.yaml` metadata; verify FA-SK-01 and FA-SK-03 graph tests
- [ ] 6.2 Implement skill validation against the pinned catalog fixture and policy; verify FA-SK-02 (disallowed tool fails; policy denial on skill path)
- [ ] 6.3 Implement the skill export bundle for Claude Code/Codex with placeholders only; verify FA-SK-04 leak scan
- [ ] 6.4 Publish `/finplan/<env>/financeagent/agent/runtime-ref` and build the optional CLI (stream rendering, resume); verify FA-CL-01 and FA-CL-02 in beta
- [ ] 6.5 Write `docs/direct-mcp.md` for Claude Code and Codex onboarding (per-client auth helper if A-4 fails); verify FA-CL-03 by a developer `tools/list` in beta matching the agent's tool set
- [ ] 6.6 Document website integration constraints (published references, identical IDs and checksums); verify FA-CL-04 as part of the contracts website check once a website exists (website owner is contracts OQ-10)

## 7. Pipeline and environments

- [x] 7.1 Write the pipeline stack (stages per contracts D6, artifact-only promotion, `release_id`, manifest and `current-release-id` writes, approval recording); verify FA-PL-01 with the contracts pipeline-structure check on the synthesized template and FA-PL-02 build-stage checks
- [ ] 7.2 Run the one-time bootstrap with the user's existing authenticated AWS CLI session (pipeline, ECR repository, per-environment Gateway service roles and `gateway-principal-ref`, `codeconnection-ref` pointing to the existing AVAILABLE connection, default explanation-provider parameters with `kind: fixture`, `budget-enforced-role-names`); include the Opus 5 model-access enablement step (task 3.8) in the same approved run, showing the exact stacks and a cost estimate first; verify the STS account and region pre-check (us-east-2) and a pipeline source-stage dry run against `FilippoLentoni/FinanceAgent` `main` (if the dry run fails, the user extends the GitHub App installation and reruns it). Recommendation only: move the operator to a scoped/MFA role later (GAP-3/GAP-4 resolved in contracts)
- [ ] 7.2a After each environment's first identity-stack deployment, create the project owner as the only human user in that environment's pool (administrator action, in the user's session; no user data in repo files) and add the needed groups; verify sign-in through the CLI with the environment's `authorizer-metadata-ref`
- [ ] 7.3 Implement beta integration tests: MCP `tools/list`, `describe_capabilities`, synthetic plan-version read, hosted-agent answer, checksum equality with a direct platform read, cross-caller session denial, and checkpoint scan; verify FA-PL-04 and ENV-15 pass in beta
- [ ] 7.4 Implement gamma tests: policy parity across channels, target isolation (gamma only), cross-environment invoke denied, beta-pool token rejected by the gamma Gateway and Runtime (FA-POL-07), policy drift check, and rollback drill; verify FA-POL-03, FA-PL-05, FA-POL-06 and FA-PL-06 in gamma
- [ ] 7.5 Implement read-only prod smoke on the synthetic portfolio; verify FA-PL-04 (prod) and FA-PL-03 digest equality across environments

## 8. Integration checks

- [ ] 8.1 Run the phase 1 end-to-end in beta then gamma with compatible FinancialPlanning and FinanceLambdasTool releases; verify ENV-15, OWN-06 (`DEPENDENCY_UNAVAILABLE` for model-backed tools when FinanceModel is absent) and OWN-08
- [ ] 8.2 After first prod promotion, verify ENV-10 and record the release in the rollback ledger

## Verification status (2026-10-08)

Checked tasks are verified **offline only** (full suite, release synth, post-synth gates, cfn-lint with 0 errors, leak scan, runtime import check); nothing is deployed yet. Every `integration-beta`, `gamma` and `smoke` part stays open until the first pipeline run. Partial or deviating items:

- 1.1: Runtime, Memory, Gateway client side, CloudFormation coverage, Cedar and policy-engine permissions confirmed (`docs/agentcore-verification.md`); quotas and the Lambda target identity path (FA-OQ-2) remain carried or open.
- 1.3: pinned by exact version and wheel digest to the vendored FinancialPlanning wheel (`contracts-pin.json`, re-pin with `scripts/check_contracts_pin.py --repin`); the CodeArtifact registry does not serve the package yet. The wrong-digest test passes.
- 1.4: the conformance runner, copied-`$id` detector and leak scan run inside the build's offline suites; the live-permission scan and ownership check are post-synth gates. Each one has a planted-violation test.
- 2.3: the WebSocket flag is off and its `requirement_ref` gate is tested; there is no handler.
- 3.7: the inference profile is resolved read-only by the `Resolve` stage action at deploy time, not at synth, so no resolved ARN reaches the assembly.
- 4.1–4.4, 5.6: the local parts are done; the beta parts are open.
- 5.2: registration also follows the deployed FinanceLambdasTool `tool-catalog-pointer` (catalog object in its pipeline store, sha256- and release-checked).
- 5.5: blocked by FA-OQ-2.
- 6.x: only the explanation skill bundles exist (add-explanation-workflows); `capability-overview`, `plan-lookup`, `experiment-status`, the export bundle, the CLI and `docs/direct-mcp.md` are not done.

## Requirement-to-test mapping

Test types: unit, contract, integration-beta, gamma, smoke. "graph" tests are unit-level LangGraph tests with fake tools and the fixture provider.

| Capability | Requirement | Test ID | Type |
|---|---|---|---|
| agent-runtime-hosting | Code-based LangGraph agent in AgentCore Runtime | FA-RT-01 | unit (architecture check, graph) + integration-beta (`describe`) |
| agent-runtime-hosting | HTTP invocation with response streaming first | FA-RT-02 | unit + integration-beta |
| agent-runtime-hosting | Bidirectional WebSocket only when required | FA-RT-03 | unit + integration-beta (connection refused) |
| agent-runtime-hosting | Session lifecycle within Runtime limits | FA-RT-04 | unit (graph) + integration-beta (resume after idle) |
| agent-runtime-hosting | Confirmation before state-changing tool calls | FA-RT-05 | unit (graph) + integration-beta |
| agent-runtime-hosting | No live financial actions | FA-RT-06 | unit (graph) + unit (live-permission scan, ENV-05) |
| agent-session-persistence | Durable checkpoints per session | FA-SS-01 | unit + integration-beta |
| agent-session-persistence | Caller and environment isolation of sessions | FA-SS-02 | unit + integration-beta + gamma |
| agent-session-persistence | Checkpoints are non-authoritative | FA-SS-03 | unit (graph) |
| agent-session-persistence | Retention and deletion | FA-SS-04 | unit (synth) + integration-beta |
| agent-session-persistence | No secrets or private holdings in checkpoints | FA-SS-05 | integration-beta (checkpoint scan) |
| tool-gateway | One Gateway per environment | FA-GW-01 | unit (synth) + gamma |
| tool-gateway | Registration from released references | FA-GW-02 | unit (fixture manifests) + integration-beta |
| tool-gateway | Compatibility gate before registration | FA-GW-03 | unit + gamma (OWN-08) |
| tool-gateway | Tool schemas derived from the pinned contract package | FA-GW-04 | unit + contract (CS-01, CS-10) |
| tool-gateway | Published Gateway references | FA-GW-05 | unit (synth) + integration-beta |
| tool-gateway | Gateway invocation limits | FA-GW-06 | unit + integration-beta |
| mcp-access-policy | Single tool access path | FA-POL-01 | unit (role policy check) |
| mcp-access-policy | FinanceAgent-owned identity provider per environment | FA-POL-07 | unit (synth: one pool per environment, authorizer bound to same-environment `authorizer-metadata-ref`) + gamma (beta-pool token rejected) |
| mcp-access-policy | Administrator-managed users with group roles | FA-POL-07 | unit (synth: sign-up disabled, groups present) |
| mcp-access-policy | Identity provider referenced only through SSM | FA-POL-07 | unit (leak scan: no pool or client IDs or ARNs) |
| mcp-access-policy | Same inbound authentication for agent and direct clients | FA-POL-02 | integration-beta + gamma |
| mcp-access-policy | Identical tool policy across channels | FA-POL-03 | unit (policy matrix) + gamma (parity replay) |
| mcp-access-policy | Caller identity propagated to tools | FA-POL-04 | unit (interceptor) + integration-beta |
| mcp-access-policy | Policy covers only paper and research operations | FA-POL-05 | unit + gamma |
| mcp-access-policy | Policy changes are versioned and tested | FA-POL-06 | unit + gamma (drift check) |
| explanation-provider | Configurable explanation provider | FA-PRV-01 | unit + integration-beta (`describe`, fixture) + gamma (`describe`, bedrock, after task 3.9) |
| explanation-provider | Claude Opus 5 through the US inference profile | FA-PRV-11 | unit (config validation: `global.` rejected) + smoke (prod `describe` reports `us.anthropic.claude-opus-5`) |
| explanation-provider | Model identifier only in configuration | FA-PRV-11 | unit (build-stage hard-coded ID scan) |
| explanation-provider | Pluggable provider interface | FA-PRV-06 | unit (adapter conformance: bedrock stubbed, fixture) |
| explanation-provider | Qwen is not an explanation provider | FA-PRV-02 | unit |
| explanation-provider | Bedrock access by IAM role without secrets | FA-PRV-03 | unit (stubbed client; access-denied mapping) + gamma (capped smoke, after task 3.9) + ENV-08 (no explanation-provider `secret-ref`) |
| explanation-provider | Least-privilege Bedrock grant | FA-PRV-08 | unit (role-scope synth: profile + routed regional foundation-model ARNs) |
| explanation-provider | Token and cost guards | FA-PRV-04 | unit (per-invocation `maxTokens`, turn and session caps) |
| explanation-provider | Per-session budget check before invocation | FA-PRV-12 | unit (mocked SSM and metrics) |
| explanation-provider | Prompt caching where supported | FA-PRV-13 | unit (stubbed client) |
| explanation-provider | No Bedrock calls in CI | FA-PRV-14 | unit (build-role policy check; stub call counter) + integration-beta (provider kind `fixture`) |
| explanation-provider | Model access enabled at bootstrap | FA-PRV-15 | bootstrap verification (task 3.9) + gamma (capped smoke) |
| explanation-provider | Bedrock spend within the explanation allocation | FA-PRV-07 | unit (mocked SSM and metrics) + gamma (ENV-17) |
| explanation-provider | Budget action covers the Runtime role | FA-PRV-09 | unit (synth: role name published) + gamma (ENV-19 policy simulation: Bedrock invoke denied after the budget action) |
| explanation-provider | Cost estimates from configured prices | FA-PRV-10 | unit |
| explanation-provider | Provider output never carries authoritative numbers | FA-PRV-05 | unit (claim check) |
| agent-skills | Versioned skill bundles | FA-SK-01 | unit + integration-beta |
| agent-skills | Skill tool lists bounded by policy | FA-SK-02 | unit + gamma |
| agent-skills | Phase 1 skill set | FA-SK-03 | unit (graph) + integration-beta |
| agent-skills | Skills exportable for direct MCP clients | FA-SK-04 | unit (leak scan) |
| agent-client-integration | Published agent invocation reference | FA-CL-01 | unit (synth) + integration-beta |
| agent-client-integration | Optional CLI client | FA-CL-02 | unit + integration-beta |
| agent-client-integration | Direct MCP client onboarding | FA-CL-03 | integration-beta (manual, recorded) |
| agent-client-integration | Website integration through published references | FA-CL-04 | gamma (when a website exists; OQ-10) |
| agent-release-pipeline | Pipeline stages per contract standard | FA-PL-01 | unit (pipeline template check, ENV-09) |
| agent-release-pipeline | Build-stage checks | FA-PL-02 | unit |
| agent-release-pipeline | Immutable promotion and release manifest | FA-PL-03 | smoke (ENV-10) + contract (ENV-06) |
| agent-release-pipeline | Deployed MCP invocation tests per environment | FA-PL-04 | integration-beta + gamma + smoke (ENV-15) |
| agent-release-pipeline | Gamma isolation verified | FA-PL-05 | gamma (ENV-03, OWN-05) |
| agent-release-pipeline | Rollback by release ID | FA-PL-06 | gamma (rollback drill, ENV-11) |

## Workflow follow-up

- Archive this change after the phase 1 end-to-end passes in gamma and the first prod smoke succeeds.
- `add-explanation-workflows` (phase 2) builds on the graph, provider, claim check and skills delivered here.

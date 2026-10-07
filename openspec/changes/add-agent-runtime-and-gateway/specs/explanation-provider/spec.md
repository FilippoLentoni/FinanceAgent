# Spec Delta

## Purpose

Defines the configurable LLM provider used for agent reasoning and explanation narrative. The default is Amazon Bedrock with IAM authentication (no API key), next to a deterministic fixture provider. The provider is a pluggable interface, so an OpenAI adapter can be added later without graph changes. The configured explanation model is Claude Opus 5 through the US cross-region inference profile `us.anthropic.claude-opus-5`, held only in SSM. Qwen is excluded as an explanation provider, and token, cost and Bedrock budget guards apply per invocation, per turn, per session and against the `bedrock_explanations` allocation.

## ADDED Requirements

### Requirement: Configurable explanation provider
The agent SHALL select its explanation provider kind from `/finplan/<env>/financeagent/config/explanation-provider` and the Bedrock model or inference-profile identifier from `/finplan/<env>/financeagent/config/explanation-model-id`. Allowed provider kinds MUST be exactly `bedrock` (the default for non-fixture use) and `fixture`. Changing the provider kind or the model identifier MUST NOT require a code change.

#### Scenario: Fixture provider in beta
- **WHEN** beta configuration sets provider kind `fixture`
- **THEN** the agent produces deterministic narrative from fixture templates and makes no external LLM call

#### Scenario: Bedrock provider configured
- **WHEN** gamma configuration sets provider kind `bedrock` and the gamma model-id parameter holds a model or inference-profile identifier
- **THEN** the agent sends model requests to Amazon Bedrock in us-east-2 with that identifier and reports the provider kind and model identifier in `describe`

#### Scenario: Unknown provider kind
- **WHEN** configuration sets provider kind `openai` or any other value than `bedrock` or `fixture`
- **THEN** the agent fails configuration validation at startup and the deployment's health check fails

#### Scenario: Model change is configuration only
- **WHEN** an operator changes the gamma model-id parameter to another Bedrock model available in us-east-2
- **THEN** a config-only release (same artifact digest) switches the model and re-scopes the Runtime role's Bedrock permission to the new identifier

### Requirement: Claude Opus 5 through the US inference profile
The prod explanation model SHALL be Claude Opus 5 through the US cross-region inference profile `us.anthropic.claude-opus-5` (foundation model `anthropic.claude-opus-5`), stored in `/finplan/<env>/financeagent/config/explanation-model-id`. Configuration validation MUST reject `global.` inference profiles so inference stays in US regions. Beta and gamma MAY configure a cheaper model identifier through the same key, under the same validation, grants and cost controls.

#### Scenario: Prod model from configuration
- **WHEN** the prod Runtime starts with provider kind `bedrock` and the prod model-id parameter holds `us.anthropic.claude-opus-5`
- **THEN** every Bedrock request uses that identifier and `describe` reports it

#### Scenario: Global profile configured
- **WHEN** an environment's model-id parameter holds a `global.`-prefixed inference profile
- **THEN** configuration validation fails at startup and the deployment's health check fails

#### Scenario: Cheaper model in gamma
- **WHEN** the gamma model-id parameter holds a cheaper Bedrock model identifier than prod
- **THEN** gamma uses it with a Runtime grant scoped to that identifier, and prod is unaffected

### Requirement: Model identifier only in configuration
Agent code, tests outside configuration fixtures and skill bundles SHALL NOT contain a literal Bedrock model or inference-profile identifier; the identifier MUST be read from the model-id parameter at run time.

#### Scenario: Hard-coded model identifier
- **WHEN** a pull request adds a literal Bedrock model or inference-profile identifier to agent code, tests outside configuration fixtures, or a skill bundle
- **THEN** the build-stage scan fails and names the file

### Requirement: Pluggable provider interface
Every provider SHALL implement one provider interface (generate narrative with token accounting and a usage record). The graph, claim check and guards MUST depend only on that interface. Adding a provider adapter, such as an optional future OpenAI adapter, MUST NOT change graph nodes, and MUST NOT be enabled by configuration until its adapter and its credential reference exist in a released version.

#### Scenario: Adapter conformance
- **WHEN** the unit suite runs the provider conformance tests against the `bedrock` adapter (with a stubbed Bedrock client) and the `fixture` adapter
- **THEN** both pass the same interface tests for narrative output, token accounting, usage records and error mapping

### Requirement: Qwen is not an explanation provider
The agent SHALL reject any configuration that names Qwen3.6-27B, any Qwen model (including Bedrock-hosted Qwen model identifiers) or any FinanceModel-hosted strategy model as the explanation model. Self-hosted Qwen3.6-27B MUST remain a FinanceModel strategy benchmark provider only.

#### Scenario: Qwen configured as provider
- **WHEN** configuration sets the explanation model to a Qwen model identifier or a FinanceModel inference reference
- **THEN** the agent fails configuration validation at startup and the deployment's health check fails

### Requirement: Bedrock access by IAM role without secrets
The `bedrock` provider SHALL authenticate with the environment's Runtime execution role only. No API-key secret and no `secret-ref` parameter MUST exist for the explanation provider. Credentials and provider responses MUST NOT appear in logs, traces or release manifests beyond the usage record.

#### Scenario: No provider secret
- **WHEN** the leak scan and the synthesized gamma stacks are checked
- **THEN** no explanation-provider secret, `secret-ref` parameter or Secrets Manager read grant for the provider exists

#### Scenario: Model access not enabled
- **WHEN** provider kind is `bedrock` and Bedrock denies the call because model access for the configured model is not enabled in the account
- **THEN** the agent reports `DEPENDENCY_UNAVAILABLE` for explanation requests with a hint that model access must be enabled, does not fall back silently to another provider, and still serves tool-only requests

### Requirement: Least-privilege Bedrock grant
The Runtime role SHALL be allowed only `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`, only on the configured inference profile (`arn:aws:bedrock:<region>:<account>:inference-profile/<model-id>`) and on each regional foundation model it routes to (`arn:aws:bedrock:<us-region>::foundation-model/<foundation-model-id>`), derived from configuration at synth time. It MUST NOT hold any other Bedrock action or resource.

#### Scenario: Role scope check
- **WHEN** the build stage inspects the synthesized gamma Runtime role
- **THEN** it fails if the role allows any Bedrock action other than the two invoke actions or any resource other than the configured profile and its routed foundation models

#### Scenario: US profile routed to another US region
- **WHEN** prod is configured with `us.anthropic.claude-opus-5` and the profile routes a request to a US region other than us-east-2
- **THEN** the invocation is authorized because the grant includes the `anthropic.claude-opus-5` foundation-model ARN in every US region listed by the profile

### Requirement: Token and cost guards
The agent SHALL send the configured per-invocation maximum output tokens (`max_tokens_invocation`) with every Bedrock request and SHALL enforce configured per-turn and per-session limits on provider tokens and tool calls. When a limit is reached it MUST stop, return `BUDGET_EXCEEDED` with the limit in `details`, and keep the session resumable.

#### Scenario: Per-invocation cap sent
- **WHEN** the `bedrock` adapter calls Converse or ConverseStream
- **THEN** the request carries `maxTokens` equal to the configured `max_tokens_invocation`, and configuration validation rejects a value above `max_tokens_turn`

#### Scenario: Per-turn token limit reached
- **WHEN** a turn's provider token usage would exceed the configured per-turn limit
- **THEN** the agent stops generation, returns `BUDGET_EXCEEDED`, and records the usage in the session

#### Scenario: Usage reporting
- **WHEN** any turn completes
- **THEN** the agent emits a metric with provider kind, model identifier, input tokens, output tokens, estimated cost and tool-call count, tagged with environment and `release_id`

### Requirement: Bedrock spend within the explanation allocation
Before every `bedrock` provider call, the agent SHALL run a pre-flight check. It MUST refuse the call with `BUDGET_EXCEEDED` when `/finplan/shared/financialplanning/config/budget-state` is set, or when the estimated month-to-date Bedrock explanation spend across all environments plus the turn's worst-case cost would exceed the `bedrock_explanations` category of `/finplan/shared/financialplanning/config/budget-allocation` (default USD 5).

#### Scenario: Allocation exhausted
- **WHEN** the estimated month-to-date Bedrock explanation spend plus the worst-case cost of the next turn exceeds the `bedrock_explanations` allocation
- **THEN** the agent returns `BUDGET_EXCEEDED` with the allocation, the estimate and the rates' `retrieved_at` in `details`, makes no Bedrock call, and still serves tool-only and evidence-only answers

#### Scenario: Budget-state flag set
- **WHEN** the project budget has reached 100% and the budget-state flag is set
- **THEN** the agent refuses Bedrock calls in its pre-flight check

### Requirement: Per-session budget check before invocation
Before every Bedrock invocation the agent SHALL compare the invocation's worst-case cost (input tokens plus `max_tokens_invocation` at configured rates) with the remaining `bedrock_explanations` allocation and the remaining session budget (`max_cost_session_usd` minus session spend). If either is insufficient it MUST make no call, return a clear `BUDGET_EXCEEDED` naming the remainder and estimate, and degrade to a tool-only or evidence-only answer.

#### Scenario: Session budget insufficient
- **WHEN** a session has spent most of `max_cost_session_usd` and the next invocation's worst case exceeds what remains
- **THEN** no Bedrock call is made, the response carries `BUDGET_EXCEEDED` with the session remainder and estimate, and tool results are still returned

#### Scenario: Remaining allocation sufficient
- **WHEN** both the remaining allocation and the remaining session budget exceed the invocation's worst case
- **THEN** the invocation proceeds and its estimated cost is added to the session and month-to-date usage

### Requirement: Prompt caching where supported
When the configured model supports Bedrock prompt caching and `prompt_caching` is enabled with cache rates configured, the `bedrock` adapter SHALL place cache points after the stable prompt prefix (system prompt, skill instructions, tool schemas) and MUST record cache read and cache write tokens in the usage record. When the model does not support caching, the adapter MUST run uncached without error.

#### Scenario: Cached prefix reused
- **WHEN** a second turn in a session sends the same stable prefix to a model that supports caching
- **THEN** the usage record reports cache-read tokens for the prefix and the cost estimate prices them at the configured cache-read rate

#### Scenario: Caching unsupported
- **WHEN** the configured model does not support prompt caching
- **THEN** the adapter sends no cache points and the invocation succeeds

### Requirement: No Bedrock calls in CI
The build stage and the beta environment SHALL use the fixture or mock provider only. CI MUST NOT invoke Bedrock, and the build environment MUST NOT hold Bedrock invoke permissions.

#### Scenario: CI run
- **WHEN** the build stage and the beta integration tests run
- **THEN** the provider kind is `fixture`, the stubbed Bedrock client records zero network calls, and the build role policy contains no Bedrock invoke action

### Requirement: Model access enabled at bootstrap
Bedrock model access for the configured prod model (Claude Opus 5) SHALL be enabled once during the approved pipeline bootstrap, by the user in the Bedrock console or by an API call made by the bootstrap, after the bootstrap IaC exists and after the exact stacks and a cost estimate have been shown. The pipeline and the Runtime role MUST NOT manage model access, and the switch to `bedrock` MUST NOT happen until access is verified.

#### Scenario: Access verified before switch
- **WHEN** the bootstrap has enabled model access for `anthropic.claude-opus-5`
- **THEN** the verification lists `us.anthropic.claude-opus-5` ACTIVE in us-east-2 and one minimal capped invocation succeeds before any environment is configured with kind `bedrock`

#### Scenario: Access not yet enabled
- **WHEN** model access has not been enabled
- **THEN** every environment stays on the `fixture` provider and no Bedrock invocation is attempted

### Requirement: Cost estimates from configured prices
The worst-case invocation, turn and session costs SHALL be computed from per-token rates in the provider configuration, taken either from configured values or from AWS pricing looked up at run time by the configuration step, and MUST carry a `retrieved_at` date. Prices MUST NOT be hard-coded or recorded in repository files.

#### Scenario: Prices missing
- **WHEN** provider kind is `bedrock` and the provider configuration has no per-token rates or no `retrieved_at`
- **THEN** the agent fails configuration validation at startup rather than calling Bedrock without a cost estimate

### Requirement: Budget action covers the Runtime role
FinanceAgent SHALL publish its Runtime role name in `/finplan/<env>/financeagent/config/budget-enforced-role-names` so that the AWS Budgets deny action at 100% stops Bedrock invocations.

#### Scenario: Budget action applied
- **WHEN** the AWS Budgets deny action has been applied at 100%
- **THEN** a direct Bedrock invoke attempt with any environment's Runtime role is denied by the action policy

### Requirement: Provider output never carries authoritative numbers
Narrative generated by the provider SHALL NOT be the source of any numeric financial result. Every number the agent presents MUST come from a tool result or evidence artifact present in the session.

#### Scenario: Unsupported number in narrative
- **WHEN** the provider's draft contains a figure that matches no tool result value in the session
- **THEN** the deterministic claim check removes or flags the figure before the response is returned

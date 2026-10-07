# Spec Delta

## Purpose

Guarantees that the hosted agent and direct MCP clients (Claude Code, Codex) reach tools under the same backend policy: the same FinanceAgent-owned per-environment identity provider and authentication, the same tool allow-list, end-user identity propagated to tools, and no privileged bypass.

## ADDED Requirements

### Requirement: Single tool access path
All tool calls, whether from the hosted agent or from a direct MCP client, SHALL go through the environment's Gateway. The agent Runtime's execution role MUST NOT hold permission to invoke tool Lambdas, FinancialPlanning platform APIs or FinanceModel APIs directly. Its only model-service permission is the Bedrock invoke grant for the configured explanation model (explanation-provider capability).

#### Scenario: Runtime role policy check
- **WHEN** the build stage inspects the synthesized Runtime execution role
- **THEN** it fails if the role allows `lambda:InvokeFunction` on any tool Lambda or calls to any platform or FinanceModel endpoint, and it accepts only the Bedrock invoke grant scoped to the configured explanation model

### Requirement: FinanceAgent-owned identity provider per environment
FinanceAgent SHALL own exactly one Amazon Cognito user pool per environment (beta, gamma, prod), deployed by its pipeline. That pool MUST be the only inbound identity provider for the same environment's Gateway and Runtime.

#### Scenario: Authorizer bound to the same environment's pool
- **WHEN** the gamma Gateway and Runtime stacks are synthesized
- **THEN** their JWT authorizer issuer and allowed clients are resolved from `/finplan/gamma/financeagent/agent/authorizer-metadata-ref`, and neither references a beta or prod pool

#### Scenario: Token from another environment
- **WHEN** a caller presents a valid token issued by the beta user pool to the gamma Gateway or the gamma Runtime
- **THEN** the request is rejected as unauthenticated and no tool Lambda or graph step runs

### Requirement: Administrator-managed users with group roles
Caller roles SHALL be Cognito groups (`viewer`, `researcher`, `plan_editor`, `plan_publisher`, `ci_test`). Self sign-up MUST be disabled, and users MUST be created only by an administrator.

#### Scenario: Self sign-up attempt
- **WHEN** an unknown person attempts to register a new user in any environment's pool
- **THEN** registration is refused, because only an administrator can create users

### Requirement: Identity provider referenced only through SSM
Consumers SHALL locate the pool only through `/finplan/<env>/financeagent/agent/user-pool-ref` and `/finplan/<env>/financeagent/agent/authorizer-metadata-ref`. Pool identifiers, client identifiers and ARNs MUST NOT appear in repository files.

#### Scenario: Literal pool identifier committed
- **WHEN** a commit adds a Cognito user pool ID, app client ID or ARN to a repository file
- **THEN** the build-stage leak scan fails the build

### Requirement: Same inbound authentication for agent and direct clients
The Gateway SHALL authenticate every caller with the environment's configured inbound authorizer. The hosted agent MUST call the Gateway on behalf of the end user who invoked it, not with a broader service identity.

#### Scenario: Unauthenticated MCP call
- **WHEN** an MCP client calls `tools/list` on the gamma Gateway without valid credentials
- **THEN** the Gateway rejects the request and no tool Lambda is invoked

#### Scenario: Agent acts as the user
- **WHEN** user U invokes the hosted agent and it calls a tool
- **THEN** the tool's audit record shows caller U with invocation channel `hosted_agent`

### Requirement: Identical tool policy across channels
One policy, defined in this repository and evaluated at the Gateway, SHALL decide which tools each caller role may call. The decision MUST NOT depend on whether the caller is the hosted agent or a direct MCP client. The channel is recorded for audit only.

#### Scenario: Same denial on both channels
- **WHEN** a caller without the `plan_publisher` role calls `publish_plan_version` directly from Claude Code, and the same caller asks the hosted agent to publish
- **THEN** both calls are denied at the Gateway with `FORBIDDEN`, and neither reaches the tool Lambda

#### Scenario: Policy parity test
- **WHEN** the gamma policy-parity suite replays each fixture request through both channels
- **THEN** every allow or deny decision and error code matches between channels

### Requirement: Caller identity propagated to tools
For every tool call, the Gateway SHALL supply the authenticated caller identity and invocation channel to the tool Lambda in a form the tool can verify as Gateway-supplied. Caller identity in the request body MUST NOT override it.

#### Scenario: Body claims another identity
- **WHEN** a direct MCP client sends a tool request whose body contains `caller: admin`
- **THEN** the tool receives the authenticated identity from the Gateway, and the body value is ignored for authorization

### Requirement: Policy covers only paper and research operations
The tool policy SHALL allow only tools present in the FinanceLambdasTool catalog. It MUST deny any tool or argument value that requests live execution, payments or wallet actions, even if such a tool appears in a catalog.

#### Scenario: Live mode argument
- **WHEN** any caller sends a tool call with execution mode `live`
- **THEN** the call is denied with `OPERATION_NOT_PERMITTED` before reaching the target

### Requirement: Policy changes are versioned and tested
The tool policy SHALL be stored in this repository, versioned with the release, and validated in the build stage against fixture callers and tools. Policy MUST NOT be edited in a deployed environment outside the pipeline.

#### Scenario: Out-of-band policy edit
- **WHEN** the deployed gamma policy digest differs from the gamma release manifest's recorded policy digest
- **THEN** the gamma drift check fails and promotion stops

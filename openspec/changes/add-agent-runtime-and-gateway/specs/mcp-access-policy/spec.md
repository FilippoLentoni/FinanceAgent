# Spec Delta

## Purpose

Guarantees that the hosted agent and direct MCP clients (Claude Code, Codex) reach tools under the same backend policy: the same authentication, the same tool allow-list, end-user identity propagated to tools, and no privileged bypass.

## ADDED Requirements

### Requirement: Single tool access path
All tool calls, whether from the hosted agent or from a direct MCP client, SHALL go through the environment's Gateway. The agent Runtime's execution role MUST NOT hold permission to invoke tool Lambdas, FinancialPlanning platform APIs or FinanceModel APIs directly. Its only model-service permission is the Bedrock invoke grant for the configured explanation model (explanation-provider capability).

#### Scenario: Runtime role policy check
- **WHEN** the build stage inspects the synthesized Runtime execution role
- **THEN** it fails if the role allows `lambda:InvokeFunction` on any tool Lambda or calls to any platform or FinanceModel endpoint, and it accepts only the Bedrock invoke grant scoped to the configured explanation model

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

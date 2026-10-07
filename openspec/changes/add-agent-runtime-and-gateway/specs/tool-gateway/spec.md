# Spec Delta

## Purpose

Exposes the FinanceLambdasTool tools over MCP through one AgentCore Gateway per environment. Targets are registered only from that environment's released references, and the Gateway's own references are published for consumers.

## ADDED Requirements

### Requirement: One Gateway per environment
FinanceAgent SHALL deploy exactly one MCP-protocol AgentCore Gateway per environment (beta, gamma, prod), tagged with the environment and owning repository. Each Gateway MUST register targets only for tool Lambdas published for the same environment.

#### Scenario: Gamma Gateway targets
- **WHEN** the gamma Gateway's targets are listed after deployment
- **THEN** every target's Lambda reference equals the value read from `/finplan/gamma/financelambdastool/lambda/<tool>-arn`, and none resolves to beta or prod

### Requirement: Registration from released references
Gateway target registration SHALL read the tool list, tool schema `$id`s and Lambda references from the FinanceLambdasTool release manifest and tool catalog for that environment. Literal Lambda identifiers MUST NOT appear in repository files.

#### Scenario: New tool released
- **WHEN** FinanceLambdasTool publishes a gamma release whose catalog adds a tool
- **THEN** the next FinanceAgent gamma deployment registers that tool, and the Gateway tool list matches the catalog

#### Scenario: Tool removed from catalog
- **WHEN** a tool is absent from the current catalog
- **THEN** the redeployed Gateway no longer lists it

### Requirement: Compatibility gate before registration
Before registering targets, the pipeline SHALL verify that the environment's FinanceLambdasTool release serves a contract major compatible with FinanceAgent's pinned contract version. If no compatible release exists, registration MUST be skipped and the stage MUST fail with a dependency-missing message.

#### Scenario: No tool release in gamma
- **WHEN** the gamma stage finds no FinanceLambdasTool manifest in gamma
- **THEN** Gateway registration is skipped and the stage fails with a dependency-missing message

#### Scenario: Incompatible contract major
- **WHEN** the gamma tool release serves only contract major 2 and FinanceAgent pins major 1
- **THEN** the stage fails, reports the incompatibility, and the previous Gateway targets are left unchanged

### Requirement: Tool schemas derived from the pinned contract package
Each registered tool's input and output schema SHALL be generated from the pinned contract package schemas named in the tool catalog. Where the Gateway's schema format cannot express a contract constraint, the published schema MUST be a documented relaxation, and the tool Lambda remains the authoritative validator.

#### Scenario: Pattern constraint not expressible
- **WHEN** a contract schema uses an identifier pattern the Gateway schema format cannot carry
- **THEN** the generated Gateway schema states the pattern in the property description, and an invalid identifier sent through the Gateway is rejected by the tool with `INVALID_IDENTIFIER`

#### Scenario: Copied schema
- **WHEN** the repository contains a hand-written copy of a contract schema
- **THEN** the consumer conformance check (copied `$id` detection) fails the build

### Requirement: Published Gateway references
Each deployment SHALL publish the Gateway MCP endpoint reference, the Gateway invocation principal reference and the inbound authorizer metadata reference as SSM parameters under `/finplan/<env>/financeagent/agent/`, and record them in the release manifest `outputs`.

#### Scenario: Tool repo reads Gateway principal
- **WHEN** FinanceLambdasTool deploys its gamma invoke grants
- **THEN** it reads the gamma Gateway principal from `/finplan/gamma/financeagent/agent/gateway-principal-ref`

### Requirement: Gateway invocation limits
Tool calls through the Gateway SHALL complete within the Gateway invocation timeout. Long-running work MUST use the asynchronous `submit_experiment`, `get_job_status` and `get_experiment_result` pattern, and responses MUST respect the Gateway payload limit.

#### Scenario: Oversized result
- **WHEN** a tool result would exceed the configured response byte limit
- **THEN** the tool returns a compact summary with trusted artifact references instead of the full payload

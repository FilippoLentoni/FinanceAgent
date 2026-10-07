# Spec Delta

## Purpose

Defines the optional ways clients reach FinanceAgent: a CLI and a website through the hosted agent invocation, and Claude Code or Codex through direct MCP connection to the Gateway. All of them use published references and the same identifiers as the platform.

## ADDED Requirements

### Requirement: Published agent invocation reference
Each deployment SHALL publish the Runtime invocation reference and its endpoint qualifier under `/finplan/<env>/financeagent/agent/runtime-ref`, and record it in the release manifest. Clients MUST resolve the agent only through this reference.

#### Scenario: CLI resolves agent
- **WHEN** the optional CLI starts with environment `beta`
- **THEN** it reads the beta runtime reference from SSM and contains no literal endpoint

### Requirement: Optional CLI client
The repository SHALL provide an optional CLI that invokes the hosted agent with the caller's own credentials, renders streamed events, and supports session resume by `session_id`. The CLI MUST NOT call tool Lambdas or platform APIs directly.

#### Scenario: CLI streaming session
- **WHEN** a user runs the CLI with a question against beta and later resumes with the printed `session_id`
- **THEN** the CLI shows streamed events and the resumed session continues the same conversation

### Requirement: Direct MCP client onboarding
The repository SHALL document how Claude Code and Codex connect directly to an environment's Gateway using the published Gateway endpoint reference and the configured authentication. Documentation MUST state that direct clients get exactly the tool policy of the hosted agent.

#### Scenario: Claude Code lists tools
- **WHEN** an authorized developer configures Claude Code with the documented beta Gateway connection
- **THEN** `tools/list` returns the same tool set the hosted beta agent can call for that developer

### Requirement: Website integration through published references
If a website integrates the agent, it SHALL use the published runtime reference and the same caller authentication, and it MUST display platform identifiers and checksums exactly as tools return them. Website ownership remains an open question and is not delivered here.

#### Scenario: Website and agent agree
- **WHEN** the website shows a plan version and the agent is asked about the same plan
- **THEN** both show the same `plan_version_id` and checksum

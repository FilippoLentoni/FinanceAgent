# Spec Delta

## Purpose

Hosts the FinanceAgent code-based LangGraph agent in AgentCore Runtime per environment. Defines how it is invoked, how it streams, when bidirectional WebSocket is allowed, and how it confirms state-changing tool calls with the user.

## ADDED Requirements

### Requirement: Code-based LangGraph agent in AgentCore Runtime
FinanceAgent SHALL implement the agent as a code-defined LangGraph graph packaged in this repository and hosted in AgentCore Runtime, one Runtime per environment. The agent MUST NOT be replaced by another agent framework or by a managed agent harness, and its graph definition MUST be versioned with the release.

#### Scenario: Deployed agent identifies its implementation
- **WHEN** a caller invokes the gamma agent with a `describe` request
- **THEN** the response reports framework `langgraph`, the gamma `release_id`, the pinned contract version and the configured explanation provider kind

#### Scenario: Managed harness proposed
- **WHEN** a pull request replaces the LangGraph graph with a managed agent harness or another framework
- **THEN** the build-stage architecture check fails and names the disallowed runtime entry point

### Requirement: HTTP invocation with response streaming first
The agent SHALL accept requests over the Runtime HTTP invocation contract and MUST support incremental response streaming of tokens and tool-progress events. A non-streaming request MUST receive the same final content as a streamed request for the same session state and fixture inputs.

#### Scenario: Streamed answer
- **WHEN** a client invokes the agent with streaming accepted and the fixture provider configured
- **THEN** the client receives ordered events (`progress`, `tool_call`, `tool_result_summary`, `token`, `final`), and the `final` event carries the `correlation_id` and `session_id`

#### Scenario: Streaming and non-streaming agree
- **WHEN** the same prompt is sent once streamed and once non-streamed in fresh sessions with the fixture provider
- **THEN** both `final` payloads are byte-identical apart from timestamps and IDs

### Requirement: Bidirectional WebSocket only when required
The agent SHALL NOT expose a bidirectional WebSocket endpoint unless a documented requirement (a design decision naming the use case and why HTTP streaming is insufficient) is recorded and enabled in that environment's configuration. Phase 1 MUST ship with WebSocket disabled.

#### Scenario: Phase 1 WebSocket attempt
- **WHEN** a client opens a WebSocket connection to the phase 1 beta agent
- **THEN** the connection is refused and HTTP invocation remains available

#### Scenario: WebSocket enabled by recorded requirement
- **WHEN** a later release sets the WebSocket feature flag and references the recorded requirement
- **THEN** the build check passes only if the WebSocket handler shares the same graph, policy and session store as the HTTP path

### Requirement: Session lifecycle within Runtime limits
The agent SHALL configure session idle timeout and maximum lifetime from environment configuration within the Runtime's documented limits. A request that would exceed the Runtime synchronous request or streaming duration MUST return a resumable `in_progress` state instead of failing silently.

#### Scenario: Idle session resumes
- **WHEN** a session is idle longer than the configured idle timeout and the client sends a new message with the same `session_id`
- **THEN** the agent restores the conversation from the persisted checkpoint and continues

#### Scenario: Long tool job
- **WHEN** a tool returns an asynchronous `run_id` for a job expected to outlast the request
- **THEN** the agent ends the turn with the `run_id` and job status and does not hold the connection open waiting for completion

### Requirement: Confirmation before state-changing tool calls
The hosted agent SHALL pause and obtain explicit user confirmation in the same session before calling any tool that the tool catalog marks state-changing (for example override, validate, publish, or non-dry-run experiment submission). It MUST include the exact arguments and idempotency key in the confirmation request.

#### Scenario: User confirms publish
- **WHEN** the user asks the agent to publish validated plan version `pv_B` and then confirms the displayed request
- **THEN** the agent calls the publish tool once with the displayed idempotency key and reports the returned `publication_id`

#### Scenario: User declines
- **WHEN** the user declines the confirmation
- **THEN** no state-changing tool is called and the checkpoint records the declined action

#### Scenario: Paid job approval requested
- **WHEN** a user asks the agent to approve a paid FinanceModel job or raise a budget
- **THEN** the agent refuses with `OPERATION_NOT_PERMITTED` semantics and points to the human approval path, because approving paid jobs is out of scope for the agent

### Requirement: No live financial actions
The agent SHALL NOT call, register or describe any capability for live trading, brokerage or exchange accounts, AgentCore payments or wallet spending, and MUST NOT modify a user's stored risk preferences or constraints.

#### Scenario: Live trade request
- **WHEN** a user asks the agent to place a live order
- **THEN** the agent declines, calls no tool, and states that only paper or simulated execution exists

#### Scenario: Risk preference rewrite request
- **WHEN** the agent's reasoning would change a stored risk preference to make a recommendation feasible
- **THEN** no tool call changes the preference, and the agent presents the trade-off to the user instead

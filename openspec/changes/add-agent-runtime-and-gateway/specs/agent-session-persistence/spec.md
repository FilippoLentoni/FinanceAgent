# Spec Delta

## Purpose

Persists agent sessions and LangGraph checkpoints so conversations survive Runtime environment recycling. Checkpoints stay isolated per caller and environment and never become authoritative plan state.

## ADDED Requirements

### Requirement: Durable checkpoints per session
The agent SHALL persist a LangGraph checkpoint after every completed graph step, keyed by environment, authenticated caller and `session_id`. A session MUST be resumable from its latest checkpoint after the Runtime execution environment is terminated.

#### Scenario: Resume after environment recycle
- **WHEN** the Runtime terminates a session's execution environment and the same caller sends the next message with the same `session_id`
- **THEN** the agent continues from the latest checkpoint with prior messages and pending confirmations intact

#### Scenario: Crash mid-step
- **WHEN** the agent process exits during a tool call
- **THEN** on resume the agent restarts from the last completed step and re-issues the tool call with the same idempotency key, so no duplicate state change occurs

### Requirement: Caller and environment isolation of sessions
A session SHALL be readable and resumable only by the caller that created it, in the environment where it was created. Session identifiers MUST be unguessable and at least the length the Runtime requires.

#### Scenario: Another caller reuses a session ID
- **WHEN** caller B sends a message with caller A's `session_id`
- **THEN** the request fails with `FORBIDDEN` and caller A's checkpoint is neither read nor modified

#### Scenario: Cross-environment resume
- **WHEN** a gamma session ID is presented to the prod agent
- **THEN** it is treated as unknown and no gamma state is read

### Requirement: Checkpoints are non-authoritative
Checkpoints SHALL store only conversation messages, graph state, tool-call arguments and compact tool results with trusted artifact references and identifiers. They MUST NOT be used as the source for plan, publication, execution or snapshot content. Any figure the agent reports MUST be re-read through a tool by its immutable identifier.

#### Scenario: Stale plan in checkpoint
- **WHEN** a resumed session's checkpoint mentions `pv_A` and the user asks for its allocations
- **THEN** the agent fetches `pv_A` through the plan tool and reports the tool's checksum, not values from the checkpoint

### Requirement: Retention and deletion
Checkpoint and session event retention SHALL be set from environment configuration within the store's supported range and MUST be deleted automatically at expiry. A caller MUST be able to delete their own session explicitly. Long-term memory extraction MUST be disabled in phase 1.

#### Scenario: Explicit deletion
- **WHEN** a caller requests deletion of their session
- **THEN** subsequent resume attempts for that `session_id` start a new empty session

#### Scenario: Long-term memory disabled
- **WHEN** the phase 1 IaC is synthesized
- **THEN** the session store has no long-term memory extraction strategy configured

### Requirement: No secrets or private holdings in checkpoints
Checkpoints SHALL NOT contain secret values, provider API keys or raw storage locations. Tool results MUST be stored in the compact, contract-validated form the tool returned.

#### Scenario: Checkpoint scan
- **WHEN** the integration-beta suite scans checkpoints written during the test run
- **THEN** no checkpoint matches the contract package's credential, ARN or bucket-name patterns

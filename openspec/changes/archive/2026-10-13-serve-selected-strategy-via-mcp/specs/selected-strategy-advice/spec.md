# Spec Delta

## Purpose

Lets the hosted portfolio agent invoke the experimentally selected strategy and explain its recommendations without altering their numerical content.

## ADDED Requirements

### Requirement: Selected strategy on user request
The agent SHALL use recommend_portfolio through Gateway as the authenticated caller when asked for a strategy-based recommendation. It MUST request missing portfolio state rather than invent holdings or replace the selected strategy with an LLM-generated allocation.

#### Scenario: Structured recommendation
- **WHEN** a caller supplies a valid snapshot, date and portfolio state
- **THEN** the agent invokes the read tool and returns its target weights and decision deltas

### Requirement: Evidence-backed recommendation output
The answer SHALL preserve the complete tool recommendation and identify its strategy, source run and configuration. It MUST distinguish advisory targets from executions and omit unsupported return forecasts.

#### Scenario: No published forecast
- **WHEN** the selected PPO strategy reports forecast not_available
- **THEN** the agent retains that status and does not turn a backtest result or critic value into a forecast

### Requirement: Deployed skill and request deadline
The runtime SHALL load the recommendation skill from its deployed package and expose its version and checksum. Its MCP client deadline MUST cover the 300-second target plus transport overhead.

#### Scenario: Hosted runtime
- **WHEN** the packaged agent starts and describes its skills
- **THEN** the recommendation skill and checksum are present and its MCP request deadline exceeds the target cap


### Requirement: Hosted beta supports a guarded language model

The hosted beta agent SHALL allow the configured Bedrock provider with existing token, session
and project budget checks. Offline CI SHALL use fixture or stub providers and SHALL NOT make real
Bedrock Runtime calls.

#### Scenario: Natural-language request in hosted beta
- **WHEN** an authenticated beta caller asks for portfolio advice
- **THEN** the configured guarded language model can collect required state and call the recommendation tool
- **AND** CI still verifies routing through fixtures or stubs without network model calls

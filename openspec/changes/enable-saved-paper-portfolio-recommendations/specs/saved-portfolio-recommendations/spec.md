# Spec Delta

## Purpose

Provide complete policy recommendations using the saved paper portfolio without repeatedly asking users for holdings.

## ADDED Requirements

### Requirement: Default saved portfolio recommendation
The agent SHALL invoke the read-only recommend_portfolio MCP tool with {} for an ordinary portfolio planning or policy recommendation request without supplied holdings. It SHALL use freshly read tool results, not prior recommended targets as executed holdings.

#### Scenario: Default investment question
- **WHEN** the user asks how to invest today without supplying a portfolio
- **THEN** the agent calls recommend_portfolio with {} and does not request holdings when the saved paper portfolio is available

### Requirement: Complete evidence-grounded recommendation
The agent SHALL render all returned instruments and cash, current and target allocations, signed proposed share and dollar changes when supplied, decision date and policy provenance. It SHALL identify saved paper state and distinguish model targets from executions or causal market explanations.

#### Scenario: Provider draft omits cash
- **WHEN** the producer returns a recommendation including cash but provider prose omits it or invents a cause
- **THEN** the final answer uses deterministic complete producer evidence and preserves the structured recommendation

### Requirement: Supplied state compatibility
The agent SHALL preserve explicit recommendation request arguments and SHALL ask for incomplete explicitly supplied actual state rather than silently substitute the paper portfolio.

#### Scenario: Explicit structured request
- **WHEN** the caller supplies a complete existing explicit-state recommendation
- **THEN** the same request reaches recommend_portfolio without replacing its holdings

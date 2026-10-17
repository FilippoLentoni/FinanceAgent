# Spec Delta

## Purpose

Provide reproducible classical mcp advice for auditable beta portfolio decisions, explanations and controlled research.

## ADDED Requirements

### Requirement: Separate traditional MCP
The system SHALL expose a distinct beta traditional-optimization MCP endpoint with its own authorized catalog. Existing PPO endpoint behavior MUST remain available.

#### Scenario: Direct classical client
- **WHEN** a client discovers the new MCP
- **THEN** it sees classical recommendation/explanation/research tools and required supporting reads, with no PPO allocation tool.

### Requirement: Equivalent hosted and direct skills
Hosted and direct clients SHALL discover the same versioned recommendation, attribution, performance and research recipes with instruction checksums. Hosted traces MUST identify applied skills and actual MCP tool calls.

#### Scenario: Skill discovery
- **WHEN** the hosted and direct recommendations are compared
- **THEN** their recipe checksums and numerical producer results match.

### Requirement: Recommendation and explanations
Natural-language and structured requests SHALL route traditional or combined PPO/traditional recommendations through the proper MCP. Follow-ups MUST use fresh immutable-analysis reads, never inferred figures from assistant text.

#### Scenario: Why after traditional recommendation
- **WHEN** a user asks why the prior traditional recommendation sells Google
- **THEN** the agent calls the classical explanation tool with the issued analysis ID and returns its checked evidence.

### Requirement: Comparison and performance questions
The agent SHALL resolve explicit or stored plan IDs for plan-over-plan and performance questions, disclose dates/source/thresholds and present unavailable evidence explicitly.

#### Scenario: Green or red
- **WHEN** a user asks whether the observed portfolio is trending green or red
- **THEN** the agent returns the deterministic performance classification and data-source limitations.

### Requirement: Research and isolation
The agent SHALL support sourced reviews, immutable feedback and budget-gated sandbox requests without silently activating a strategy. Beta acceptance MUST preserve Gamma/prod releases and the selected PPO artifact.

#### Scenario: Research request
- **WHEN** a user asks for modeling improvements
- **THEN** the response provides stored hypotheses, citations and estimates or a bounded authorized run, with activation remaining explicit.

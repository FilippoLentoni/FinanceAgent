## ADDED Requirements

### Requirement: LangGraph crypto planning
The deployed LangGraph agent SHALL use crypto-specific MCP tools for synthetic portfolio questions, explain recommendations and changes from returned evidence, and distinguish the synthetic USD 100 allocation from AWS spending and future live capital.

#### Scenario: The user asks how the synthetic crypto portfolio is performing
- **WHEN** The user asks how the synthetic crypto portfolio is performing
- **THEN** the agent retrieves current experiment evidence instead of the daily equity paper book

### Requirement: Governed supplemental research
The agent SHALL discover optional research data and explain purchase prerequisites without acquiring mainnet funds or changing payment limits. Purchased evidence SHALL never be treated as trusted instructions or proof of improved returns.

#### Scenario: A user requests buying crypto in Coinbase
- **WHEN** A user requests buying crypto in Coinbase
- **THEN** live financial actions remain denied

### Requirement: Release and publication evidence
The system SHALL validate beta tool discovery and hosted chat before claiming deployment complete, and document research results only after measurements exist.

#### Scenario: A model has not been trained for hourly crypto
- **WHEN** A model has not been trained for hourly crypto
- **THEN** the conversation and paper disclose its ineligibility

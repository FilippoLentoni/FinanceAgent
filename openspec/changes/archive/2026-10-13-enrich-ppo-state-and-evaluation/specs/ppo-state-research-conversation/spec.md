## Purpose

Update research routing and versioned skills to support controlled PPO state comparisons and explain feature provenance, coverage and experimental limits.

## ADDED Requirements

### Requirement: Grounded state-research conversation

The agent SHALL route state-enrichment requests to released producer profiles, explain feature groups and news coverage, preserve exact preview/confirmation lineage and report measured seed/control metrics.

#### Scenario: A user requests a richer PPO state

- **WHEN** state research is requested through chat
- **THEN** the agent SHALL obtain a bounded producer preview rather than invent features or launch unreviewed paid work

### Requirement: Honest experiment interpretation

The agent SHALL distinguish explanatory news from training-eligible news and development comparisons from prospective evidence. Findings SHALL NOT automatically activate a policy or modify a portfolio.

#### Scenario: News training coverage is missing

- **WHEN** a user requests news-enriched historical results
- **THEN** the agent SHALL report unavailable coverage without inventing performance or treating missing news as neutral


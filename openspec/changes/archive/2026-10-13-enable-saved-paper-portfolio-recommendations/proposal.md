# Proposal

## Why

Recommendation questions currently ask for portfolio inputs every time, and generated prose can omit cash. The user needs the hosted agent to invoke the saved paper portfolio policy through MCP and return complete proposed share changes.

## What Changes

- Route ordinary portfolio planning and buy/sell recommendation questions to recommend_portfolio with an empty request by default.
- Preserve supplied-state recommendations and report saved paper state explicitly.
- Render every instrument and cash deterministically from producer evidence; retain exact structured results.
- Pin contracts 1.3.0 and update skills, API documentation and regression coverage.

## Capabilities

### New Capabilities

- `saved-portfolio-recommendations`: Saved paper portfolio recommendation behavior and evidence.

### Modified Capabilities

None. Existing capabilities are represented by unarchived changes.

## Impact

Existing recommendation contracts, runtime/tool code, skills and offline regression tests. Beta deployment only; no training, execution or new recurring costs.

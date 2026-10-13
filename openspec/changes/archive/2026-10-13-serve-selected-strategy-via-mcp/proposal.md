# Proposal

## Why

On 2026-10-09 the user clarified that the agent must invoke whichever portfolio strategy is selected after offline experimentation through an MCP Lambda target. The existing research pipeline trains policies but does not provide this strategy-neutral on-demand serving path.

## What Changes

- Load the versioned recommendation skill in the deployed runtime and call recommend_portfolio on user request, using the authenticated caller through Gateway.
- Return the tool's complete allocation, buy/sell/hold deltas and frozen strategy provenance without generating a new allocation in the LLM.
- Allow the client deadline to cover the five-minute tool, while preserving existing narration budget and confirmation boundaries.

## Capabilities

### New Capabilities

- `selected-strategy-advice`: this repository's part of reproducible, on-demand selected-strategy recommendations.

### Modified Capabilities

None in the archived spec inventory. This complements the existing in-flight daily and explanation changes; it does not replace performance replay with an allocation-hold approximation.

## Impact

Skill packaging, LangGraph recommendation request/output, MCP deadline and deployment checks. No automatic execution, strategy changes or retraining. Validation starts offline. Any new AWS work in this round stays below USD 2 and within the user's USD 50 project budget; no fresh training is authorized by an inference request.

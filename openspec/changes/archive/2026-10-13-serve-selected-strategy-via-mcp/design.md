# Design

## Context

See proposal.md. Current local edits were started before OpenSpec tracking and are uncommitted. The current MCP adapter reaches FinanceModel through a short job API; the user clarified a direct, five-minute Lambda target on 2026-10-09.

## Goals / Non-Goals

**Goals:** one selected-strategy recommendation interface with bounded request latency, exact model semantics and provenance.

**Non-Goals:** training during a recommendation, automatic promotion, broker execution, and changes to full explanation evidence semantics.

## Decisions

Package skills in both the image and BuildOutput. The runtime loads manifests and instruction checksums, then uses the existing authenticated Gateway client and graph read-tool path. Set the MCP client request timeout to 330 seconds. Return the full producer recommendation in the final answer; narration never changes weights. The existing performance/recommendation-change/sensitivity workflows retain their separate evidence-job design.

The selected research policy remains beta advisory/paper while the existing promotion gate is unmet. This is separate from the production-strategy key. The source run and explicit strategy selector freeze the chosen parameters; no fallback to a different algorithm is silent. Unsupported algorithms fail explicitly.

## Risks / Trade-offs

- [Cold starts or data reads dominate inference] → use bounded inputs, nested deadlines and a deployed latency check; no always-on endpoint.
- [Different preprocessing changes the policy] → compare exported deterministic actions with the training library and constrain the frozen universe and feature order.
- [Read requests still incur cost] → no training privileges, fixed timeouts, bounded request size and beta validation under the round's USD 2 cap within the USD 50 project budget.

## Migration Plan

Release producer contracts, model service, adapters, then agent into beta through their existing pipelines. Preserve gamma/prod gates. Roll back to the recorded releases or clear the advisory pointer; source artifacts remain immutable. Existing uncompleted explanation tasks stay uncompleted until their specified deployed tests pass.


## Beta-only user steering (2026-10-09)

The hosted beta agent must be usable through natural-language requests because the user wants all four pipelines validated in beta. Replace the old environment-wide fixture restriction with guarded hosted Bedrock using the already configured economical model and existing SSM price card. Offline tests retain fixture/stub providers and prohibit real Bedrock calls. Gamma/prod stay outside this deployment. The lifecycle is documented in `docs/strategy-lifecycle.md`; recurring autonomous research is a future phase, with no schedule enabled now.

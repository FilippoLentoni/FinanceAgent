# Explanations: evidence first, narrative second

OpenSpec change `add-explanation-workflows`. This page documents the contract between the explanation
evidence and the narrative, the three workflows, and the modeled-effect disclaimer. Code:
`agent/finplan_agent/explanations/`. Skills: `skills/explain-*`.

## The rule

Every number in an explanation comes from an **evidence artifact computed by a deterministic tool or
FinanceModel job** and referenced by a contract trusted reference (`core/v1/artifact-ref.json`, kind
`explanation_evidence`, with `checksum`). The agent and the explanation provider (Amazon Bedrock, or the
fixture provider in CI) never compute financial figures. The narrative is a separate field,
written after the evidence is final and checked deterministically against it.

> **Disclaimer (fixed text, appended whenever a result carries `modeled_effect`):** Attributions,
> re-solves and sweeps are modeled effects under the stated model and inputs, not real-world causes.

## Pipeline (one per request)

```
explain_prepare  validate request -> read anchors as the caller -> inventory/policy -> evidence jobs
                 -> dry-run estimates -> budget gate (explanation limits + cpu_research remaining)
explain_confirm  confirmation interrupt: exact submit_experiment calls + estimates
                 (or, for adopt-alternative, the exact create_override_version call + concessions)
explain_collect  submit -> bounded status reads (a running job ends the turn in_progress with run_ids)
                 -> fetch evidence -> deterministic checks (failure: VALIDATION_FAILED, no narrative)
explain_narrate  per-session Bedrock budget check -> ONE capped provider call over the compact
                 evidence summary -> explanation claim check -> contract result
```

* **Caller identity.** Every read and every job submission goes through the environment's Gateway with
  the caller's own token. A policy denial of `submit_experiment` returns `FORBIDDEN` and an
  "evidence unavailable for this caller" answer; no job exists.
* **No state change on its own.** Explanation code reaches tools only through `GuardedTools`: a
  state-changing tool runs only with an idempotency key approved in the confirmation interrupt
  (`submit_experiment` with `dry_run: true` is the only exception). Concessions are never applied.
* **Budget.** Evidence jobs: `cpu_research`; a non-CPU category is `OPERATION_NOT_PERMITTED`; over the
  per-request limit or the remaining allocation is `BUDGET_EXCEEDED` with the estimates and an offer of
  cheaper evidence. Narration: `bedrock_explanations`, preflighted by the phase 1 guard; a refusal
  returns the full evidence with `narrative_status: budget_exceeded` and a `narrative_message` naming
  the remaining amount and the estimate. A provider failure gives `unavailable`.
* **Limits** (`config/<env>.json` `explanations`, overridable by
  `/finplan/<env>/financeagent/config/explanation-limits`): `enabled`, `max_resolves_per_request`,
  `max_shapley_groups`, `max_sweep_points`, `max_estimated_usd_per_request`,
  `narration_max_tokens_invocation`, `status_polls`, `default_tolerance`. prod is disabled until
  approval.

## Request

`POST /invocations` with an `explanation` object (a `prompt` is optional):

| `type` | Required fields | Optional |
|---|---|---|
| `plan_performance` | `publication_id` (or `plan_id` / `plan_version_id` resolved to one publication), `window`, `realized_snapshot_id` | `actual_source` (`paper` or `simulated`) |
| `recommendation_change` | `plan_version_id` (new), `compare_to_plan_version_id` (previous) | `groups` (subset of data, configuration, constraints, model), `shapley`, `evaluation_window` |
| `sensitivity` | `plan_version_id`, `parameter` `{name, values, instrument?}` | `fixed_inputs`, `evaluation_window` |
| `adopt_alternative` | `plan_version_id`, `sweep_run_id`, `point_index` | |

All types accept `evidence_run_ids` (evidence kind to `run_id`) to reuse evidence from an earlier,
still-running turn without submitting again.

## Result

The result is a `core/v1/explanation-result.json` document validated against the pinned contract, plus
the fields the contract does not define yet (CONTRACT GAP-E2): `explanation_type`, `request_ids`,
`evidence_items[]` (citation key `evN`, kind, artifact ID, checksum, run ID, labels, deterministic
statements), `checks[]`, `labels[]`, `narrative_status`, `citations[]`, `claim_check`,
`completion_status` (`succeeded`), `solution_status` (`no_effect` for an empty difference inventory),
`findings` (sweep classifications, concessions, experiment proposals) and `disclaimer`.

### Labels

`modeled_effect`, `no_effect`, `requires_retraining`, `concession`, `manual_override`,
`excel_import`, `inventory_only_model`, `infeasible_switch_<group>`, `uncertain_attribution`,
`lineage_unavailable`, `within_forecast_interval`, `outside_forecast_interval`,
`forecast_not_available`, `components_not_available`, `partial_period`.

### Deterministic checks

| Workflow | Checks (any `failed` blocks narration) |
|---|---|
| 1 | `anchor_identifiers`, `reconciliation_plan_path`, `reconciliation_executed_path`, `gap_components_sum_to_total`, `forecast_position_consistent` |
| 2 | `no_effect_identical_lineage`, `base_reproduction`, `all_switched_reproduction`, `effect_<group>_<quantity>`, `decomposition_sum_<quantity>`, `weights_sum_switch_<group>`, `seed_spread_reported`, `shapley_efficiency_<quantity>`, `shapley_resolve_count`, `shapley_total_matches_resolves` |
| 3 | `sweep_points_complete`, `point_<i>_weights_sum`, `point_<i>_portfolio_wide_change`, `point_<i>_constraints`, `point_<i>_no_effect_consistent` |

### Narrative claim check

Sentence by sentence: citation tags must name evidence of this result; each figure must match a value
of an evidence item cited **in the same sentence** (phase 1 rounding rules), else it is replaced by
`[unsupported figure removed]`; causal verbs are rewritten as modeled effects; trend claims need
computed sweep points that support them; "no effect" claims need a `no_effect` label; mandatory
statements (disclaimer, `intraday_partial` days, stochastic uncertainty) are appended if missing.

### Example (workflow 2, fixture backend, two factor groups)

Request:

```json
{
  "explanation": {
    "type": "recommendation_change",
    "plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY02",
    "compare_to_plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY01"
  }
}
```

Confirmation request (the session waits for `{"action": "confirm", "approve": true}`):

```json
{
  "type": "confirmation_required",
  "calls": [
    {
      "tool": "submit_experiment",
      "arguments": {
        "domain": "finance",
        "domain_schema_version": "1.0",
        "purpose": "research",
        "input_snapshot_id": "snap_01JABCDEFGHJKMNPQRSTVWXY01",
        "evaluation_window": {
          "start": "2026-01-02",
          "end": "2026-03-31"
        },
        "configuration": {
          "domain": "finance",
          "domain_schema_version": "1.0",
          "payload": {
            "evidence_kind": "controlled_resolve_set",
            "previous_plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY01",
            "new_plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY02",
            "groups": [
              "data",
              "configuration"
            ],
            "lineage": {
              "solver": "clarabel",
              "seeds": [
                7
              ],
              "deterministic": true
            }
          }
        },
        "job_type": "controlled_resolve",
        "dry_run": false,
        "idempotency_key": "fa-41b7d9a37c51e9e0245e5057e2198789b5b485f9debdad08"
      },
      "idempotency_key": "fa-41b7d9a37c51e9e0245e5057e2198789b5b485f9debdad08"
    }
  ],
  "explanation": {
    "kind": "evidence_jobs",
    "explanation_type": "recommendation_change",
    "estimates": [
      {
        "job_type": "controlled_resolve",
        "evidence_kind": "controlled_resolve_set",
        "resolves": 4,
        "estimated_usd_upper_bound": 0.02,
        "remaining_allocation_usd": 6.5,
        "budget_category": "cpu_research",
        "price_retrieved_at": "2026-10-01T00:00:00Z"
      }
    ],
    "budget": {
      "total_estimated_usd_upper_bound": 0.02,
      "max_estimated_usd_per_request": 0.5,
      "remaining_allocation_usd": 6.5,
      "budget_category": "cpu_research"
    }
  }
}
```

Result (validated by `tests/unit/test_explanation_docs.py`):

<!-- example:explanation-result -->
```json
{
  "domain": "finance",
  "domain_schema_version": "1.0",
  "subject": {
    "plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY02",
    "compare_to_plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY01"
  },
  "evidence": [
    {
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY01",
      "owner": "financemodel",
      "kind": "explanation_evidence",
      "checksum": "sha256:ce08afd8a403c5fff0f4afcbb0a3f0e407f004d2114acfb283e5fc85eb58e258",
      "content_type": "application/json",
      "domain": "finance",
      "synthetic": true
    },
    {
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY02",
      "owner": "financemodel",
      "kind": "explanation_evidence",
      "checksum": "sha256:97bd66b61491944298a13b78c10975f28c665bd8cafeb2e65f0d287c28cf049b",
      "content_type": "application/json",
      "domain": "finance",
      "synthetic": true
    }
  ],
  "narrative": "Previous version pv_01JABCDEFGHJKMNPQRSTVWXY01 (checksum sha256:1111111111111111111111111111111111111111111111111111111111111111, configuration cfg_a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1, snapshot snap_01JABCDEFGHJKMNPQRSTVWXY01) [ev1]. New version pv_01JABCDEFGHJKMNPQRSTVWXY02 (checksum sha256:2222222222222222222222222222222222222222222222222222222222222222, configuration cfg_b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2, snapshot snap_01JABCDEFGHJKMNPQRSTVWXY02) [ev1]. Difference in data: snap_01JABCDEFGHJKMNPQRSTVWXY01 to snap_01JABCDEFGHJKMNPQRSTVWXY02 [ev1]. Difference in configuration at /payload/risk_aversion: 2.0 to 2.5 [ev1]. Under the stated model, switching only data changes the objective by 0.03, expected return by 0.004, risk by 0.01 (modeled_effect) [ev2]. Per-asset weight changes from switching data: AGG 0.05, SPY -0.05 [ev2]. Under the stated model, switching only configuration changes the objective by -0.01, expected return by -0.002, risk by -0.02 (modeled_effect) [ev2]. Per-asset weight changes from switching configuration: AGG 0.1, SPY -0.1 [ev2]. The total modeled change in the objective is 0.02, with an interaction remainder of 0.0 [ev2]. Attributions, re-solves and sweeps are modeled effects under the stated model and inputs, not real-world causes.",
  "generated_at": "2026-10-08T12:00:00Z",
  "explanation_type": "recommendation_change",
  "request_ids": {
    "plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY02",
    "compare_to_plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXY01"
  },
  "evidence_items": [
    {
      "cite": "ev1",
      "kind": "difference_inventory",
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY01",
      "checksum": "sha256:ce08afd8a403c5fff0f4afcbb0a3f0e407f004d2114acfb283e5fc85eb58e258",
      "run_id": null,
      "source_tool": "compare_plan_versions",
      "labels": [],
      "statements": [
        "Previous version pv_01JABCDEFGHJKMNPQRSTVWXY01 (checksum sha256:1111111111111111111111111111111111111111111111111111111111111111, configuration cfg_a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1, snapshot snap_01JABCDEFGHJKMNPQRSTVWXY01) [ev1].",
        "New version pv_01JABCDEFGHJKMNPQRSTVWXY02 (checksum sha256:2222222222222222222222222222222222222222222222222222222222222222, configuration cfg_b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2, snapshot snap_01JABCDEFGHJKMNPQRSTVWXY02) [ev1].",
        "Difference in data: snap_01JABCDEFGHJKMNPQRSTVWXY01 to snap_01JABCDEFGHJKMNPQRSTVWXY02 [ev1].",
        "Difference in configuration at /payload/risk_aversion: 2.0 to 2.5 [ev1]."
      ]
    },
    {
      "cite": "ev2",
      "kind": "controlled_resolve_set",
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY02",
      "checksum": "sha256:97bd66b61491944298a13b78c10975f28c665bd8cafeb2e65f0d287c28cf049b",
      "run_id": "run_01JABCDEFGHJKMNPQRSTVWXY10",
      "source_tool": "get_experiment_result",
      "labels": [
        "modeled_effect"
      ],
      "statements": [
        "Under the stated model, switching only data changes the objective by 0.03, expected return by 0.004, risk by 0.01 (modeled_effect) [ev2].",
        "Per-asset weight changes from switching data: AGG 0.05, SPY -0.05 [ev2].",
        "Under the stated model, switching only configuration changes the objective by -0.01, expected return by -0.002, risk by -0.02 (modeled_effect) [ev2].",
        "Per-asset weight changes from switching configuration: AGG 0.1, SPY -0.1 [ev2].",
        "The total modeled change in the objective is 0.02, with an interaction remainder of 0.0 [ev2]."
      ]
    }
  ],
  "checks": [
    {
      "name": "difference_inventory_present",
      "status": "passed",
      "tolerance": 0.0,
      "observed": null,
      "evidence": "ev1"
    },
    {
      "name": "base_reproduction",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "all_switched_reproduction",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "weights_sum_switch_data",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_data_objective",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_data_expected_return",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_data_risk",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "weights_sum_switch_configuration",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_configuration_objective",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_configuration_expected_return",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "effect_configuration_risk",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "decomposition_sum_objective",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "decomposition_sum_expected_return",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    },
    {
      "name": "decomposition_sum_risk",
      "status": "passed",
      "tolerance": 1e-06,
      "observed": 0.0,
      "evidence": "ev2"
    }
  ],
  "labels": [
    "modeled_effect"
  ],
  "narrative_status": "generated",
  "citations": [
    {
      "cite": "ev1",
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY01"
    },
    {
      "cite": "ev2",
      "artifact_id": "art_01JABCDEFGHJKMNPQRSTVWXY02"
    }
  ],
  "completion_status": "succeeded",
  "solution_status": "not_applicable",
  "findings": {},
  "disclaimer": "Attributions, re-solves and sweeps are modeled effects under the stated model and inputs, not real-world causes.",
  "claim_check": {
    "passed": true,
    "checked_figures": 14,
    "removed_figures": [],
    "unknown_citations": [],
    "causal_rewrites": [],
    "rejected_sentences": [],
    "appended_statements": 1,
    "replaced_with_statements": false
  },
  "synthetic": true
}
```

## Dependencies not yet available (BLOCKERS / CONTRACT GAPs)

* **EX-OQ-9:** FinanceLambdasTool read tools `get_publication`, `list_publications`,
  `list_executions` and `compare_plan_versions` (synchronous; `compare_plan_versions` returns
  `{evidence, evidence_ref}`). A missing tool gives `DEPENDENCY_UNAVAILABLE` (`tool_not_offered`).
* **GAP-E1 / EX-OQ-1:** FinanceModel CPU job types `performance_decomposition`, `controlled_resolve`,
  `grouped_shapley`, `sensitivity_sweep`, returning `payload.evidence` plus exactly one
  `explanation_evidence` artifact reference, and recording `recorded_request`, `evaluator_version` and
  seeds for reproduction (FA-EV-03).
* **GAP-E2 / EX-OQ-2:** per-kind evidence schemas; the agent's expectation is pinned as DRAFT schemas
  in `tests/fixtures/explanations/draft-schemas/` (never published), plus the envelope fields above
  (the contract envelope has no `narrative_status`, `checks`, `labels` or `citations`).
* **EX-OQ-6:** the portfolio-policy schema; until then the original policy is the base version's
  `content.constraints` (`max_turnover` as `turnover_limit`, `max_weight`, `min_weight`, `max_risk`).
* **FA-OQ-2:** how the caller identity reaches the tool Lambdas (the Lambda target context carries only
  gateway, target, tool and request IDs), needed for the evidence-job audit record.

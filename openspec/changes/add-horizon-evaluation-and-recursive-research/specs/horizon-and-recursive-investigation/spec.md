## ADDED Requirements

### Requirement: Objective-aware investigation
The agent SHALL separate daily observed attribution from full strategy evaluation using the decision's declared objective, evaluation windows and frozen sequential replay. It SHALL disclose partial horizons and unavailable evidence rather than infer failure or optimality from short returns.

#### Scenario: Daily loss before the strategy horizon
- **WHEN** a user asks whether a daily loss proves PPO failed
- **THEN** the agent retrieves the saved decision and numerical evaluation, reports daily results separately from available horizon evidence, and preserves the producer's limited-evidence conclusion.

### Requirement: Bounded recursive review
The agent SHALL offer a persisted recursive improvement workflow with a dry-run default, experiment lineage, cost bounds and human review before paid launch or activation.

#### Scenario: Research cycle requested
- **WHEN** a user asks for recursive improvement
- **THEN** the agent invokes run_recursive_improvement without launching paid compute and shows the resulting cycle state and proposal.

#### Scenario: Paid cycle advance requested
- **WHEN** a user requests a paid experiment after reviewing its estimate
- **THEN** the agent presents exact arguments for confirmation and preserves the tool's verified researcher and budget checks.

### Requirement: Honest benchmark discovery
The agent SHALL expose actual Qwen/Jev readiness and validate the producer's matching benchmark request before obtaining a concrete submit_experiment dry-run estimate. Confirmed launches SHALL advance that same recursive cycle with retained job/result lineage. Independent estimates and other benchmark families SHALL NOT authorize the launch.

#### Scenario: Configured benchmark preview
- **WHEN** a named benchmark review supplies a valid matching sandbox request
- **THEN** the agent invokes submit_experiment with dry_run=true, displays its estimate and approval requirements, and records no paid run.

#### Scenario: Benchmark confirmation and approval resume
- **WHEN** the user confirms a benchmark launch after its matching cycle preview and estimate
- **THEN** the agent invokes run_recursive_improvement with that cycle_id, dry_run=false and confirmed_by_user=true; later requests for an existing job awaiting compute approval resume read-only without another paid call.

#### Scenario: Unconfigured benchmark
- **WHEN** a user requests a benchmark that is not configured
- **THEN** the agent reports the capability evidence and does not invent a completed result or substitute a different model.

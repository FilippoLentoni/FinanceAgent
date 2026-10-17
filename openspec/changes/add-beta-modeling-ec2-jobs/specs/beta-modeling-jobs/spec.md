## ADDED Requirements

### Requirement: Independent durable EC2 jobs
The system SHALL run independently scheduled Codex snapshot, Codex research,
hourly multi-approach, New York daily closing and weekly comparison jobs on EC2.

#### Scenario: Snapshot is a real Codex invocation
- **WHEN** the minute timer fires
- **THEN** the installed headless Codex SHALL create a timestamped zero-byte file
- **AND** the wrapper SHALL verify its size and invocation completion
- **AND** no financial decision workflow SHALL run from the snapshot job

#### Scenario: Restart leaves hourly gaps visible
- **WHEN** the instance restarts after missing hourly slots
- **THEN** bounded recovery SHALL persist explicit missed statuses for each approach
- **AND** current evidence SHALL NOT be backdated into those slots

### Requirement: Exact public graph input capture
The beta machine comparison interface SHALL capture and durably persist actual
provider/tool public inputs before invoking them, independently of checkpoints.

#### Scenario: Capture fails before model invocation
- **WHEN** immutable input persistence fails
- **THEN** the provider/tool invocation SHALL NOT execute
- **AND** the response SHALL retain a capture-failed status

#### Scenario: Credentials cannot become evidence
- **WHEN** a captured payload contains credential-shaped fields or values
- **THEN** capture SHALL refuse execution without persisting the credential
- **AND** transport headers and hidden model reasoning SHALL NOT be captured

### Requirement: Beta release isolation
Deployment SHALL use explicit source revisions and stop before Gamma.

#### Scenario: Restore a held pipeline transition
- **WHEN** all Beta actions pass or the beta-only release fails
- **THEN** its execution SHALL be terminal before restoring the original Gamma transition
- **AND** no production approval SHALL be submitted

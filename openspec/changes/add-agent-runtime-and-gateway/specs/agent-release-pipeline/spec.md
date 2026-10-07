# Spec Delta

## Purpose

Defines the FinanceAgent CodePipeline under the shared pipeline standard: build once, promote the same artifacts through beta, gamma and prod, publish release manifests, run deployed MCP invocation tests in every environment, and roll back by release ID.

## ADDED Requirements

### Requirement: Pipeline stages per contract standard
FinanceAgent SHALL have its own pipeline with source, build and test, beta deploy and tests, gamma deploy and tests, manual approval, and prod deploy and smoke stages, in that order. A failing stage MUST stop promotion.

#### Scenario: Gamma MCP test fails
- **WHEN** a gamma deployed MCP invocation test fails
- **THEN** the pipeline stops before approval and prod is unchanged

### Requirement: Build-stage checks
The build stage SHALL run unit tests, graph tests with fake tools and the fixture provider, contract conformance in consumer mode for the pinned version, skill validation, policy tests, the identifier and secret leak scan, the live-financial permission scan, the ownership check, and IaC synthesis. It MUST produce a digest-addressed agent artifact and a `release_id`.

#### Scenario: Leak scan finds an ARN
- **WHEN** a commit adds a literal Lambda ARN to a configuration file
- **THEN** the build stage fails and nothing is deployed

### Requirement: Immutable promotion and release manifest
Beta, gamma and prod SHALL deploy the same agent artifact digest and synthesized assembly, with only environment configuration differing. Each deploy MUST write the FinanceAgent release manifest and `current-release-id` under `/finplan/<env>/financeagent/release/`.

#### Scenario: Digest equality
- **WHEN** release `rel_X` reaches prod
- **THEN** the prod manifest's `artifact_digest` equals the beta and gamma digests for `rel_X`

### Requirement: Deployed MCP invocation tests per environment
After each deployment the pipeline SHALL call the deployed Gateway over MCP with an environment test principal: `tools/list`, `describe_capabilities`, and a read of the synthetic portfolio's plan version. It MUST also invoke the hosted agent once and check that the same identifiers and checksums come back.

#### Scenario: Beta end-to-end
- **WHEN** platform, tool and agent phase 1 releases are present in beta
- **THEN** the MCP read and the agent answer report the same `plan_version_id` and checksum as a direct platform plan API read

#### Scenario: Prod smoke is read-only
- **WHEN** prod smoke tests run
- **THEN** they use only read-only tools against the synthetic portfolio and create no plan version, publication, execution or job

### Requirement: Gamma isolation verified
The gamma stage SHALL verify that every gamma Gateway target resolves to gamma Lambdas only and that a gamma principal cannot invoke prod targets.

#### Scenario: Cross-environment target
- **WHEN** a gamma target's Lambda reference does not match the gamma FinanceLambdasTool SSM value
- **THEN** the gamma isolation test fails and promotion stops

### Requirement: Rollback by release ID
The pipeline SHALL support redeploying a recorded `release_id`'s stored artifacts without rebuilding, re-registering the Gateway targets recorded in that release, and writing a manifest with `rolled_back_from`.

#### Scenario: Prod rollback
- **WHEN** prod smoke fails after `rel_Y`
- **THEN** operators redeploy `rel_X`, the Gateway target set returns to `rel_X`'s recorded set, and the manifest records `rolled_back_from` `rel_Y`

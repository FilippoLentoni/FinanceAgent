# Spec Delta

## Purpose

Packages reusable agent skills as versioned bundles. Each bundle has instructions, a declared tool allow-list bounded by the Gateway policy, and an output contract. The hosted agent loads them, and they can be exported for Claude Code and Codex users.

## ADDED Requirements

### Requirement: Versioned skill bundles
Each agent skill SHALL be a bundle in this repository with a unique name, semantic version, purpose, instructions, declared tool allow-list and expected output contract. The release MUST record the set of skill names and versions it ships.

#### Scenario: Skill inventory in describe
- **WHEN** a caller requests `describe` from the gamma agent
- **THEN** the response lists each skill's name and version, matching the gamma release manifest

### Requirement: Skill tool lists bounded by policy
A skill's tool allow-list SHALL only narrow what the Gateway policy permits for the caller. A skill MUST NOT grant access to tools, roles or arguments that the policy denies.

#### Scenario: Skill declares a disallowed tool
- **WHEN** a skill bundle declares a tool absent from the FinanceLambdasTool catalog or a live-execution tool
- **THEN** the build-stage skill validation fails and names the tool

#### Scenario: Caller lacks permission used by a skill
- **WHEN** a caller without the `plan_publisher` role runs a skill that lists `publish_plan_version`
- **THEN** the publish step is denied by the Gateway policy exactly as for a direct call

### Requirement: Phase 1 skill set
Phase 1 SHALL ship at least these skills: `capability-overview` (describe tools and environment), `plan-lookup` (read plans and versions with checksums) and `experiment-status` (submit dry-run estimates and track jobs). All of them MUST work against fixture-backed tools.

#### Scenario: Plan lookup skill in beta
- **WHEN** a user runs `plan-lookup` for the synthetic portfolio's current plan in beta
- **THEN** the agent returns the same `plan_version_id` and checksum as a direct platform plan API read

### Requirement: Skills exportable for direct MCP clients
The build SHALL produce an exportable form of each skill (instructions plus tool list) that Claude Code or Codex users can install for use with the Gateway. Exported skills MUST NOT contain environment identifiers, endpoints or credentials.

#### Scenario: Export hygiene
- **WHEN** the skill export is generated
- **THEN** the leak scan finds no account IDs, ARNs, Gateway IDs or secret values, and the endpoint appears only as a placeholder resolved from configuration

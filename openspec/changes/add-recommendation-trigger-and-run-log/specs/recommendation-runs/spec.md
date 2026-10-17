# Spec Delta

## Purpose

Generate saved-portfolio recommendations from the cloud on a schedule or on demand, and keep a durable, queryable log of every run with the inputs it consumed and the decisions it produced.

## ADDED Requirements

### Requirement: Scheduled recommendation runs
When enabled for an environment, the system SHALL start recommendation runs on a configured schedule without any developer-desktop process. Each run SHALL request PPO, minimum variance, mean variance and scenario CVaR recommendations for every configured saved paper portfolio from the deployed agent runtime. The beta default schedule SHALL be hourly and SHALL be changeable by configuration without code changes. The feature SHALL be disabled outside beta unless explicitly enabled.

#### Scenario: Hourly beta run
- **WHEN** the hourly schedule fires in beta
- **THEN** one run is recorded for each configured portfolio with a status for each of the four methods

#### Scenario: Disabled environment
- **WHEN** the configuration flag is off for an environment
- **THEN** no schedule, function or run log is deployed for that environment

### Requirement: On-demand recommendation runs
An authorized operator SHALL be able to start a run at any time with their own AWS credentials, optionally naming portfolios and methods, and SHALL receive the run id and per-method outcome. The caller SHALL NOT need a browser-issued agent token.

#### Scenario: Operator asks for a fresh recommendation
- **WHEN** the operator invokes the trigger on demand for a portfolio
- **THEN** a new run with trigger type `on_demand` and a unique run id is recorded and returned

### Requirement: Read-only advisory behavior
Runs SHALL only request recommendations and record results. They SHALL NOT confirm or resolve decisions, change holdings, place orders, train or promote models, or refresh market data. They SHALL authenticate to the agent runtime with the existing read-only machine client and SHALL never persist or log credentials.

#### Scenario: Credential safety
- **WHEN** a run fails during authentication or invocation
- **THEN** the run is recorded as failed with an error code and no token or secret appears in the run log or logs

### Requirement: Formation timing honored
Runs SHALL use the existing decision-cutoff rules. A request before the 09:00 America/New_York formation SHALL be recorded with each news-driven method's explicit not-due/unavailable status rather than retried with other inputs. A request after formation SHALL record which formation, news window and completed price session were used, so repeated runs on the same morning are identifiable as reusing the same inputs. Closed-market runs SHALL be labeled as such.

#### Scenario: Run before formation
- **WHEN** a scheduled run starts at 08:00 America/New_York
- **THEN** news-driven methods are recorded as not due and no substitute policy is reported

#### Scenario: Repeated runs after formation
- **WHEN** two runs occur at 10:00 and 11:00 on the same day
- **THEN** both records show the same formation timestamp and news source identity

### Requirement: Duplicate and volume protection
The system SHALL record at most one scheduled run per portfolio per schedule window, including under retries, and SHALL refuse new runs beyond a configured daily cap with an explicit `cap_reached` outcome. On-demand runs SHALL count toward the cap.

#### Scenario: Retried schedule event
- **WHEN** the same schedule window is delivered twice
- **THEN** only one run record exists for that portfolio and window

#### Scenario: Daily cap reached
- **WHEN** the configured daily cap has been reached
- **THEN** a further request returns `cap_reached` and no agent invocation is made

### Requirement: Durable run log with inputs and outputs
Every run SHALL produce one retained central record with its trigger, timing and overall status, and, per portfolio and method, the outcome, the consumed inputs and the produced outputs. Records SHALL reference full decisions by decision id rather than duplicating them.

#### Scenario: Inputs and outputs captured per method
- **WHEN** a method returns a decision
- **THEN** the record holds its decision id, input snapshot id, completed price session, news source day/window/checksum and quality (if used), model export id and artifact checksum (if applicable), portfolio revision, target-weight summary, a bounded narrative excerpt, claim-check result and usage/cost

#### Scenario: Successful run recorded
- **WHEN** a run completes with four issued decisions
- **THEN** its record lists four decision ids with their input identities and the overall status `completed`

#### Scenario: Partial run recorded
- **WHEN** one method fails and three succeed
- **THEN** the record status is `partial` and the failing method keeps its error code

### Requirement: Querying runs
The operator SHALL be able to list the most recent N runs (newest first) and filter by portfolio, method, trigger type and date range, and fetch one run by id. The hosted agent SHALL answer requests such as "show me the latest five recommendations" from the run log, returning run ids and decision ids that can then be used for existing explain, compare and evaluate requests. Query access SHALL be read-only.

#### Scenario: Latest five
- **WHEN** a user asks the agent for the latest five recommendations
- **THEN** the answer lists up to five most recent runs or decisions with their ids, methods, timestamps and statuses

#### Scenario: Follow-up by id
- **WHEN** the user then asks to explain one of the listed decision ids
- **THEN** the existing explanation path is used with that id

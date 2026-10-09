# Bootstrap runbook (FinanceAgent)

Task 7.2 of `add-agent-runtime-and-gateway` (design D7, Migration Plan step 2; contracts D6, D11, D12,
D16; CONTRACT GAP-3 ordering). It mirrors the deployed FinancialPlanning and FinanceModel bootstraps.

> **Do not run the bootstrap during implementation work.** A human runs it once, after this IaC is
> synthesized. Afterwards only the scoped roles it creates deploy anything; every environment stack is
> deployed by the pipeline, never by the bootstrap identity.

## Approval status

- The user approved the one-time bootstrap **in principle on 2026-10-07**.
- `scripts/bootstrap.py` refuses to start without a synthesized cloud assembly, prints the **exact
  stacks** with their resource types and a **monthly cost estimate** priced from the AWS Price List API
  at run time (no price is written in this repository), and deploys only after the operator types
  `deploy`.

## Prerequisites

1. **The FinancialPlanning bootstrap has run.** FinanceAgent reuses, and never creates:
   the permission boundaries `finplan-<env>-permission-boundary` and `finplan-shared-permission-boundary`;
   the project budget (USD 50), its alerts and 100% deny action; the default allocation
   `/finplan/shared/financialplanning/config/budget-allocation` (`bedrock_explanations` 5); the
   connection reference `/finplan/shared/financialplanning/config/codeconnection-ref`.
2. **AWS credentials** for `us-east-2` from the default chain (the DevDesktop instance role works).
3. **Local untracked configuration**, read exactly like the platform bootstrap reads it: `--config PATH`,
   else `$FINPLAN_BOOTSTRAP_CONFIG`, else the shared **`~/.finplan/bootstrap.json`**. Only `account_id`,
   `primary_region` and `codeconnection_arn` are used; platform-only keys are ignored and never printed.
   An optional overlay `~/.finplan/financeagent-bootstrap.json` and `FINPLAN_ACCOUNT_ID` /
   `FINPLAN_PRIMARY_REGION` / `FINPLAN_CODECONNECTION_ARN` override them. A file inside the repository is
   refused. Without `codeconnection_arn` the **existing** platform connection is reused (read-only).
4. **Toolchain:** `uv` (Python 3.12) and Node.js for `npx aws-cdk@2`.

## What the bootstrap deploys

Exactly two account-level stacks (environment `shared`):

| Stack | Contents |
|---|---|
| `finplan-shared-financeagent-pipeline-store` | Store bucket `finplan-shared-financeagent-pipeline-store-<account-id>`: SSE-S3, TLS only, Block Public Access, versioned, retained. Pipeline artifacts, the release ledger (`releases/`), the staged tooling template (`bootstrap/`). Legacy synthesizer: no CDK bootstrap role or bucket |
| `finplan-shared-financeagent-tooling` | The Runtime image repository `finplan-shared-financeagent-runtime-images` (immutable tags, scan on push, newest 10 release images); the **Gateway service roles** `finplan-<env>-financeagent-gateway-service-role` (one per environment, tagged with it, its boundary, invoke only that environment's FinanceLambdasTool Lambdas, evaluate its policy engine); the pipeline `finplan-shared-financeagent-pipeline` with its scoped roles, four CodeBuild projects (ARM64 build, one stage project per environment) and their `/aws/codebuild/<project>` log groups (30 days). Staged in the store under `bootstrap/` (template above the inline limit) |

Neither stack needs `CDKToolkit`; the bootstrap refuses an assembly that references `cdk-hnb659fds`
roles or `cdk-*-assets` buckets or carries container-image assets (lesson L1).

### Updating an existing pipeline's tool wiring

The pipeline does not update itself. When the pinned catalog adds tools, its shared Tooling stack
must also be refreshed: the stage CodeBuild projects export the target variables and the pipeline
passes each one as a CloudFormation parameter. Deploying only a new Agent template leaves new
parameters at `none`, even if Resolve found their Lambda references.

For an existing installation, synthesize offline, compare the Tooling template with the deployed
template, and update only `finplan-shared-financeagent-tooling` through a reviewed CloudFormation
change set. This is a control-plane update, separate from the one-time bootstrap; do not rerun the
bootstrap or directly deploy workload stacks. Stop the operator's pending executions first, hold
Beta/Gamma transitions, preserve existing stack parameters, and verify the barriers after update.
Then release the exact source commit through Beta. `RestartExecutionOnUpdate` stays false. The
PublishRelease action refuses to publish if deployed target parameters differ from resolved targets.
Restore the recorded transition states only after stopping the beta-only execution before Gamma.

Scoped roles created:

| Role | Tags / boundary | Used by |
|---|---|---|
| `finplan-shared-financeagent-pipeline-role` | shared / shared | CodePipeline |
| `finplan-shared-financeagent-pipeline-build-project-role` | shared / shared | Build: gates, synth, ARM64 image push, ledger (no Bedrock) |
| `finplan-shared-financeagent-deploy-role-<env>` | `<env>` / `<env>` | Deploy actions |
| `finplan-shared-financeagent-deploy-role-<env>-exec` | `<env>` / `<env>` | CloudFormation: this environment's pool, secret, AgentCore resources and `finplan-<env>-financeagent-*` roles (environment boundary only) |
| `finplan-<env>-financeagent-pipeline-stage-role` | `<env>` / `<env>` | Resolve, publish, deployed suites (no Bedrock invoke, no direct Lambda invoke) |
| `finplan-<env>-financeagent-gateway-service-role` | `<env>` / `<env>` | AgentCore Gateway of `<env>` |

## Steps

```sh
uv run python scripts/synth.py --out cdk.out                                   # 1. offline synth
AWS_REGION=us-east-2 uv run python scripts/bootstrap.py --assembly cdk.out     # 2. interactive, a human step
```

| # | Step | Stops when |
|---|---|---|
| 1 | Assembly: copy only the two account-level stacks into `cdk.out.bootstrap/` and check them | No assembly, missing stacks, CDK bootstrap references |
| 2 | Pre-run plan: stacks, resource types, estimate (Price List API) | n/a (read-only) |
| 3 | Caller account, region, connection `AVAILABLE`, scoped deploy roles | Any mismatch |
| 4 | Confirmation: the operator types `deploy` | Anything else; nothing deployed |
| 5 | Writes `/finplan/shared/financeagent/config/codeconnection-ref` | n/a |
| 6 | `npx aws-cdk@2 deploy --all` of `cdk.out.bootstrap/`; reports (read-only) the shared allocation; then, as the `bootstrap` writer: `/finplan/<env>/financeagent/agent/gateway-principal-ref` for beta, gamma, prod (role NAME; before FinanceLambdasTool's Gateway-facing release, GAP-3) and `/finplan/shared/financeagent/config/budget-enforced-role-names`; reports whether each environment's configured explanation inference profile is ACTIVE (read-only) | A CloudFormation failure (automatic rollback) |
| 7 | Source-stage dry run of `FilippoLentoni/FinanceAgent` `main` | Source cannot fetch: extend the GitHub App installation of that connection, rerun |

## After the bootstrap

1. **Re-run the FinanceLambdasTool pipeline** so its pre-deploy step reads the new
   `gateway-principal-ref` and grants the Gateway role invoke on the tool aliases.
2. **First FinanceAgent pipeline run** (beta -> gamma -> approval -> prod). Each stage:
   `Resolve` (FinanceLambdasTool compatibility gate + target resolution; stops with "dependency missing"
   until a FinanceLambdasTool release exists in that environment), `DeployIdentity`, `DeployAgent`,
   `PublishRelease`, the deployed suite with REAL payloads.
3. **Project owner user** (task 7.2a, administrator action in your session, per environment after its
   first identity deploy; no user data in files):
   ```sh
   POOL=$(aws ssm get-parameter --name /finplan/beta/financeagent/agent/user-pool-ref --query Parameter.Value --output text)
   aws cognito-idp admin-create-user --user-pool-id "$POOL" --username <your-email> --user-attributes Name=email,Value=<your-email> Name=email_verified,Value=true
   aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username <your-email> --group-name plan_publisher
   ```
4. **Budget enforcement:** re-run the FinancialPlanning bootstrap after the first beta deploy, so its 100%
   deny covers `/finplan/<env>/financeagent/config/budget-enforced-role-names` (Runtime role first, Gateway
   role, deploy/exec/stage roles) and the shared tooling roles. FinanceAgent never creates a budget.
5. **Bedrock model access** (tasks 3.8/3.9, human console step): enable access for the configured model
   (`config/<env>.json` `explanation.model_id`), confirm the bootstrap reported the profile ACTIVE, enter
   current per-1k rates (from AWS pricing, `source: configured`, `retrieved_at`) into
   `/finplan/<env>/financeagent/config/explanation-guards` `rates`, then switch `explanation.provider` to
   `bedrock` for gamma (later prod) in a config-only release. The `Resolve` action then grants the Runtime
   role `InvokeModel`/`InvokeModelWithResponseStream` on exactly that inference profile and its routed US
   foundation models. Beta stays `fixture`.
6. **Pipeline changes** (the tooling stack itself) deploy only by re-running the bootstrap.

## Teardown

Only when FinanceAgent is retired: delete `finplan-<env>-financeagent-agent`, then
`finplan-<env>-financeagent-identity` (prod pool has deletion protection; retained), then the tooling and
store stacks (termination protection; disable it first). The store bucket and image repository are retained.

## Never

- Commit the account ID, connection ARN, pool/client IDs or any ARN.
- Deploy environment stacks with the bootstrap identity.
- Create a budget, an allocation or a permission boundary from FinanceAgent.
- Run the agent against Bedrock from a workstation.

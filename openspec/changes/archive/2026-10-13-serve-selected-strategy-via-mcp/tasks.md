# Tasks

## 1. Implementation and local verification

- [x] 1.1 Package and load the recommendation skill, including manifest version and instruction checksum; verify container/build packaging and runtime inventory tests.
- [x] 1.2 Wire structured and natural-language recommendation requests to the read tool and preserve full allocations/provenance in the final answer; verify graph tests and missing-state/denial paths.
- [x] 1.3 Set the MCP client deadline to 330 seconds, document invocation and verify timeout configuration plus narration-budget regression tests.

- [x] 1.4 Enable guarded Bedrock for the hosted beta agent while preserving hermetic CI fixtures, validate provider/IAM configuration and document the four-pipeline strategy lifecycle.

## 2. Deployed beta verification

- [x] 2.1 Deploy beta through the existing pipeline and verify one hosted, authenticated recommendation uses the selected policy and records caller tool access without creating a job or changing production selection.

## Workflow follow-up

- Review before archiving; existing explanation and gamma/prod tasks retain their own acceptance criteria.

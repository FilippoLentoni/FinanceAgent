"""Cloud-triggered recommendation runs and their run log (OpenSpec add-recommendation-trigger-and-run-log).

:mod:`.runlog` (item builder, dedupe, daily cap, statuses) and :mod:`.trigger` (the Lambda handler)
are stdlib + boto3 only: ``infra/stacks/recommendation_runs.py`` bundles both into the inline code of
``finplan-<env>-financeagent-recommendation-trigger``. Import nothing else from the package here.
"""

"""Approve and sign an exact testnet research request using separated execution credentials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "agent")]
from finplan_agent.payments.execution import PrivateLedger
from scripts.research_payments import budget_preflight


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["approve", "sign", "delivery"])
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--challenge", type=Path)
    ap.add_argument("--resource")
    ap.add_argument("--allow-host", action="append", default=[])
    ap.add_argument("--request-body", type=Path)
    ap.add_argument("--response-file", type=Path)
    ap.add_argument("--payment-id")
    ap.add_argument("--resume", action="store_true", help="Explicitly reconcile an unknown result using the SAME payment token")
    args = ap.parse_args()
    import boto3
    from botocore.config import Config
    state = json.loads(args.state.read_text())
    session = boto3.Session(region_name="us-east-2")
    ledger = PrivateLedger(args.state.parent / "payment-receipts")
    body = json.loads(args.request_body.read_text()) if args.request_body else None
    if args.action == "approve":
        if not args.challenge or not args.resource or not args.allow_host: ap.error("approve requires --challenge, --resource and an operator-reviewed --allow-host")
        budget_preflight(session)
        identifier = ledger.approve(challenge=json.loads(args.challenge.read_text()), resource_url=args.resource, allowed_hosts=set(args.allow_host),
            manager_arn=state["manager_arn"], session_id=state["session_id"], instrument_id=state["instrument_id"], user_id=state["user_id"],
            approved_by=session.client("sts").get_caller_identity()["Arn"], request_body=body)
        print(json.dumps({"payment_id": identifier, "status": "approved", "paid": False})); return
    if not args.payment_id: ap.error("--payment-id is required")
    if args.action == "delivery":
        if not args.resource or not args.response_file: ap.error("delivery requires --resource and --response-file")
        print(json.dumps(ledger.record_delivery(args.payment_id, resource_url=args.resource, data=args.response_file.read_bytes()))); return
    credentials = session.client("sts").assume_role(RoleArn=state["roles"]["ExecutionRoleArn"], RoleSessionName="research-payment-execution")["Credentials"]
    execution = boto3.Session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"], aws_session_token=credentials["SessionToken"], region_name="us-east-2")
    client = execution.client("bedrock-agentcore", config=Config(retries={"total_max_attempts": 1}))
    print(json.dumps(ledger.execute(args.payment_id, client=client, project_preflight=lambda: budget_preflight(session), request_body=body, resume=args.resume)))


if __name__ == "__main__": main()

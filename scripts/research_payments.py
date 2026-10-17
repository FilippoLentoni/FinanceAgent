"""Independent beta AgentCore Payments bootstrap. No hosted-runtime grants or mainnet payments."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from finplan_agent.payments.policies import MANAGER_NAME, STACK_NAME, role_template


def private_write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink(): raise ValueError("state cannot be a symlink")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out: json.dump(value, out, indent=2, default=str); out.write("\n")
    os.replace(tmp, path); os.chmod(path, 0o600)


def operator_role(arn):
    if ":assumed-role/" in arn:
        prefix, role = arn.split(":assumed-role/", 1)
        return prefix.replace(":sts:", ":iam:") + ":role/" + role.rsplit("/", 1)[0]
    if ":iam:" not in arn: raise ValueError("unsupported operator identity")
    return arn


def budget_preflight(session):
    ssm = session.client("ssm"); ceiling = float(ssm.get_parameter(Name="/finplan/shared/financialplanning/config/cost-ceiling-usd")["Parameter"]["Value"])
    account = session.client("sts").get_caller_identity()["Account"]
    b = session.client("budgets", region_name="us-east-1").describe_budget(AccountId=account, BudgetName="finplan-shared-financialplanning-project-budget")["Budget"]
    spent = float(b["CalculatedSpend"]["ActualSpend"]["Amount"])
    limit = min(100., ceiling, float(b["BudgetLimit"]["Amount"]))
    if not all(math.isfinite(v) for v in (ceiling, spent, limit)) or not (0 < limit <= 100 and spent >= 0 and spent + 5 <= limit): raise ValueError("research-payment setup lacks USD 5 project headroom")
    return {"project_limit_usd": limit, "reported_spend_usd": spent, "billing_lag_applies": True, "setup_cap_usd": 5, "session_cap_usd": 1, "network": "eip155:84532", "mainnet_enabled": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["plan", "prepare", "connect", "status", "discover", "instrument", "session"])
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--query", default="financial market news earnings")
    ap.add_argument("--email", help="Wallet owner email, only for the instrument action; retained privately")
    args = ap.parse_args()
    import boto3
    session = boto3.Session(region_name="us-east-2")
    if args.action == "discover":
        from finplan_agent.payments.discovery import discover
        result = discover(args.query); private_write(args.state.parent / "discovery.json", result)
        print(json.dumps({"status": "discovery_only", "paid": False, "evidence_file": str(args.state.parent / "discovery.json")})); return
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    if args.action in ("instrument", "session"):
        state["budget"] = budget_preflight(session)
        if not state.get("manager_arn") or state.get("connector_status") != "READY":
            raise ValueError("Complete Coinbase connection before wallet/session setup")
        control = session.client("bedrock-agentcore-control")
        connector = control.get_payment_connector(paymentManagerId=state["manager_id"], paymentConnectorId=state["connector_id"])
        if connector["status"] != "READY": raise ValueError("Coinbase connector is not ready")
        credentials = session.client("sts").assume_role(RoleArn=state["roles"]["ManagementRoleArn"], RoleSessionName="research-payment-management")["Credentials"]
        management = boto3.Session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"], aws_session_token=credentials["SessionToken"], region_name="us-east-2").client("bedrock-agentcore")
        if args.action == "instrument" and not state.get("instrument_id"):
            if not args.email or "@" not in args.email or len(args.email) > 254: raise ValueError("instrument requires a valid wallet owner --email")
            state["user_id"] = "research-" + hashlib.sha256(args.email.strip().lower().encode()).hexdigest()[:20]
            state.setdefault("instrument_token", str(uuid.uuid4())); private_write(args.state, state)
            result = management.create_payment_instrument(paymentManagerArn=state["manager_arn"], paymentConnectorId=state["connector_id"], userId=state["user_id"],
                paymentInstrumentType="EMBEDDED_CRYPTO_WALLET", paymentInstrumentDetails={"embeddedCryptoWallet": {"network": "ETHEREUM", "linkedAccounts": [{"email": {"emailAddress": args.email}}]}}, clientToken=state["instrument_token"])["paymentInstrument"]
            state.update(instrument_id=result["paymentInstrumentId"], instrument_status=result["status"], wallet_setup=result["paymentInstrumentDetails"])
        if args.action == "session":
            if not state.get("instrument_id"): raise ValueError("create and activate the test wallet first")
            instrument = management.get_payment_instrument(paymentManagerArn=state["manager_arn"], paymentInstrumentId=state["instrument_id"], userId=state["user_id"])["paymentInstrument"]
            if instrument["status"] != "ACTIVE": raise ValueError("wallet user consent/activation is pending")
            state["instrument_status"] = instrument["status"]
            if not state.get("session_id"):
                state.setdefault("session_token", str(uuid.uuid4())); private_write(args.state, state)
                result = management.create_payment_session(paymentManagerArn=state["manager_arn"], userId=state["user_id"],
                    limits={"maxSpendAmount": {"value": "1.00", "currency": "USD"}}, expiryTimeInMinutes=15, clientToken=state["session_token"])["paymentSession"]
                state["session_id"] = result["paymentSessionId"]
        private_write(args.state, state)
        print(json.dumps({"instrument_status": state.get("instrument_status"), "session_created": bool(state.get("session_id")), "network": "Base Sepolia only", "mainnet_enabled": False, "wallet_setup_retained_privately": bool(state.get("wallet_setup"))})); return
    if args.action in ("plan", "prepare", "connect"):
        state["budget"] = budget_preflight(session)
        caller = session.client("sts").get_caller_identity()["Arn"]
        template = role_template(operator_role(caller))
        private_write(args.state.parent / "roles-template.json", template)
        if args.action == "plan":
            print(json.dumps({"status": "plan", "budget": state["budget"], "template": str(args.state.parent / "roles-template.json"), "aws_changes": False})); return
        cfn = session.client("cloudformation")
        kwargs = dict(StackName=STACK_NAME, TemplateBody=json.dumps(template), Capabilities=["CAPABILITY_NAMED_IAM"],
                      Tags=[{"Key": k, "Value": v} for k, v in {"project": "finplan", "owner-repo": "financeagent", "environment": "beta", "logical-role": "research-payments"}.items()])
        try: stack = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
        except cfn.exceptions.ClientError as exc:
            if "does not exist" not in str(exc): raise
            cfn.create_stack(**kwargs); stack = {"StackStatus": "CREATE_IN_PROGRESS"}
        if stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            existing = cfn.get_template(StackName=STACK_NAME)["TemplateBody"]
            if isinstance(existing, str): existing = json.loads(existing)
            if existing != template:
                cfn.update_stack(**kwargs); stack = {"StackStatus": "UPDATE_IN_PROGRESS"}
        deadline = time.monotonic() + 300
        while stack["StackStatus"].endswith("_IN_PROGRESS"):
            if time.monotonic() > deadline: raise RuntimeError("role stack is still creating; resume prepare")
            time.sleep(3); stack = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
        if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"): raise RuntimeError("payment role stack failed; inspect CloudFormation")
        state["roles"] = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
        control = session.client("bedrock-agentcore-control")
        if not state.get("manager_id"):
            managers = control.list_payment_managers()["paymentManagers"]
            found = next((m for m in managers if m["name"] == MANAGER_NAME), None)
            if found is not None and found["roleArn"] != state["roles"]["ServiceRoleArn"]: raise RuntimeError("owned manager name points to another role")
            if found is None:
                found = control.create_payment_manager(name=MANAGER_NAME, authorizerType="AWS_IAM", roleArn=state["roles"]["ServiceRoleArn"], clientToken=str(uuid.uuid5(uuid.NAMESPACE_URL, MANAGER_NAME + "-v1")), tags={"project": "finplan", "owner-repo": "financeagent", "environment": "beta"})
            state.update(manager_id=found["paymentManagerId"], manager_arn=found["paymentManagerArn"])
            private_write(args.state, state)
        deadline = time.monotonic() + 180
        manager = control.get_payment_manager(paymentManagerId=state["manager_id"])
        while manager["status"] in ("CREATING", "UPDATING"):
            if time.monotonic() > deadline: raise RuntimeError("manager not ready; resume prepare")
            time.sleep(3); manager = control.get_payment_manager(paymentManagerId=state["manager_id"])
        state["manager_status"] = manager["status"]
        if manager["status"] != "READY": private_write(args.state, state); raise RuntimeError("payment manager not READY")
        if args.action == "connect" and not state.get("connector_id"):
            try:
                candidates = [x for x in control.list_payment_connectors(paymentManagerId=state["manager_id"])["paymentConnectors"] if x["type"] == "CoinbaseCDP"]
                if len(candidates) > 1: raise ValueError("multiple Coinbase connectors require operator selection")
                result = candidates[0] if candidates else control.create_payment_connector(paymentManagerId=state["manager_id"], name="CoinbaseResearch", type="CoinbaseCDP", credentialProviderConfigurations=[], provisionMode="QUICK_CREATE", clientToken=str(uuid.uuid5(uuid.NAMESPACE_URL, "finplan-beta-coinbase-quick-create-v1")))
                state.update(connector_id=result["paymentConnectorId"], connector_status=result["status"], authorization_url=result.get("authorizationUrl"))
                state.pop("setup_error", None)
            except control.exceptions.ClientError as exc:
                state["setup_error"] = exc.response["Error"]["Code"]
                private_write(args.state, state)
                print(json.dumps({"status": "setup_prerequisite_required", "code": state["setup_error"], "manager_status": state["manager_status"], "payments_enabled": False})); return
        private_write(args.state, state)
    if args.action in ("status", "connect") and state.get("manager_id"):
        control = session.client("bedrock-agentcore-control")
        state["manager_status"] = control.get_payment_manager(paymentManagerId=state["manager_id"])["status"]
        if state.get("connector_id"):
            state["connector_status"] = control.get_payment_connector(paymentManagerId=state["manager_id"], paymentConnectorId=state["connector_id"])["status"]
            if state["connector_status"] in ("READY", "AUTHENTICATION_EXPIRED", "AUTHENTICATION_FAILED"):
                state.pop("authorization_url", None)
        private_write(args.state, state)
    print(json.dumps({k: state.get(k) for k in ("manager_status", "connector_status", "setup_error", "authorization_url", "budget")}))


if __name__ == "__main__": main()

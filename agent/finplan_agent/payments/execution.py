"""Operator-only x402 testnet signing with durable request binding and no automatic retry.

This module never creates sessions, funds wallets, follows URLs or changes policy.
Payment signing and merchant data delivery are deliberately separate receipts.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

NETWORK = "eip155:84532"
# Circle's canonical Base Sepolia USDC contract, verified 2026-10-13.
ASSET = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
MAX_ATOMIC_AMOUNT = 100000  # 0.10 test USDC per request; six decimal places.


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_challenge(challenge, *, resource_url, allowed_hosts):
    url = urlsplit(resource_url)
    if url.scheme != "https" or url.hostname not in allowed_hosts or url.username or url.password or url.fragment or url.port not in (None, 443):
        raise ValueError("research endpoint is not explicitly allowlisted HTTPS")
    if challenge.get("x402Version") != 2 or challenge.get("resource", {}).get("url") != resource_url:
        raise ValueError("challenge version or resource binding mismatch")
    accepts = challenge.get("accepts")
    if not isinstance(accepts, list) or len(accepts) != 1:
        raise ValueError("exactly one approved payment option is required")
    option = accepts[0]
    if option.get("network") != NETWORK or option.get("scheme") != "exact" or str(option.get("asset", "")).lower() != ASSET:
        raise ValueError("only exact Base Sepolia test USDC is supported")
    amount = option.get("amount")
    if not isinstance(amount, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", amount) or int(amount) > MAX_ATOMIC_AMOUNT:
        raise ValueError("payment amount is invalid or exceeds the per-request cap")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", str(option.get("payTo", ""))) or int(option["payTo"][2:], 16) == 0:
        raise ValueError("invalid payment recipient")
    timeout = option.get("maxTimeoutSeconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("payment proof timeout must be 1–300 seconds")
    extra = option.get("extra") or {}
    if extra.get("assetTransferMethod", "eip3009") != "eip3009":
        raise ValueError("token allowance or unsupported transfer method is not authorized")
    # Keep the exact approved payload, but reject unexpected transfer mechanisms.
    return copy.deepcopy(challenge)


class PrivateLedger:
    def __init__(self, directory):
        self.directory = Path(directory)
        if self.directory.is_symlink(): raise ValueError("ledger cannot be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    @contextmanager
    def lock(self):
        fd = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def path(self, identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier): raise ValueError("invalid payment identity")
        return self.directory / (identifier + ".json")

    def read(self, identifier):
        path = self.path(identifier)
        if path.is_symlink(): raise ValueError("receipt cannot be a symlink")
        return json.loads(path.read_text())

    def write(self, identifier, record):
        path = self.path(identifier); tmp = self.directory / (uuid.uuid4().hex + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2, default=str, allow_nan=False); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(self.directory, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)

    def approve(self, *, challenge, resource_url, allowed_hosts, manager_arn, session_id, instrument_id, user_id, approved_by, request_body=None):
        payload = validate_challenge(challenge, resource_url=resource_url, allowed_hosts=allowed_hosts)
        if not re.fullmatch(r"arn:aws:bedrock-agentcore:us-east-2:[0-9]{12}:payment-manager/finplanbetaresearch-[a-z0-9]+", manager_arn):
            raise ValueError("unexpected payment manager")
        if not all(isinstance(v, str) and v.strip() for v in (session_id, instrument_id, user_id, approved_by)):
            raise ValueError("approval requires exact session, instrument, user and operator identities")
        identifier = uuid.uuid4().hex
        approved = {"challenge": payload, "resource_url": resource_url, "request_body_checksum": digest(request_body),
                    "manager_arn": manager_arn, "session_id": session_id, "instrument_id": instrument_id, "user_id": user_id,
                    "approved_by": approved_by, "approved_at": datetime.now(UTC).isoformat()}
        with self.lock():
            self.write(identifier, {"approved": approved, "approval_checksum": digest(approved), "status": "approved", "payment_id": identifier, "data_delivery_status": "not_requested"})
        return identifier

    def execute(self, identifier, *, client, project_preflight, request_body=None, resume=False):
        with self.lock():
            record = self.read(identifier); approved = record["approved"]
            if digest(approved) != record["approval_checksum"] or digest(request_body) != approved["request_body_checksum"]:
                raise ValueError("approved payment inputs changed")
            if record["status"] in ("PROOF_GENERATED",):
                return self.public_receipt(record)
            if record["status"] != "approved" and not resume:
                raise ValueError("payment outcome requires explicit reconciliation; no automatic retry")
            if record["status"] not in ("approved", "outcome_unknown", "processing"):
                raise ValueError("terminal payment failure requires investigation")
            validate_challenge(approved["challenge"], resource_url=approved["resource_url"], allowed_hosts={urlsplit(approved["resource_url"]).hostname})
            project_preflight()
            kwargs = {"paymentManagerArn": approved["manager_arn"], "userId": approved["user_id"]}
            session = client.get_payment_session(**kwargs, paymentSessionId=approved["session_id"])["paymentSession"]
            instrument = client.get_payment_instrument(**kwargs, paymentInstrumentId=approved["instrument_id"])["paymentInstrument"]
            cap = session.get("limits", {}).get("maxSpendAmount", {})
            amount = Decimal(cap.get("value", "NaN"))
            if cap.get("currency") != "USD" or not amount.is_finite() or not Decimal(0) < amount <= Decimal(1):
                raise ValueError("session must have an explicit positive limit no greater than USD 1")
            for obj, key, value in ((session, "paymentSessionId", approved["session_id"]), (instrument, "paymentInstrumentId", approved["instrument_id"])):
                if obj.get(key) != value or obj.get("paymentManagerArn") != approved["manager_arn"] or obj.get("userId") != approved["user_id"]:
                    raise ValueError("session or instrument ownership mismatch")
            if instrument.get("status") != "ACTIVE": raise ValueError("wallet consent/activation is not complete")
            created = session["createdAt"]
            if isinstance(created, str): created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created.tzinfo is None or created + timedelta(minutes=session["expiryTimeInMinutes"]) <= datetime.now(UTC):
                raise ValueError("payment session expired")
            attempts = sum(json.loads(path.read_text()).get("attempts", 0) for path in self.directory.glob("*.json"))
            if attempts >= 10: raise ValueError("research round signing-attempt cap exhausted")
            record["attempts"] = record.get("attempts", 0) + 1
            record["status"] = "processing"; self.write(identifier, record)
            try:
                response = client.process_payment(**kwargs, paymentSessionId=approved["session_id"], paymentInstrumentId=approved["instrument_id"],
                    paymentType="CRYPTO_X402", paymentInput={"cryptoX402": {"version": "2", "payload": approved["challenge"]}}, clientToken=str(uuid.UUID(hex=identifier)))
            except Exception:
                record["status"] = "outcome_unknown"; self.write(identifier, record); raise
            record.update(status=response["status"], process_payment_id=response["processPaymentId"], private_payment_response=response)
            self.write(identifier, record)
            return self.public_receipt(record)

    @staticmethod
    def public_receipt(record):
        return {k: record.get(k) for k in ("payment_id", "status", "process_payment_id", "approval_checksum", "data_delivery_status")}

    def record_delivery(self, identifier, *, resource_url, data):
        with self.lock():
            record = self.read(identifier)
            if resource_url != record["approved"]["resource_url"] or record["status"] not in ("PROOF_GENERATED",):
                raise ValueError("delivery does not match a successful approved signing")
            if not isinstance(data, bytes) or not data: raise ValueError("empty or invalid data cannot be marked delivered")
            checksum = "sha256:" + hashlib.sha256(data).hexdigest()
            if record.get("data_checksum") not in (None, checksum): raise ValueError("delivery receipt is immutable")
            record.update(data_delivery_status="received_unvalidated", data_checksum=checksum, data_bytes=len(data))
            self.write(identifier, record)
            return {**self.public_receipt(record), "data_checksum": checksum, "data_bytes": len(data), "note": "Receipt does not establish data quality or historical training eligibility."}

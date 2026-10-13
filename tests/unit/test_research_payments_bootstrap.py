from types import SimpleNamespace

import pytest

from scripts.research_payments import budget_preflight, operator_role, private_write


def test_operator_identity_preserves_assumed_role_path():
    assert operator_role("arn:aws:sts::<account-id>:assumed-role/team/operator/session") == "arn:aws:iam::<account-id>:role/team/operator"


def budget_session(ceiling="100", spent="12", limit="100"):
    return SimpleNamespace(client=lambda name, **kwargs: {
        "ssm": SimpleNamespace(get_parameter=lambda **kw: {"Parameter": {"Value": ceiling}}),
        "sts": SimpleNamespace(get_caller_identity=lambda: {"Account": "<account-id>"}),
        "budgets": SimpleNamespace(describe_budget=lambda **kw: {"Budget": {"BudgetLimit": {"Amount": limit}, "CalculatedSpend": {"ActualSpend": {"Amount": spent}}}}),
    }[name])


@pytest.mark.parametrize("ceiling,spent,limit", [("NaN", "12", "100"), ("100", "NaN", "100"), ("100", "96", "100"), ("50", "48", "100"), ("-1", "0", "100")])
def test_setup_refuses_unknown_or_exhausted_project_budget(ceiling, spent, limit):
    with pytest.raises(ValueError): budget_preflight(budget_session(ceiling, spent, limit))


def test_budget_raise_does_not_relax_authorized_total_and_private_state_is_private(tmp_path):
    assert budget_preflight(budget_session("200"))["project_limit_usd"] == 100
    target = tmp_path / "state.json"; private_write(target, {"synthetic": True})
    assert target.stat().st_mode & 0o777 == 0o600


"""Isolated IAM roles for the separately approved beta payment subsystem."""
MANAGER_NAME = "finplanbetaresearch"
STACK_NAME = "finplan-beta-financeagent-research-payments"
BOUNDARY_NAME = "finplan-beta-research-payment-permission-boundary"
MANAGEMENT_ACTIONS = ["bedrock-agentcore:" + name for name in (
    "CreatePaymentInstrument", "GetPaymentInstrument", "ListPaymentInstruments", "DeletePaymentInstrument",
    "GetPaymentInstrumentBalance", "CreatePaymentSession", "GetPaymentSession", "ListPaymentSessions", "DeletePaymentSession")]
EXECUTION_ACTIONS = ["bedrock-agentcore:" + name for name in (
    "ProcessPayment", "GetPaymentInstrument", "GetPaymentInstrumentBalance", "GetPaymentSession")]


def role_template(operator_arn):
    manager = "arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:payment-manager/" + MANAGER_NAME + "-*"
    boundary = {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/" + BOUNDARY_NAME}
    def statement(actions, resources, effect="Allow"):
        return {"Effect": effect, "Action": actions, "Resource": [{"Fn::Sub": r} if "${" in r else r for r in resources]}
    operator_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": operator_arn}, "Action": "sts:AssumeRole"}]}
    service_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole", "Condition": {
        "StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}}, "ArnLike": {"aws:SourceArn": {"Fn::Sub": manager}}}}]}
    base = "arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:"
    policies = {
        "Management": [statement(MANAGEMENT_ACTIONS, [manager]), statement(["bedrock-agentcore:ProcessPayment"], ["*"], "Deny")],
        "Execution": [statement(EXECUTION_ACTIONS, [manager]), statement(["bedrock-agentcore:CreatePaymentSession", "bedrock-agentcore:DeletePaymentSession", "bedrock-agentcore:CreatePaymentInstrument", "bedrock-agentcore:DeletePaymentInstrument"], ["*"], "Deny")],
        "Service": [statement(["bedrock-agentcore:CreateWorkloadIdentity", "bedrock-agentcore:DeleteWorkloadIdentity", "bedrock-agentcore:TagResource"], [base + "workload-identity-directory/default", base + "workload-identity-directory/default/workload-identity/" + MANAGER_NAME + "-*"]),
                    statement(["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourcePaymentToken"], [base + "workload-identity-directory/default", base + "workload-identity-directory/default/workload-identity/" + MANAGER_NAME + "-*", base + "token-vault/default"]),
                    statement(["bedrock-agentcore:CreatePaymentCredentialProvider", "bedrock-agentcore:GetPaymentCredentialProvider", "bedrock-agentcore:TagResource"], [base + "token-vault/default", base + "token-vault/default/paymentcredentialprovider/*"])],
    }
    # Create-time tagging is authorized against an as-yet-unknown identity ARN.
    # Allow only the service-assumed role, in this account/region, with fixed tags.
    policies["Service"].append({**statement(["bedrock-agentcore:CreateWorkloadIdentity", "bedrock-agentcore:TagResource"],
        [base + "workload-identity-directory/default", base + "workload-identity-directory/default/workload-identity/*"]),
        "Condition": {"StringEquals": {"aws:RequestTag/project": "finplan", "aws:RequestTag/owner-repo": "financeagent", "aws:RequestTag/environment": "beta"}}})
    resources = {}
    for kind, statements in policies.items():
        resources[kind + "Role"] = {"Type": "AWS::IAM::Role", "Properties": {
            "RoleName": "finplan-beta-financeagent-research-payment-" + kind.lower(),
            "PermissionsBoundary": boundary, "AssumeRolePolicyDocument": service_trust if kind == "Service" else operator_trust,
            "Policies": [{"PolicyName": "research-payment-" + kind.lower(), "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}],
            "Tags": [{"Key": k, "Value": v} for k, v in {"project": "finplan", "owner-repo": "financeagent", "environment": "beta", "logical-role": "research-payment-" + kind.lower()}.items()]}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Independent beta research-payment roles; paper pipeline roles unchanged", "Resources": resources,
            "Outputs": {kind + "RoleArn": {"Value": {"Fn::GetAtt": [kind + "Role", "Arn"]}} for kind in policies}}

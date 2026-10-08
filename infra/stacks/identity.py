"""Per-environment identity stack ``finplan-<env>-financeagent-identity`` (task 5.0; FA-POL-07; design D2
"Identity provider"; matrix row ``oidc-identity-provider``).

One Amazon Cognito user pool per environment, the ONLY inbound identity provider of that
environment's Gateway and Runtime:

* self sign-up disabled (administrator-created users only), email sign-in, groups ``viewer``,
  ``researcher``, ``plan_editor``, ``plan_publisher``, ``ci_test`` (roles = groups);
* a hosted domain (the OAuth token endpoint);
* a resource server ``finplan-agent`` with the scopes ``invoke`` (people) and ``ci_test`` (the
  pipeline client only); the Gateway policy recognises the machine client by that scope, because
  client-credentials tokens carry no groups;
* a PUBLIC authorization-code (PKCE) client for the hosted agent, the CLI and direct MCP clients
  (Claude Code, Codex): no secret;
* the ``ci_test`` client-credentials client; its id and secret are stored in Secrets Manager under
  ``finplan/<env>/financeagent/ci-test-client`` (JSON ``{"client_id", "client_secret"}``) and the
  pipeline publishes only that NAME at ``/finplan/<env>/financeagent/secret-ref/ci-test-client``.

Nothing here names a pool, client, issuer or account: every value is a deploy-time token, published
by the stage runner from the stack outputs (``user-pool-ref``, ``authorizer-metadata-ref``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
from aws_cdk import Aws, Duration, Fn
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from . import naming as n
from .common import EnvStack, tag_role

__all__ = ["GROUPS", "IdentityStack"]

GROUPS = ("viewer", "researcher", "plan_editor", "plan_publisher", "ci_test")
LOGICAL = "oidc-identity-provider"
CI_LOGICAL = "ci-test-client"


class IdentityStack(EnvStack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, cfg: Mapping[str, Any], **kwargs: Any) -> None:
        region = str(cfg.get("region", "us-east-2"))
        super().__init__(
            scope,
            construct_id,
            env_name=env_name,
            region=region,
            description=f"FinanceAgent {env_name}: Cognito user pool (the Gateway and Runtime identity provider), app clients, ci_test client secret",
            stack_name=n.identity_stack_name(env_name),
            **kwargs,
        )
        env = env_name
        ident = dict(cfg.get("identity") or {})
        prod = env == "prod"
        self.pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=n.user_pool_name(env),
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(min_length=14, require_digits=True, require_lowercase=True, require_uppercase=True, require_symbols=True),
            deletion_protection=bool(ident.get("deletion_protection", prod)),
            removal_policy=cdk.RemovalPolicy.RETAIN if prod else cdk.RemovalPolicy.DESTROY,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
        )
        tag_role(self.pool, LOGICAL)
        # AWS::Cognito::UserPool carries its tags in UserPoolTags (not Tags): the ownership check reads metadata
        self.pool.node.default_child.add_metadata("logical-role", LOGICAL)
        self.groups = []
        for g in GROUPS:
            grp = cognito.CfnUserPoolGroup(self, f"Group{''.join(p.capitalize() for p in g.split('_'))}", user_pool_id=self.pool.user_pool_id, group_name=g, description=f"FinanceAgent role {g} ({env})")
            grp.add_metadata("logical-role", CI_LOGICAL if g == "ci_test" else LOGICAL)
            self.groups.append(grp)
        self.domain = self.pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=n.user_pool_domain_prefix(env, Aws.ACCOUNT_ID)))
        self.domain.node.default_child.add_metadata("logical-role", LOGICAL)
        invoke = cognito.ResourceServerScope(scope_name="invoke", scope_description="Use the FinanceAgent agent and tools as yourself")
        ci_scope = cognito.ResourceServerScope(scope_name="ci_test", scope_description="Pipeline test principal (read-only tools)")
        self.resource_server = self.pool.add_resource_server("ResourceServer", identifier=n.RESOURCE_SERVER, user_pool_resource_server_name=n.env_name(env, "agent-api"), scopes=[invoke, ci_scope])
        self.resource_server.node.default_child.add_metadata("logical-role", LOGICAL)
        token_minutes = int(ident.get("access_token_minutes", 60))
        self.pkce_client = self.pool.add_client(
            "PkceClient",
            user_pool_client_name=n.env_name(env, "users-pkce"),
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=False, user_password=False, custom=False, admin_user_password=False),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE, cognito.OAuthScope.resource_server(self.resource_server, invoke)],
                callback_urls=list(ident.get("callback_urls") or ["http://localhost:8765/callback"]),
                logout_urls=list(ident.get("logout_urls") or []) or None,
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            prevent_user_existence_errors=True,
            access_token_validity=Duration.minutes(token_minutes),
            id_token_validity=Duration.minutes(token_minutes),
            refresh_token_validity=Duration.days(1),
            enable_token_revocation=True,
        )
        self.pkce_client.node.default_child.add_metadata("logical-role", LOGICAL)
        self.ci_client = self.pool.add_client(
            "CiTestClient",
            user_pool_client_name=n.env_name(env, "ci-test"),
            generate_secret=True,
            auth_flows=cognito.AuthFlow(user_srp=False, user_password=False, custom=False, admin_user_password=False),
            o_auth=cognito.OAuthSettings(flows=cognito.OAuthFlows(client_credentials=True), scopes=[cognito.OAuthScope.resource_server(self.resource_server, ci_scope)]),
            access_token_validity=Duration.minutes(60),
            enable_token_revocation=True,
            prevent_user_existence_errors=True,
        )
        ci_cfn = self.ci_client.node.default_child
        ci_cfn.add_metadata("logical-role", CI_LOGICAL)
        secret_value = Fn.join("", ['{"client_id":"', ci_cfn.attr_client_id, '","client_secret":"', ci_cfn.attr_client_secret, '"}'])
        self.ci_secret = sm.CfnSecret(
            self, "CiTestClientSecret", name=n.secret_name(env, "ci-test-client"), description=f"FinanceAgent {env} ci_test client credentials (read by the {env} pipeline stage role only)", secret_string=secret_value
        )
        tag_role(self.ci_secret, CI_LOGICAL)

        self.discovery_url = Fn.join("", ["https://cognito-idp.", Aws.REGION, ".amazonaws.com/", self.pool.user_pool_id, "/.well-known/openid-configuration"])
        self.issuer = Fn.join("", ["https://cognito-idp.", Aws.REGION, ".amazonaws.com/", self.pool.user_pool_id])
        self.token_endpoint = Fn.join("", ["https://", self.domain.domain_name, ".auth.", Aws.REGION, ".amazoncognito.com/oauth2/token"])
        outputs = {
            "UserPoolId": self.pool.user_pool_id,
            "UserPoolArn": self.pool.user_pool_arn,
            "PkceClientId": self.pkce_client.user_pool_client_id,
            "CiTestClientId": self.ci_client.user_pool_client_id,
            "DiscoveryUrl": self.discovery_url,
            "Issuer": self.issuer,
            "TokenEndpoint": self.token_endpoint,
            "CiTestClientSecretName": n.secret_name(env, "ci-test-client"),
        }
        for k, v in outputs.items():
            cdk.CfnOutput(self, k, value=v)

    @property
    def allowed_clients(self) -> list[str]:
        return [self.pkce_client.user_pool_client_id, self.ci_client.user_pool_client_id]

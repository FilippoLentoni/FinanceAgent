"""Agent-owned, separately authenticated View-only brokerage Gateway (beta only)."""
import json
from pathlib import Path
from aws_cdk import Aws, Fn, CfnTag
from aws_cdk import aws_bedrockagentcore as ac, aws_iam as iam, aws_lambda as lam, aws_logs as logs
from .common import cfn_tags,tag_role


def build_brokerage(stack,identity,env,cfg):
    settings=cfg.get('brokerage_read') or {}
    if env!='beta' or not settings.get('enabled'):return None
    owner=settings.get('owner_sub');portfolio=settings.get('portfolio_uuid');secret=settings.get('secret_name')
    if not owner or not portfolio or secret!='finplan/beta/financeagent/coinbase-brokerage-readonly':
        raise ValueError('explicit brokerage owner, portfolio and dedicated read-only secret are required')
    prefix=f'finplan-{env}-financeagent-brokerage'
    secret_arn=Fn.join('',['arn:',Aws.PARTITION,':secretsmanager:',Aws.REGION,':',Aws.ACCOUNT_ID,':secret:',secret,'-??????'])
    function_name=prefix+'-read'
    group_name='/aws/lambda/'+function_name
    role=iam.Role(stack,'BrokerageReadRole',role_name=prefix+'-read-role',assumed_by=iam.ServicePrincipal('lambda.amazonaws.com'),
        inline_policies={'read-only':iam.PolicyDocument.from_json({'Version':'2012-10-17','Statement':[
            {'Effect':'Allow','Action':['secretsmanager:GetSecretValue'],'Resource':[secret_arn]},
            {'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],'Resource':[Fn.join('',['arn:',Aws.PARTITION,':logs:',Aws.REGION,':',Aws.ACCOUNT_ID,':log-group:',group_name,':*'])]}]})})
    tag_role(role,'brokerage-read-lambda')
    group=logs.CfnLogGroup(stack,'BrokerageReadLogs',log_group_name=group_name,retention_in_days=30)
    tag_role(group,'brokerage-read-lambda')
    issuer=Fn.join('',['https://cognito-idp.',Aws.REGION,'.amazonaws.com/',identity.pool.user_pool_id])
    handler=(Path(__file__).parents[1]/'brokerage_lambda'/'index.js').read_text()
    function=lam.CfnFunction(stack,'BrokerageReadFunction',function_name=function_name,runtime='nodejs22.x',handler='index.handler',
        role=role.role_arn,timeout=60,memory_size=256,code=lam.CfnFunction.CodeProperty(zip_file=handler),
        environment=lam.CfnFunction.EnvironmentProperty(variables={'SECRET_ARN':secret,'PORTFOLIO_UUID':portfolio,'OWNER_SUB':owner,
            'COGNITO_ISSUER':issuer,'ALLOWED_CLIENTS':Fn.to_json_string(identity.allowed_clients),'BETA_OPERATOR_CLIENT_ID':identity.ci_client.user_pool_client_id}),tags=[CfnTag(key=k,value=v) for k,v in cfn_tags(env,'brokerage-read-lambda').items()])
    function.add_dependency(group)
    function.add_metadata('logical-role','brokerage-read-lambda')
    gateway_role=iam.Role(stack,'BrokerageGatewayRole',role_name=prefix+'-gateway-role',assumed_by=iam.ServicePrincipal('bedrock-agentcore.amazonaws.com',conditions={
        'StringEquals':{'aws:SourceAccount':Aws.ACCOUNT_ID},'ArnLike':{'aws:SourceArn':Fn.join('',['arn:',Aws.PARTITION,':bedrock-agentcore:',Aws.REGION,':',Aws.ACCOUNT_ID,':gateway/',prefix,'-gateway-*'])}}),
        inline_policies={'invoke-read-only':iam.PolicyDocument.from_json({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':['lambda:InvokeFunction'],'Resource':[function.attr_arn]}]})})
    tag_role(gateway_role,'brokerage-read-gateway')
    gateway=ac.CfnGateway(stack,'BrokerageGateway',name=prefix+'-gateway',description='Owner-authenticated View-only Coinbase ETF discovery; no trading tools',
        protocol_type='MCP',authorizer_type='CUSTOM_JWT',role_arn=gateway_role.role_arn,
        authorizer_configuration=ac.CfnGateway.AuthorizerConfigurationProperty(custom_jwt_authorizer=ac.CfnGateway.CustomJWTAuthorizerConfigurationProperty(discovery_url=identity.discovery_url,allowed_clients=identity.allowed_clients)),
        tags=cfn_tags(env,'brokerage-read-gateway'))
    target=ac.CfnGatewayTarget(stack,'BrokerageReadTarget',gateway_identifier=gateway.attr_gateway_identifier,name='brokerage-read-status',
        credential_provider_configurations=[ac.CfnGatewayTarget.CredentialProviderConfigurationProperty(credential_provider_type='GATEWAY_IAM_ROLE')],
        metadata_configuration=ac.CfnGatewayTarget.MetadataConfigurationProperty(allowed_request_headers=['X-Finplan-User-Token']),
        target_configuration=ac.CfnGatewayTarget.TargetConfigurationProperty(mcp=ac.CfnGatewayTarget.McpTargetConfigurationProperty(lambda_=ac.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(lambda_arn=function.attr_arn,tool_schema=ac.CfnGatewayTarget.ToolSchemaProperty(inline_payload=[])))))
    target.add_property_override('TargetConfiguration.Mcp.Lambda.ToolSchema.InlinePayload',[{'Name':'get_etf_connection_status','Description':'Read owner-authorized Coinbase View-only connection and SPY ETF catalog status. No orders or account balances are exposed.','InputSchema':{'Type':'object','Properties':{}},'OutputSchema':{'Type':'object'}}])
    # Same-account invocation still requires an explicit source-bound Lambda resource grant.
    target.add_metadata('logical-role','brokerage-read-gateway')
    permission=lam.CfnPermission(stack,'BrokerageGatewayInvoke',action='lambda:InvokeFunction',function_name=function.attr_arn,principal='bedrock-agentcore.amazonaws.com',source_account=Aws.ACCOUNT_ID,source_arn=gateway.attr_gateway_arn)
    permission.add_metadata('logical-role','brokerage-read-lambda')
    target.add_dependency(permission)
    return gateway

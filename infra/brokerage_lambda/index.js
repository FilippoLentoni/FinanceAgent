'use strict';
// Fixed read-only brokerage probe. No order, transfer or account-creation routes exist.
const crypto = require('node:crypto');
const https = require('node:https');
const SPY='5b27e1b11b2328f8449830368d98cb479e235023db07f9a68bea0ba72d63227c';
const PREFIX='/api/v3/brokerage';
const paths = new Set([PREFIX+'/key_permissions',PREFIX+'/accounts?limit=1',PREFIX+'/products/'+SPY]);
const b64 = x => Buffer.from(typeof x==='string'?x:JSON.stringify(x)).toString('base64url');
function get(url,headers={},limit=262144) {
  return new Promise((resolve,reject)=>{
    const req=https.get(url,{headers,timeout:10000},res=>{
      if(res.statusCode!==200){res.resume();reject(new Error('remote read rejected'));return;}
      let size=0;const chunks=[];
      res.on('data',chunk=>{size+=chunk.length;if(size>limit){res.destroy();reject(new Error('response limit'));}else chunks.push(chunk);});
      res.on('end',()=>{try{resolve(JSON.parse(Buffer.concat(chunks).toString()));}catch{reject(new Error('invalid JSON'));}});
      res.on('error',()=>reject(new Error('response unavailable')));
    });
    req.on('timeout',()=>{req.destroy();reject(new Error('timeout'));});
    req.on('error',()=>reject(new Error('transport unavailable')));
  });
}
function validateClaims(token,jwks,now,env=process.env) {
  if(typeof token!=='string'||token.length>16384)throw new Error('token required');
  const parts=token.split('.');if(parts.length!==3)throw new Error('malformed token');
  const header=JSON.parse(Buffer.from(parts[0],'base64url'));
  const claims=JSON.parse(Buffer.from(parts[1],'base64url'));
  if(header.alg!=='RS256'||['jku','jwk','x5u','x5c','crit','b64'].some(k=>k in header))throw new Error('unsupported token');
  const key=jwks.keys.find(k=>k.kid===header.kid&&k.kty==='RSA');if(!key)throw new Error('unknown key');
  if(!crypto.verify('RSA-SHA256',Buffer.from(parts[0]+'.'+parts[1]),crypto.createPublicKey({key,format:'jwk'}),Buffer.from(parts[2],'base64url')))throw new Error('signature rejected');
  if(claims.iss!==env.COGNITO_ISSUER||claims.token_use!=='access'||!JSON.parse(env.ALLOWED_CLIENTS).includes(claims.client_id))throw new Error('owner required');
  if(!Number.isInteger(claims.exp)||!Number.isInteger(claims.iat)||claims.exp<=now||claims.iat>now||('nbf'in claims&&(!Number.isInteger(claims.nbf)||claims.nbf>now)))throw new Error('token expired');
  if(typeof claims.scope!=='string')throw new Error('scopes required');
  const scopes=claims.scope.split(' ');
  const owner=claims.sub===env.OWNER_SUB&&scopes.includes('finplan-agent/invoke')&&!scopes.includes('finplan-agent/ci_test');
  const operator=env.BETA_OPERATOR_CLIENT_ID&&claims.client_id===env.BETA_OPERATOR_CLIENT_ID&&claims.sub===env.BETA_OPERATOR_CLIENT_ID&&scopes.includes('finplan-agent/ci_test');
  if(!owner&&!operator)throw new Error('owner or exact beta operator required');
  return claims;
}
function jwt(credential,path,now){
  if(!paths.has(path))throw new Error('unapproved read');
  if(!/^organizations\/[^/\s]+\/apiKeys\/[^/\s]+$/.test(credential.name))throw new Error('invalid key');
  const key=crypto.createPrivateKey(credential.privateKey);
  if(key.asymmetricKeyType!=='ec'||key.asymmetricKeyDetails.namedCurve!=='prime256v1')throw new Error('ES256 required');
  const message=b64({alg:'ES256',typ:'JWT',kid:credential.name,nonce:crypto.randomBytes(24).toString('hex')})+'.'+b64({sub:credential.name,iss:'cdp',nbf:now,exp:now+120,uri:'GET api.coinbase.com'+path.split('?')[0]});
  return message+'.'+crypto.sign('sha256',Buffer.from(message),{key,dsaEncoding:'ieee-p1363'}).toString('base64url');
}
async function handler(event,context){
  let stage='input';
  try{
    if(!event||typeof event!=='object'||Object.keys(event).length!==0)throw new Error('no input arguments permitted');
    stage='owner_header';
    const headers=context?.clientContext?.custom?.bedrockAgentCorePropagatedHeaders;
    const entries=Object.entries(headers||{}).filter(([k])=>k.toLowerCase()==='x-finplan-user-token');
    if(entries.length!==1)throw new Error('owner token required');
    stage='owner_authentication';
    const now=Math.floor(Date.now()/1000);
    const jwks=await get(process.env.COGNITO_ISSUER+'/.well-known/jwks.json',{},65536);
    validateClaims(entries[0][1],jwks,now);
    const {SecretsManagerClient,GetSecretValueCommand}=require('@aws-sdk/client-secrets-manager');
    const sm=new SecretsManagerClient({});
    stage='secret_read';
    const secret=await sm.send(new GetSecretValueCommand({SecretId:process.env.SECRET_ARN}));
    const credential=JSON.parse(secret.SecretString);
    const read=path=>get('https://api.coinbase.com'+path,{Authorization:'Bearer '+jwt(credential,path,now),Accept:'application/json'});
    stage='key_permissions';
    const permissions=await read(PREFIX+'/key_permissions');
    if(permissions.can_view!==true||permissions.can_trade!==false||permissions.can_transfer!==false||permissions.portfolio_uuid!==process.env.PORTFOLIO_UUID)throw new Error('view-only scoped key required');
    stage='account_read';
    const accounts=await read(PREFIX+'/accounts?limit=1');
    if((accounts.accounts||[]).some(a=>a.retail_portfolio_id&&a.retail_portfolio_id!==process.env.PORTFOLIO_UUID))throw new Error('account scope mismatch');
    stage='product_read';
    const product=await read(PREFIX+'/products/'+SPY);const details=product.equity_product_details||{};
    const verified=product.product_id===SPY&&product.product_type==='EQUITY'&&details.ticker==='SPY'&&details.equity_subtype==='EQUITY_PRODUCT_SUBTYPE_ETF';
    return {status:'authenticated',authenticated:true,interface:'agentcore_gateway_advanced_trade_rest',read_tools:['get_etf_connection_status'],live_execution_enabled:false,etf_eligibility_verified:verified,
      etf_catalog:verified?[{ticker:'SPY',product_id:SPY,quote_currency:product.quote_currency_id,asset_type:'ETF',tradable:details.equity_trading_flags?.tradable===true,buy_enabled:details.equity_trading_flags?.buy_enabled===true,sell_enabled:details.equity_trading_flags?.sell_enabled===true}]:[],
      account_read_verified:true,order_permission_verified:false,
      message:verified?'Coinbase View-only brokerage access and SPY ETF catalog metadata were verified through the authenticated AgentCore Gateway. Live execution is disabled; catalog flags do not establish order permission.':'Coinbase View-only brokerage access was verified through the authenticated AgentCore Gateway; ETF classification remains unverified. Live execution is disabled.'};
  }catch{console.warn(JSON.stringify({event:'brokerage_read_failed',stage}));return {status:'unavailable',authenticated:false,live_execution_enabled:false,etf_eligibility_verified:false,message:'Owner-authenticated read-only brokerage verification failed. No account holdings were inferred and no order was submitted.'};}
}
exports.handler=handler;
exports.validateClaims=validateClaims;
exports.jwt=jwt;

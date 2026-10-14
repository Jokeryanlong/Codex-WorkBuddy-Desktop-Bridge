'use strict';
// Reuse the installed desktop's credential codec; credentials stay in memory.
const path = require('node:path');
const fs = require('node:fs');
const main = path.join(path.dirname(process.execPath), 'resources', 'app.asar', 'main');
const keys = require(path.join(main, 'app-lifetime-job.js'));
const fields = require(path.join(main, 'log-acl-guard.js'));
fields.to();
let codec;
let capabilities;
try {
  capabilities = keys.U(process._linkedBinding('electron_browser_workbuddy_storage').loggerGet());
  if (capabilities.symmetric.status !== 'available') throw new Error('Desktop credential codec is unavailable');
  fields._o({policy:'fields', symmetricKey:capabilities.symmetric.value});
  const configDir = process.env.WORKBUDDY_CONFIG_DIR || path.join(require('node:os').homedir(), '.workbuddy');
  const configPath = path.join(configDir, 'cache', 'acc-product-config-v3.json');
  const product = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const authId = product.authentication?.id;
  if (!authId || /[\\/]/.test(authId)) throw new Error('Desktop authentication identity is invalid');
  const authFile = path.join(process.env.LOCALAPPDATA, 'CodeBuddyExtension', 'Data', 'Public', 'auth', authId+'.info');
  if (fs.existsSync(authFile+'.logout')) throw new Error('WorkBuddy desktop is logged out');
  codec = fields.vo({purpose:'workbuddy-auth-session',resourceId:'auth/'+authId+'.info'});
  const session = codec.decode(JSON.parse(fs.readFileSync(authFile,'utf8')), fields.lo).value;
  if (!session?.account?.uid || typeof session.auth?.accessToken !== 'string') throw new Error('WorkBuddy desktop account is not signed in');
  if (session.auth.expiresAt && Date.now() >= session.auth.expiresAt) throw new Error('WorkBuddy desktop login expired; refresh the desktop account');
  const headers = {'x-user-id':session.account.uid};
  if (session.account.enterpriseId) {
    headers['x-enterprise-id']=session.account.enterpriseId;
    headers['x-tenant-id']=session.account.enterpriseId;
  }
  if (process.argv.includes('--bridge-auth-check')) {
    console.log('WB_AUTH_CHECK:'+JSON.stringify({account_authenticated:true,credential_source:'workbuddy-desktop',product_auth_id:authId}));
  }
  process.env.ACC_PRODUCT_CONFIG_PATH=configPath;
  process.env.CODEBUDDY_AUTH_TOKEN=session.auth.accessToken;
  process.env.CODEBUDDY_CUSTOM_HEADERS=Object.entries(headers).map(([key,value])=>key+': '+value).join('\n');
  delete process.env.CODEBUDDY_BASE_URL;
  process.env.CODEBUDDY_HOST='workbuddy-desktop';
  process.env.CODEBUDDY_FORCE_LITE_WB_BUNDLE='1';
  process.env.CODEBUDDY_FORCE_HEADLESS_BUNDLE='1';
} catch (error) {
  console.error('WORKBUDDY_DESKTOP_AUTH_ERROR: '+error.message);
  process.exitCode=1;
} finally {
  codec?.dispose();
  fields.bo();
  capabilities?.symmetric?.value?.key?.fill(0);
}
if (!process.exitCode && !process.argv.includes('--bridge-auth-check')) require(path.join(path.dirname(process.execPath),'resources','app.asar.unpacked','cli','bin','codebuddy'));

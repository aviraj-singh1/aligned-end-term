type Msg91Callback = (data: unknown) => void;

declare global {
  interface Window {
    initSendOTP?: (configuration: Record<string, unknown>) => void;
    sendOtp?: (identifier:string,success?:Msg91Callback,failure?:Msg91Callback) => void;
    retryOtp?: (channel:string|null,success?:Msg91Callback,failure?:Msg91Callback,requestId?:string) => void;
    verifyOtp?: (otp:number,success?:Msg91Callback,failure?:Msg91Callback,requestId?:string) => void;
  }
}

const SCRIPT_PRIMARY='https://verify.msg91.com/otp-provider.js';
const SCRIPT_FALLBACK='https://verify.phone91.com/otp-provider.js';
const REQUEST_TIMEOUT_MS=15_000;
let ready:Promise<void>|null=null;
let requestId='';

export const msg91Configured=Boolean(import.meta.env.VITE_MSG91_WIDGET_ID&&import.meta.env.VITE_MSG91_WIDGET_TOKEN);

export function normalizeIndianPhone(value:string):string {
  const compact=value.trim().replace(/[\s()-]/g,'');
  const national=compact.startsWith('+91')?compact.slice(3):compact.startsWith('91')&&compact.length===12?compact.slice(2):compact;
  if(!/^[6-9]\d{9}$/.test(national))throw new Error('Enter a valid 10-digit Indian mobile number.');
  return `+91${national}`;
}

export function indianNationalNumber(value:string|undefined):string {
  if(!value)return '';
  try{return normalizeIndianPhone(value).slice(3);}catch{return '';}
}

function detail(error:unknown,depth=0,seen=new Set<object>()):string {
  if(typeof error==='string'||typeof error==='number')return String(error);
  if(!error||typeof error!=='object'||depth>4||seen.has(error))return '';
  seen.add(error);
  const record=error as Record<string,unknown>;
  const parts=['message','type','code','error','description','data']
    .map(key=>detail(record[key],depth+1,seen))
    .filter(Boolean);
  return parts.join(' ');
}

export function phoneVerificationErrorMessage(error:unknown,action:'send'|'verify'):string {
  const value=detail(error).toLowerCase();
  if(value.includes('rate')||value.includes('limit')||value.includes('frequent'))return 'Please wait before requesting another verification code.';
  if(value.includes('expired'))return 'This verification code has expired. Request a new code.';
  if(value.includes('invalid')||value.includes('incorrect')||value.includes('mismatch'))return 'That verification code is incorrect or no longer valid.';
  if(value.includes('captcha'))return 'Complete the security check, then try again.';
  if(value.includes('unauthor')||value.includes('widget token')||value.includes('tokenauth'))return 'MSG91 rejected the widget configuration. Check the widget ID and browser token.';
  if(value.includes('network')||value.includes('failed to fetch')||value.includes('could not be loaded'))return 'Could not reach MSG91. Check your connection and try again.';
  if(value.includes('timed out'))return 'MSG91 did not respond. Please try again shortly.';
  if(value.includes('initialize')||value.includes('unavailable'))return 'Phone verification could not start. Refresh the page and try again.';
  return action==='send'?'We could not send the verification code. Please try again.':'We could not verify that code. Please try again.';
}

function loadScript(source:string):Promise<void>{
  return new Promise((resolve,reject)=>{
    const existing=document.querySelector<HTMLScriptElement>(`script[src="${source}"]`);
    if(existing){if(window.initSendOTP)resolve();else{existing.addEventListener('load',()=>resolve(),{once:true});existing.addEventListener('error',()=>reject(new Error('MSG91 could not be loaded.')),{once:true});}return;}
    const script=document.createElement('script');script.src=source;script.async=true;
    script.onload=()=>resolve();script.onerror=()=>reject(new Error('MSG91 could not be loaded.'));document.head.appendChild(script);
  });
}

async function waitForMethod(name:'sendOtp'|'retryOtp'|'verifyOtp'):Promise<void>{
  const deadline=Date.now()+5_000;
  while(typeof window[name]!=='function'&&Date.now()<deadline)await new Promise(resolve=>window.setTimeout(resolve,25));
  if(typeof window[name]!=='function')throw new Error('MSG91 phone verification did not initialize.');
}

export function prepareMsg91():Promise<void>{
  if(!msg91Configured)return Promise.reject(new Error('MSG91 phone verification is not configured.'));
  if(ready)return ready;
  ready=(async()=>{
    try{await loadScript(SCRIPT_PRIMARY);}catch{await loadScript(SCRIPT_FALLBACK);}
    if(!window.initSendOTP)throw new Error('MSG91 phone verification did not initialize.');
    window.initSendOTP({
      widgetId:import.meta.env.VITE_MSG91_WIDGET_ID,
      tokenAuth:import.meta.env.VITE_MSG91_WIDGET_TOKEN,
      exposeMethods:true,
      captchaRenderId:'msg91-captcha',
      // The current MSG91 SDK validates both callbacks even when the exposed
      // methods provide their own per-request callbacks.
      success:()=>undefined,
      failure:()=>undefined,
    });
    await waitForMethod('sendOtp');
  })().catch(error=>{ready=null;throw error;});
  return ready;
}

function extractRequestId(data:unknown):string {
  if(!data||typeof data!=='object')return '';
  const record=data as Record<string,unknown>;
  const direct=record.reqId||record.req_id||record.requestId;
  return typeof direct==='string'?direct:extractRequestId(record.data);
}

function extractAccessToken(data:unknown):string {
  if(typeof data==='string'&&data.length>=20)return data;
  if(!data||typeof data!=='object')return '';
  const record=data as Record<string,unknown>;
  for(const key of ['access-token','access_token','accessToken','token'])if(typeof record[key]==='string'&&String(record[key]).length>=20)return String(record[key]);
  return extractAccessToken(record.data);
}

function callback<T>(invoke:(resolve:(data:unknown)=>void,reject:(error:unknown)=>void)=>void,convert:(data:unknown)=>T):Promise<T>{
  return new Promise((resolve,reject)=>{
    const timer=window.setTimeout(()=>reject(new Error('MSG91 request timed out.')),REQUEST_TIMEOUT_MS);
    const succeed=(data:unknown)=>{window.clearTimeout(timer);try{resolve(convert(data));}catch(error){reject(error);}};
    const fail=(error:unknown)=>{window.clearTimeout(timer);reject(error);};
    try{invoke(succeed,fail);}catch(error){fail(error);}
  });
}

export async function sendMsg91Otp(phone:string):Promise<string>{
  await prepareMsg91();
  const normalized=normalizeIndianPhone(phone);
  if(!window.sendOtp)throw new Error('MSG91 phone verification is unavailable.');
  await callback((resolve,reject)=>window.sendOtp!(normalized.slice(1),resolve,reject),data=>{requestId=extractRequestId(data);return data;});
  return normalized;
}

export async function resendMsg91Otp():Promise<void>{
  await prepareMsg91();
  if(!window.retryOtp)throw new Error('MSG91 phone verification is unavailable.');
  await callback((resolve,reject)=>window.retryOtp!('11',resolve,reject,requestId||undefined),()=>undefined);
}

export async function verifyMsg91Otp(code:string):Promise<string>{
  await prepareMsg91();
  if(!/^\d{6}$/.test(code)||!window.verifyOtp)throw new Error('Enter the six-digit verification code.');
  return callback((resolve,reject)=>window.verifyOtp!(Number(code),resolve,reject,requestId||undefined),data=>{
    const token=extractAccessToken(data);if(!token)throw new Error('MSG91 did not return a verification token.');return token;
  });
}

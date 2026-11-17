'use strict';
const fs=require('node:fs'),crypto=require('node:crypto'),path=require('node:path');
function encrypt(value,passphrase){
  if(typeof passphrase!=='string'||passphrase.length<16)throw Error('CALENDAR_PASSPHRASE must contain at least 16 characters');
  const salt=crypto.randomBytes(16),iv=crypto.randomBytes(12),iterations=310000;
  const key=crypto.pbkdf2Sync(passphrase,salt,iterations,32,'sha256');
  const cipher=crypto.createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from('kejian.feishu.v1'));
  const body=Buffer.concat([cipher.update(JSON.stringify(value),'utf8'),cipher.final(),cipher.getAuthTag()]);
  return {version:1,algorithm:'AES-256-GCM',iterations,salt:salt.toString('base64'),iv:iv.toString('base64'),ciphertext:body.toString('base64')};
}
if(require.main===module){
  const [input,output]=process.argv.slice(2);
  if(!input||!output)throw Error('Usage: node encrypt-calendar.cjs input output');
  try{
    const value=JSON.parse(fs.readFileSync(input,'utf8'));
    const envelope=encrypt(value,process.env.CALENDAR_PASSPHRASE);
    fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(envelope));
    console.log('Encrypted calendar snapshot created. No plaintext calendar is published.');
  }finally{if(fs.existsSync(input))fs.unlinkSync(input);}
}
module.exports={encrypt};

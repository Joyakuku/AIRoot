import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { commandScript, planScript, registerScript, quote, readResponse } from '../bridge.mjs';
const config = { executable: process.execPath, informationRoot: process.cwd(), dataRoot: process.cwd() };

test('PowerShell quoting roundtrips shell metacharacters as literal data', { skip: process.platform !== 'win32' }, () => {
  const values = ["D:\\中文 空格\\it's a tool.exe", '$(throw "injected")', '`$env:USERNAME', 'x; throw 1 #', '--confirm', ''];
  const script = '@(' + values.map(quote).join(',') + ') | ConvertTo-Json -Compress';
  const output = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
    '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ' + script], { encoding: 'utf8', windowsHide: true });
  assert.deepEqual(JSON.parse(output.replace(/^\uFEFF/, '').trim()), values);
});

test('call cannot replace deployment registry, including equals syntax', () => {
  for (const args of [['status','--data-root','C:\\other'], ['env','list','--data-root=C:\\other'], ['status','--json'], ['daemon'], ['operation','plan','--file','C:\\other']]) {
    assert.throws(() => commandScript(config, args));
  }
});

test('resource program flags after separator retain their identity', () => {
  const script = commandScript(config, ['resource','run','test-id','--','--json','--data-root=program-value']);
  assert.match(script, /'--' '--json' '--data-root=program-value'$/);
});

test('apply does not fabricate confirmations and only interpolates literal tokens', () => {
  const script = commandScript(config, ['operation','apply', "id'; throw 1 #"]);
  assert.ok(!script.includes('--confirm'));
  assert.ok(script.endsWith("'id''; throw 1 #'"));
});

test('plan payload is lossless JSON data, independent of PowerShell syntax', () => {
  const request = { action: 'create', name: "中文$(throw 'x')", project: 'a`b' };
  const script = planScript(config, request);
  const encoded = script.match(/FromBase64String\('([^']+)'\)/)[1];
  assert.deepEqual(JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')), request);
  assert.ok(!script.includes(request.name));
  assert.match(script, /finally \{ Remove-Item -LiteralPath \$airootPlanFile/);
});

function result(stdout, fields = {}) { return { isError: false, value: { kind:'foreground', stdout: { text: stdout, truncated:false }, ...fields } }; }
test('AIRoot business errors remain failure envelopes', () => {
  const expected = { api_version: 1, ok: false, data: null, error: 'Resource is in use' };
  assert.deepEqual(readResponse(result(JSON.stringify(expected))), expected);
});
test('denied, incomplete and incompatible results cannot become successful responses', () => {
  const valid = JSON.stringify({ api_version:1, ok:true, data:42 });
  for (const item of [{ isError:true,error:{message:'approval denied'} }, result(valid,{sandbox:{denied:true}}), result(valid,{timedOut:true}), result(valid,{kind:'promoted'}), result(valid,{stdout:{text:valid,truncated:true}}), result('{"api_version":2,"ok":true}')]) {
    assert.throws(() => readResponse(item));
  }
});

test('explicit tool JSON remains literal, unverified and project-associated', () => {
 const resource={class:'tool',kind:'cli',name:"it'$(throw 1)",path:process.execPath,entry:process.execPath,projects:['test']};
 const script=registerScript(config,resource);
 const encoded=script.match(/FromBase64String\('([^']+)'\)/)[1];
 const row=JSON.parse(Buffer.from(encoded,'base64').toString('utf8'));
 assert.equal(row.status,'unverified'); assert.deepEqual(row.projects,['test']);
 assert.ok(!script.includes(resource.name));assert.match(script,/'registry' 'add' '--file'/);
 assert.throws(()=>registerScript(config,{...resource,kind:'chat-app'}));
});

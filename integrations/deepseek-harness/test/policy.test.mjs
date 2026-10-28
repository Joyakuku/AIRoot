import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requireDaemonAccess } from '../policy.mjs';
test('restricted and missing policies cannot enter the independent daemon', () => {
  for (const mode of ['read-only','workspace-write',undefined,'unrestricted']) {
    assert.throws(() => requireDaemonAccess(mode), /daemon access denied/);
  }
});
test('explicit Harness full access can reach daemon; confirmation rules remain AIRoot/Agent responsibilities', () => {
  assert.doesNotThrow(() => requireDaemonAccess('danger-full-access'));
});

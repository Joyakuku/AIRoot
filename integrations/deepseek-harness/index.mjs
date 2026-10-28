import { homedir } from 'node:os';
import { dirname, join, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import Schema from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { approveEscalation, validateEscalationArgs } from '@deepseek-ai/dsh-sandbox';
import { requireDaemonAccess } from './policy.mjs';
import { commandScript, planScript, registerScript, readResponse } from './bridge.mjs';

export const name = 'airoot';
export const inject = ['tools', 'shell', 'shellEnv', 'workingDirectory', 'sandboxPolicy'];
export const Config = Schema.object({
  executable: Schema.string(),
  dataRoot: Schema.string(),
  informationRoot: Schema.string(),
  timeoutMs: Schema.number().default(60000),
});

export function apply(ctx, config) {
  for (const field of ['executable', 'dataRoot', 'informationRoot']) {
    if (config[field] !== undefined && !isAbsolute(config[field])) throw new Error(`${field} must be absolute`);
  }
  if (!Number.isFinite(config.timeoutMs) || config.timeoutMs <= 0) throw new Error('timeoutMs must be positive');
  const pluginRoot = dirname(fileURLToPath(import.meta.url));
  const resolved = {
    ...config,
    executable: config.executable ?? process.env.AIROOT_EXE ?? join(pluginRoot, 'runtime', 'airoot.exe'),
    informationRoot: config.informationRoot ?? config.dataRoot ?? join(homedir(), '.airoot'),
  };
  if (!isAbsolute(resolved.executable)) throw new Error('Bundled AIRoot executable path must be absolute');
  const escalation = {
    sandbox_permissions: { type: 'string', description: 'Harness escalation target; AIRoot daemon access needs danger-full-access. After denial retry the exact call with justification through Harness approval.' },
    justification: { type: 'string', description: 'User-facing reason for the exact Harness escalation. Never a human deletion confirmation.' },
  };
  async function delegate(script, args, exec) {
    // Use only the public confining Shell service. Resolve the calling session's
    // policy on every call; no alternate process launcher or bypass fallback.
    validateEscalationArgs(args.sandbox_permissions, args.justification);
    if (ctx.shell.sandboxMode === undefined) throw new Error('AIRoot requires a confining Harness shell');
    let policy = ctx.sandboxPolicy.resolve(exec.agent ? { session: exec.agent.session } : {});
    if (args.sandbox_permissions !== undefined) {
      const mode = await approveEscalation(
        { requestedMode: args.sandbox_permissions, justification: args.justification, effectiveMode: policy.mode, subject: 'AIRoot command' },
        { approver: ctx.get('approval'), agent: exec.agent, callId: exec.callId, toolName: 'airoot', signal: exec.signal },
      );
      policy = { ...policy, mode };
    }
    // The daemon runs outside the child shell sandbox. Never treat restricted
    // shell policy as confinement of daemon-side effects. Explicit approved full
    // access is required even for queries, which may start/recover the daemon.
    requireDaemonAccess(policy.mode);
    const cwd = exec.agent ? await ctx.workingDirectory.ensure(exec.agent, exec.signal) : undefined;
    const spec = ctx.shell.resolve({ command: script, workdir: cwd, timeoutMs: config.timeoutMs,
      dshEnv: ctx.shellEnv.collect(exec), sandboxPolicy: policy, signal: exec.signal });
    const process = await ctx.shell.execute(spec);
    const value = await process.result();
    return readResponse({ value });
  }

  const output = { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] };
  ctx.tools.register(defineTool({
    name: 'airoot_command',
    description: 'Query or maintain AIRoot using literal CLI argument tokens, for example ["env","list"] or ["operation","show",id]. AIRoot reports risk and conditions; the Agent handles permission. Apply never fills confirmations automatically. Load the airoot skill for lifecycle and deletion rules. Calls run through the Harness public Shell service, session policy, approval and sandbox.',
    parameters: {
      args: { type: 'array', items: { type: 'string' }, required: true, description: 'AIRoot tokens without executable, --json or --data-root. No shell syntax. Use airoot_plan to prepare a maintenance plan.' },
      ...escalation,
    }, output,
    async execute(args, exec) { return delegate(commandScript(resolved, args.args), args, exec); },
  }));
  ctx.tools.register(defineTool({
    name: 'airoot_register',
    description: 'Explicitly register an already-installed reusable CLI, script or available MCP connector, with projects. Does not install anything. AIRoot verifies identity; ordinary chat/office apps are excluded. Use when registry add --path cannot establish tool identity.',
    parameters: { resource: { type: 'object', required: true, additionalProperties: false, properties: {
      class: { type: 'string', enum: ['tool'], required: true },
      kind: { type: 'string', enum: ['cli','script','mcp-connector'], required: true },
      name: { type: 'string', required: true }, path: { type: 'string', required: true },
      entry: { type: 'string', required: true }, projects: { type: 'array',items: {type:'string'},required:true },
      parent_id: {type:'string'}, metadata: {type:'object',additionalProperties:true},
    } }, ...escalation }, output,
    async execute(args, exec) { return delegate(registerScript(resolved,args.resource),args,exec); },
  }));
  ctx.tools.register(defineTool({
    name: 'airoot_plan',
    description: 'Prepare an AIRoot maintenance plan without executing it. Returns exact resource, paths, risk, effects and requirements. New environments require task abbreviation name and corresponding project. Review before a separate operation apply.',
    parameters: {
      request: {
        type: 'object', required: true, additionalProperties: false,
        properties: {
          action: { type: 'string', required: true },
          resource_id: { type: 'string' }, class: { type: 'string' }, kind: { type: 'string' },
          name: { type: 'string' }, target: { type: 'string' }, project: { type: 'string' },
          version: { type: 'string' }, manager: { type: 'string' },
          packages: { type: 'array', items: { type: 'string' } }, source: { type: 'string' },
          archive: { type: 'string' }, sha256: { type: 'string' },
        },
      }, ...escalation,
    }, output,
    async execute(args, exec) { return delegate(planScript(resolved, args.request), args, exec); },
  }));
}

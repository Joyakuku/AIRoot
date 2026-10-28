import { randomUUID } from 'node:crypto';
import { isAbsolute, join } from 'node:path';

const allowed = {
  status: [], capabilities: [],
  env: ['list', 'show'], tool: ['list', 'show'], project: ['show'],
  resource: ['find', 'run'], scan: ['start', 'status', 'pause', 'resume'],
  registry: ['add', 'verify', 'ignore', 'restore', 'backup'],
  operation: ['apply', 'show', 'recover', 'cleanup'],
  usage: ['list', 'acquire', 'release'], log: ['show'],
  components: ['status', 'service-plan', 'start', 'stop'], config: ['show', 'set'],
  'platform-discover': ['wsl', 'docker'], 'platform-inspect': null,
};

export function quote(value) {
  if (typeof value !== 'string' || value.includes('\0')) throw new Error('Expected a string without NUL');
  return "'" + value.replaceAll("'", "''") + "'";
}

export function invocation(config, args) {
  if (!isAbsolute(config.executable)) throw new Error('AIRoot executable must be absolute');
  if (config.dataRoot && !isAbsolute(config.dataRoot)) throw new Error('AIRoot dataRoot must be absolute');
  const prefix = [config.executable, '--json'];
  if (config.dataRoot) prefix.push('--data-root', config.dataRoot);
  return '& ' + [...prefix, ...args].map(quote).join(' ');
}

export function commandScript(config, args) {
  if (!Array.isArray(args) || args.length === 0 || args.some(a => typeof a !== 'string' || a.includes('\0'))) {
    throw new Error('args must be a nonempty array of strings without NUL');
  }
  const children = allowed[args[0]];
  if (!Object.hasOwn(allowed, args[0]) ||
      (args[0] === 'status' && args.length !== 1) ||
      (args[0] === 'capabilities' && args.length !== 1) ||
      (args.length === 1 && children?.length > 0) ||
      (args.length > 1 && children && !children.includes(args[1])) ||
      (args[0] === 'platform-discover' && args.length !== 2) ||
      (args[0] === 'platform-inspect' &&
        (args.length !== 4 || !args[1] || args[2] !== '--path' || !args[3]))) {
    throw new Error('Unsupported AIRoot command; use airoot_plan for operation plan');
  }
  // Resource arguments after -- belong to the selected program, not to AIRoot.
  const boundary = args[0] === 'resource' && args[1] === 'run' ? args.indexOf('--') : -1;
  const cliArgs = boundary < 0 ? args : args.slice(0, boundary);
  if (cliArgs.some(a => a === '--data-root' || a.startsWith('--data-root=') || a === '--json')) {
    throw new Error('Global output/data configuration belongs to the deployment');
  }
  return invocation(config, args);
}

export function planScript(config, request) {
  if (!request || typeof request !== 'object' || Array.isArray(request) || typeof request.action !== 'string') {
    throw new Error('A structured AIRoot plan request with action is required');
  }
  if (!isAbsolute(config.informationRoot)) throw new Error('informationRoot must be absolute');
  const directory = join(config.informationRoot, 'agent', 'requests');
  const path = join(directory, `${randomUUID()}.json`);
  const base64 = Buffer.from(JSON.stringify(request), 'utf8').toString('base64');
  return "$ErrorActionPreference='Stop'\n" +
    `New-Item -ItemType Directory -Path ${quote(directory)} -Force | Out-Null\n` +
    `$airootPlanFile=${quote(path)}\ntry {\n` +
    `  [System.IO.File]::WriteAllText($airootPlanFile,[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(${quote(base64)})),[System.Text.UTF8Encoding]::new($false))\n` +
    '  ' + invocation(config, ['operation', 'plan', '--file', path]) + '\n' +
    '} finally { Remove-Item -LiteralPath $airootPlanFile -Force -ErrorAction SilentlyContinue }';
}

export function readResponse(result) {
  if (result?.isError) throw new Error(result.error?.message ?? 'Harness denied the delegated PowerShell call');
  const value = result?.value;
  if ((value?.kind !== undefined && value.kind !== 'foreground') ||
      (value?.status !== undefined && value.status !== 'completed') ||
      value?.timedOut || value?.aborted || value?.cancelled || value?.signal ||
      value?.error || value?.permission || value?.restriction || value?.failure ||
      value?.stdout?.truncated || typeof value?.stdout?.text !== 'string') {
    throw new Error('No complete AIRoot response; inspect operation/status before repeating maintenance');
  }
  if (value.sandbox?.denied || value.sandbox?.runnerFailed) {
    throw new Error('Harness sandbox denied AIRoot; request escalation for this exact command through Harness');
  }
  let response;
  try { response = JSON.parse(value.stdout.text.replace(/^\uFEFF/, '').trim()); }
  catch { throw new Error('AIRoot returned no valid JSON response; inspect Harness process result before retrying'); }
  if (response.api_version !== 1 || typeof response.ok !== 'boolean') throw new Error('Unsupported AIRoot API response');
  return response;
}

// Explicit registration is separate from installation. This only serializes
// resource metadata; AIRoot still verifies identity and excludes ordinary apps.
export function registerScript(config, resource) {
  if (!resource || resource.class !== 'tool' || !['cli','script','mcp-connector'].includes(resource.kind)) {
    throw new Error('Explicit registration requires a supported tool kind');
  }
  if (!isAbsolute(resource.path) || !isAbsolute(resource.entry)) throw new Error('Absolute tool path and entry required');
  if (!Array.isArray(resource.projects) || resource.projects.some(p => typeof p !== 'string')) throw new Error('projects must be strings');
  if (!isAbsolute(config.informationRoot)) throw new Error('informationRoot must be absolute');
  const directory = join(config.informationRoot, 'agent', 'requests');
  const path = join(directory, `${randomUUID()}.json`);
  const row = { id: '', class: resource.class, kind: resource.kind, name: resource.name,
    path: resource.path, entry: resource.entry, version: null, parent_id: resource.parent_id ?? null,
    scope: 'host', status: 'unverified', projects: resource.projects, dependencies: [],
    metadata: resource.metadata ?? {}, updated_at: '' };
  const encoded = Buffer.from(JSON.stringify(row),'utf8').toString('base64');
  return "$ErrorActionPreference='Stop'\n" +
    `New-Item -ItemType Directory -Path ${quote(directory)} -Force | Out-Null\n` +
    `$airootRegistrationFile=${quote(path)}\ntry {\n` +
    `  [System.IO.File]::WriteAllText($airootRegistrationFile,[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(${quote(encoded)})),[System.Text.UTF8Encoding]::new($false))\n` +
    '  ' + invocation(config,['registry','add','--file',path]) + '\n' +
    '} finally { Remove-Item -LiteralPath $airootRegistrationFile -Force -ErrorAction SilentlyContinue }';
}

// The daemon is not a child of the confining shell. Restricted-mode IPC must
// not turn into an unconfined maintenance capability (queries can start recovery).
export function requireDaemonAccess(mode) {
  if (mode !== 'danger-full-access') {
    throw new Error(`[sandbox: AIRoot daemon access denied under ${mode ?? 'unknown'} mode] AIRoot's independent daemon is outside the shell sandbox. Retry this exact call with sandbox_permissions="danger-full-access" and justification through Harness approval; never switch tools or write confirmation strings to bypass this.`);
  }
}

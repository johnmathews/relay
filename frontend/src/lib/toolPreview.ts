// One-line tool-call summary used by both the timeline (`TimelinePane`)
// and the chat transcript (`ChatTranscript`). Per-tool aware: bash →
// `$ <command>`, read → `← <path>`, write/edit → `→ <path>`,
// grep → `? <pattern>`, glob → `* <pattern>`, task/agent → description.
// Falls back to `<firstKey>: <value>` for unknown tools. Returns '' when
// nothing useful can be summarised.

const PREVIEW_MAX = 140

export function truncatePreview(s: string, max = PREVIEW_MAX): string {
  if (s.length <= max) return s
  return `${s.slice(0, max - 1)}…`
}

export function toolPreview(name: string, args: unknown): string {
  // Pi emits tool names in either casing (`Bash` / `bash`) depending
  // on the underlying provider. Normalise to lowercase for matching.
  const n = name.toLowerCase()
  const a =
    typeof args === 'object' && args !== null
      ? (args as Record<string, unknown>)
      : null
  if (a == null) return ''
  const argStr = (k: string): string =>
    typeof a[k] === 'string' ? (a[k] as string) : ''
  const filePath = argStr('file_path') || argStr('path') || argStr('filename')
  const pattern = argStr('pattern')
  if (n === 'bash') {
    const cmd = argStr('command')
    if (cmd === '') return ''
    return truncatePreview(`$ ${cmd.replace(/\s+/g, ' ').trim()}`)
  }
  if (n === 'write' || n === 'edit') {
    return filePath === '' ? '' : truncatePreview(`→ ${filePath}`)
  }
  if (n === 'read') {
    return filePath === '' ? '' : truncatePreview(`← ${filePath}`)
  }
  if (n === 'grep') {
    return pattern === '' ? '' : truncatePreview(`? ${pattern}`)
  }
  if (n === 'glob') {
    return pattern === '' ? '' : truncatePreview(`* ${pattern}`)
  }
  if (n === 'task' || n === 'agent') {
    const d = argStr('description') || argStr('prompt')
    return d === '' ? '' : truncatePreview(d)
  }
  const keys = Object.keys(a)
  if (keys.length === 0) return ''
  const k = keys[0]!
  const v = a[k]
  const vs = typeof v === 'string' ? v : JSON.stringify(v)
  return truncatePreview(`${k}: ${vs}`)
}

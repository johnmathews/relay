// Minimal local type shims for the markdown-it ecosystem. No
// `@types/markdown-it*` is installed and adding a types dependency is
// not allowed (Phase-4 W6 constraint), so we declare exactly the narrow
// surface `src/lib/render.ts` touches rather than pull `any`. The
// concrete structural contract lives in render.ts (`MarkdownItInstance`
// etc.); these declarations only stop the implicit-any module errors
// and keep the default exports typed as constructors/plugin functions.

declare module 'markdown-it' {
  // The real class has a far larger surface; render.ts re-types it via
  // a local structural interface and only relies on `new`-ability here.
  const MarkdownIt: new (opts?: Record<string, unknown>) => unknown
  export default MarkdownIt
}

declare module 'markdown-it-footnote' {
  // A markdown-it plugin: `md.use(plugin)`. Opaque function.
  const plugin: (...args: unknown[]) => void
  export default plugin
}

declare module 'markdown-it-task-lists' {
  const plugin: (...args: unknown[]) => void
  export default plugin
}

import { useCallback, useSyncExternalStore } from 'react'

const EVENT = 'md:navigate'

function subscribe(cb: () => void) {
  window.addEventListener('popstate', cb)
  window.addEventListener(EVENT, cb)
  return () => { window.removeEventListener('popstate', cb); window.removeEventListener(EVENT, cb) }
}
const snapshot = () => window.location.pathname + window.location.search

export interface Route { path: string; params: URLSearchParams; segments: string[] }

export function useRoute(): Route {
  const key = useSyncExternalStore(subscribe, snapshot, snapshot)
  const [path, search = ''] = key.split('?')
  return { path, params: new URLSearchParams(search), segments: path.split('/').filter(Boolean) }
}

export function navigate(to: string, replace = false) {
  if (to === snapshot()) return
  window.history[replace ? 'replaceState' : 'pushState']({}, '', to)
  window.dispatchEvent(new Event(EVENT))
  if (!replace) { window.scrollTo({ top: 0 }); document.getElementById('md-scroll')?.scrollTo({ top: 0 }) }   // в окне приложения прокручивается область под шапкой
}

export function useNavigate() {
  return useCallback(navigate, [])
}

export function buildQuery(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(values)) if (v !== null && v !== undefined && v !== '') params.set(k, String(v))
  const s = params.toString()
  return s ? '?' + s : ''
}

/** Ссылка на запись: опционально вкладка и место в записи (секунды). */
export function recordLink(id: string, opts: { view?: string; t?: number | null } = {}): string {
  return `/records/${id}` + buildQuery({
    view: opts.view ?? (opts.t != null ? 'transcript' : undefined),
    t: opts.t != null ? Math.round(opts.t) : undefined,
  })
}

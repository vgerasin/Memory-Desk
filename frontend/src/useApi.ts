import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from './api'
import { useApp } from './store'

export interface Resource<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** GET-запрос, который сам обновляется при изменении данных на сервере; прежние данные остаются на экране, пока идёт обновление. */
export function useApi<T>(path: string | null): Resource<T> {
  const { version, invalidate } = useApp()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const lastPath = useRef<string | null>(null)

  useEffect(() => {
    if (!path) { setData(null); setLoading(false); return }
    let alive = true
    if (lastPath.current !== path) { setLoading(true); lastPath.current = path }
    api.get<T>(path)
      .then(result => { if (alive) { setData(result); setError(null) } })
      .catch(e => { if (alive) setError(errorText(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [path, version])

  const reload = useCallback(() => invalidate(), [invalidate])
  return { data, error, loading, reload }
}

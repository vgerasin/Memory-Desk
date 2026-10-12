import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { fmtBytes } from '../format'
import { useApp } from '../store'
import type { StorageUsage } from '../types'

export function StorageModal() {
  const { storageOpen, setStorageOpen, showToast, invalidate } = useApp()
  const [usage, setUsage] = useState<StorageUsage | null>(null)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState('')
  const [days, setDays] = useState(30)

  const load = useCallback(() => {
    api.get<StorageUsage>('/api/storage').then(setUsage).catch(e => setError(errorText(e)))
  }, [])
  useEffect(() => { if (storageOpen) { setError(''); setConfirm(''); load() } }, [storageOpen, load])
  if (!storageOpen) return null

  const run = async (key: string, withDays: boolean) => {
    setBusy(key); setError('')
    try {
      const r = await api.post<{ freed: number; removed: number; usage: StorageUsage }>('/api/storage/clean',
        { category: key, days: withDays ? days : undefined })
      setUsage(r.usage)
      showToast(r.removed ? `Освобождено ${fmtBytes(r.freed)} · файлов: ${r.removed}` : 'Нечего удалять')
      invalidate()
    } catch (e) { setError(errorText(e)) } finally { setBusy(''); setConfirm('') }
  }

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setStorageOpen(false) }}>
      <div className="modal" role="dialog" aria-label="Хранилище">
        <div>
          <div className="h3">Хранилище</div>
          <div className="subtitle">{usage ? `Всего занято: ${fmtBytes(usage.total)}` : 'Считаю…'} · заметки и база отсюда не удаляются</div>
        </div>
        {usage?.categories.map(c => (
          <div className="store-row" key={c.key}>
            <div className="store-main">
              <div className="store-title">{c.title}<span className="store-size">{fmtBytes(c.bytes)}{c.count ? ` · ${c.count}` : ''}</span></div>
              <div className="hint">{c.hint}</div>
            </div>
            <div className="store-actions">
              {c.days && <label className="store-days"><input type="number" min={0} max={3650} value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Старше, дней" /> дн.</label>}
              {confirm === c.key
                ? (
                  <>
                    <button className="btn s28 muted" onClick={() => setConfirm('')}>Нет</button>
                    <button className="btn s28 accent" disabled={busy === c.key} onClick={() => void run(c.key, c.days)}>Да, удалить</button>
                  </>
                )
                : <button className="btn s28" disabled={!c.count || !!busy} onClick={() => (c.danger ? setConfirm(c.key) : void run(c.key, c.days))}>{c.clean}</button>}
            </div>
          </div>
        ))}
        {usage && (
          <div className="store-info">
            <div className="label-caps">Остальное</div>
            {usage.info.map(i => (
              <div className="store-info-row" key={i.key}><span>{i.title}</span><span className="num">{fmtBytes(i.bytes)}</span></div>
            ))}
          </div>
        )}
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        <div className="modal-foot"><button className="btn s34" onClick={() => setStorageOpen(false)}>Закрыть</button></div>
      </div>
    </div>
  )
}

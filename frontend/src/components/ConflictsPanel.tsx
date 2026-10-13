import { useState } from 'react'
import { api, errorText } from '../api'
import { dayLabel } from '../format'
import { navigate, recordLink } from '../router'
import { useApp } from '../store'
import { useApi } from '../useApi'
import type { ConflictPair, ConflictVerdict } from '../types'
import { tr } from '../i18n'

const VERDICT: Record<ConflictVerdict['relation'], string> = {
  replaces: tr('Модель: новое решение заменяет старое'),
  contradicts: tr('Модель: решения противоречат друг другу'),
  consistent: tr('Модель: решения согласуются'),
  unrelated: tr('Модель: это разные вопросы'),
}

function Pair({ pair, onDone }: { pair: ConflictPair; onDone: () => void }) {
  const { showToast } = useApp()
  const [busy, setBusy] = useState(false)
  const [verdict, setVerdict] = useState<ConflictVerdict | null>(null)
  const ids = { old: pair.old.id, new: pair.new.id }

  const run = async (job: () => Promise<unknown>, ok?: string) => {
    setBusy(true)
    try { await job(); if (ok) showToast(ok); onDone() } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  const ask = async () => {
    setBusy(true)
    try { setVerdict(await api.post<ConflictVerdict>('/api/decisions/conflicts/check', ids)) } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  const part = (label: string, d: ConflictPair['old']) => (
    <button className="conflict-side" onClick={() => navigate(recordLink(d.meetingId, { t: d.tSec }))}>
      <span className="label-caps">{label} · {dayLabel(d.meetingDate)}</span>
      <span className="conflict-text">{d.text}</span>
    </button>
  )
  return (
    <div className="conflict">
      <div className="conflict-pair">{part(tr('Было'), pair.old)}{part(tr('Стало'), pair.new)}</div>
      {verdict && <div className={'conflict-verdict ' + verdict.relation}>{VERDICT[verdict.relation]}{verdict.reason ? `. ${verdict.reason}` : ''}</div>}
      <div className="conflict-actions">
        <button className="btn s28 accent" disabled={busy}
          onClick={() => void run(() => api.patch(`/api/decisions/${pair.old.id}`, { replaced_by: pair.new.id }), tr('Старое решение отмечено заменённым'))}>{tr('Старое заменено новым')}</button>
        <button className="btn s28" disabled={busy} onClick={() => void run(() => api.post('/api/decisions/conflicts/dismiss', ids))}>{tr('Не связаны')}</button>
        <button className="btn s28 muted" disabled={busy} onClick={() => void ask()}>{tr('Спросить модель')}</button>
      </div>
    </div>
  )
}

/** Над списком решений: новые решения, похожие на старые в том же проекте. Решает человек; модель только подсказывает. */
export function ConflictsPanel() {
  const { data, reload } = useApi<{ items: ConflictPair[]; total: number }>('/api/decisions/conflicts')
  const [open, setOpen] = useState(true)
  if (!data || data.total === 0) return null
  return (
    <section className="card conflicts" aria-label={tr('Возможные замены решений')}>
      <button className="series-head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="series-title">{tr('Возможно, решение уже устарело · {0}', [data.total])}</span>
        <span className="series-toggle">{open ? tr('Свернуть') : tr('Показать')}</span>
      </button>
      {open && <div className="conflicts-body">
        <div className="muted-text" style={{ fontSize: 13 }}>{tr('Новое решение похоже на старое из того же проекта. Если оно его заменяет, отметьте старое «Заменено».')}</div>
        {data.items.slice(0, 8).map(p => <Pair key={p.key} pair={p} onDone={reload} />)}
        {data.total > 8 && <div className="muted-text" style={{ fontSize: 13 }}>{tr('Ещё {0}: появятся после разбора этих.', [data.total - 8])}</div>}
      </div>}
    </section>
  )
}

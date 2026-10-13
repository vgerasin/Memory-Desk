import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { navigate } from '../router'
import { useApp } from '../store'
import type { EraseResult, ErasePlan } from '../types'
import { localeTag, tr } from '../i18n'

/** «Стереть запись целиком»: сначала список того, что исчезнет, потом слово-подтверждение. Откатить стирание нельзя. */
export function EraseDialog() {
  const { eraseId, setEraseId, showToast, invalidate } = useApp()
  const [plan, setPlan] = useState<ErasePlan | null>(null)
  const [word, setWord] = useState('')
  const confirmWord = tr('СТЕРЕТЬ')       // сервер принимает слово на любом из языков интерфейса
  const [scrubBackups, setScrubBackups] = useState(true)
  const [noteCopies, setNoteCopies] = useState(true)
  const [docs, setDocs] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<EraseResult | null>(null)

  useEffect(() => {
    if (!eraseId) return
    setPlan(null); setWord(''); setError(''); setFailed(null); setScrubBackups(true); setNoteCopies(true)
    api.get<ErasePlan>(`/api/meetings/${eraseId}/erase/plan`)
      .then(p => { setPlan(p); setDocs(new Set()) })
      .catch(e => setError(errorText(e)))
  }, [eraseId])
  if (!eraseId) return null

  const close = () => { if (!busy) setEraseId(null) }
  const ready = !!plan && !plan.busy && word.trim().toLocaleUpperCase(localeTag()) === confirmWord
  const toggleDoc = (id: string) => setDocs(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const erase = async () => {
    if (!plan || !ready) return
    setBusy(true); setError(''); setFailed(null)
    try {
      const r = await api.post<EraseResult>(`/api/meetings/${eraseId}/erase`,
        { confirm: word, scrubBackups, noteCopies, documents: [...docs] })
      if (!r.ok) { setFailed(r); return }
      setEraseId(null)
      navigate('/records', true)
      showToast(tr('Запись стёрта целиком'))
      invalidate()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) close() }}>
      <div className="modal erase" role="dialog" aria-label={tr('Стереть запись целиком')}>
        <div>
          <div className="h3">{tr('Стереть запись целиком')}</div>
          <div className="subtitle">{plan ? `«${plan.title}» · ${plan.date}` : tr('Считаю, что будет стёрто…')}</div>
        </div>
        {plan && (
          <>
            <div className="erase-list">
              {plan.items.filter(i => !i.optional).map(i => (
                <div className={'erase-item' + (i.count ? '' : ' none')} key={i.key}>
                  <span>{i.label}</span>
                  <span className="num">{i.count ? i.count : '—'}</span>
                  {(i.detail || (!i.count && i.empty)) && <span className="hint">{i.count ? i.detail : i.empty}</span>}
                </div>
              ))}
            </div>
            {plan.items.filter(i => i.optional).map(i => {
              const on = i.key === 'backups' ? scrubBackups : noteCopies
              return (
                <label className="erase-opt" key={i.key}>
                  <input type="checkbox" checked={on} disabled={!i.count}
                    onChange={() => (i.key === 'backups' ? setScrubBackups(!scrubBackups) : setNoteCopies(!noteCopies))} />
                  <span>{i.label}: {i.count || tr('нет')}{i.detail && <span className="hint"> · {i.detail}</span>}</span>
                </label>
              )
            })}
            {plan.documents.length > 0 && (
              <div className="erase-docs">
                <div className="label-caps">{tr('Документы, составленные по записи (отметьте, какие стереть тоже)')}</div>
                {plan.documents.map(d => (
                  <label className="erase-opt" key={d.id}>
                    <input type="checkbox" checked={docs.has(d.id)} onChange={() => toggleDoc(d.id)} />
                    <span>{d.name}<span className="hint"> · {d.folder}</span></span>
                  </label>
                ))}
              </div>
            )}
            <ul className="erase-warn">{plan.warnings.map(w => <li key={w}>{w}</li>)}</ul>
            {plan.busy && <div className="error-box" style={{ marginTop: 0 }}>{tr('Сейчас идёт обработка файла. Дождитесь её окончания или остановите её, затем повторите.')}</div>}
            <div className="field">
              <label htmlFor="erase-word">{tr('Чтобы подтвердить, введите слово {0}. Отменить стирание нельзя.', [confirmWord])}</label>
              <input id="erase-word" type="text" autoComplete="off" value={word} onChange={e => setWord(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') void erase() }} placeholder={confirmWord} />
            </div>
          </>
        )}
        {failed && (
          <div className="error-box" style={{ marginTop: 0 }}>
            {failed.interrupted ? tr('Стирание остановлено: часть файлов удалить не удалось. Запись и заметка не тронуты, можно повторить.') : tr('Стёрто не всё:')}
            <ul>{failed.problems.map(p => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        <div className="modal-foot">
          <button className="btn s34" onClick={close}>{tr('Отмена')}</button>
          <button className="btn danger s34" disabled={!ready || busy} onClick={() => void erase()}>{busy ? tr('Стираю…') : tr('Стереть навсегда')}</button>
        </div>
      </div>
    </div>
  )
}

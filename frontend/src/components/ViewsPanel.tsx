import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { useApi } from '../useApi'
import { tc } from '../format'
import type { MeetingDetail, ViewDto, ViewJob, ViewPresets } from '../types'

/** Вкладка «Взгляды»: дополнительные разделы заметки с другим фокусом. Основное саммари они не заменяют. */
export function ViewsPanel({ meeting, onJump }: { meeting: MeetingDetail; onJump: (sec: number | null) => void }) {
  const { showToast, invalidate } = useApp()
  const { data: presets } = useApi<ViewPresets>('/api/views/presets')
  const views = meeting.views ?? []
  const [active, setActive] = useState('')
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [instruction, setInstruction] = useState('')
  const [job, setJob] = useState<ViewJob>({ state: 'idle' })
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState<'refresh' | 'delete' | null>(null)
  const timer = useRef<number | null>(null)

  const stop = () => { if (timer.current != null) { window.clearInterval(timer.current); timer.current = null } }
  const poll = () => {
    stop()
    timer.current = window.setInterval(async () => {
      try {
        const r = await api.get<ViewJob>(`/api/meetings/${meeting.id}/views/status`)
        setJob(r)
        if (r.state === 'done' || r.state === 'error') {
          stop()
          if (r.state === 'done') { invalidate(); if (r.viewId) setActive(r.viewId); showToast(`Взгляд «${r.title}» готов`) }
        }
      } catch (e) { stop(); setError(errorText(e)) }
    }, 600)
  }
  useEffect(() => stop, [])
  useEffect(() => { setActive(''); setAdding(false); setJob({ state: 'idle' }); setError(''); setConfirm(null); stop() }, [meeting.id])
  useEffect(() => { setConfirm(null) }, [active])
  // уже идущий расчёт (например, после перезахода на вкладку) подхватываем
  useEffect(() => {
    void api.get<ViewJob>(`/api/meetings/${meeting.id}/views/status`).then(r => { if (r.state === 'running') { setJob(r); poll() } }).catch(() => undefined)
  }, [meeting.id])

  const current: ViewDto | undefined = views.find(v => v.id === active) ?? views[0]
  const running = job.state === 'running'
  const max = presets?.max ?? 8
  const have = (id: string) => views.some(v => v.id === id)
  const typed = title.trim().toLowerCase()
  const sameAsPreset = (presets?.presets ?? []).find(p => p.title.toLowerCase() === typed)
  const tooShort = instruction.trim().length < 8

  const launch = async (url: string, body?: unknown) => {
    setError('')
    try {
      setJob(await api.post<ViewJob>(url, body))
      setAdding(false); setConfirm(null)
      poll()
    } catch (e) { setError(errorText(e)) }
  }
  const remove = async (v: ViewDto) => {
    setError('')
    try {
      await api.del(`/api/meetings/${meeting.id}/views/${v.id}`)
      setConfirm(null); setActive(''); invalidate()
      showToast(`Взгляд «${v.title}» удалён, остальная заметка не изменилась`)
    } catch (e) { setError(errorText(e)) }
  }

  return (
    <div className="views">
      <div className="views-head">
        <div className="hint">Взгляд — отдельный раздел заметки с другим фокусом. Основное краткое содержание он не заменяет, а строится заново по стенограмме.</div>
        {!adding && !running && views.length < max && <button className="btn s28 accent" onClick={() => setAdding(true)}>Добавить взгляд</button>}
      </div>

      {running && (
        <div className="proto-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.percent ?? 0} aria-label="Построение взгляда">
          <div className="proto-line"><b>{job.label ?? 'Запускаю…'}</b><span className="num">{job.percent ?? 0}%</span></div>
          <div className="track"><i style={{ width: (job.percent ?? 0) + '%' }} /></div>
          <div className="proto-sub">Прошло {job.elapsed ?? 0} с · локальная модель читает стенограмму, обычно это от нескольких секунд до минуты</div>
        </div>
      )}
      {(error || job.state === 'error') && <div className="hint bad" role="alert">{error || job.message}</div>}

      {adding && presets && (
        <div className="view-add">
          <div className="label-caps">Готовые взгляды</div>
          <div className="view-presets">
            {(presets.presets).map(p => have(p.id) ? (
              <button key={p.id} className="view-preset have" onClick={() => { setActive(p.id); setAdding(false) }}>
                <b>{p.title}</b><span>Уже есть в этой записи. Открыть; обновить можно кнопкой «Пересчитать».</span>
              </button>
            ) : (
              <button key={p.id} className="view-preset" onClick={() => void launch(`/api/meetings/${meeting.id}/views`, { presetId: p.id })}>
                <b>{p.title}</b><span>{p.instruction}</span>
              </button>
            ))}
          </div>
          <div className="label-caps">Свой взгляд</div>
          <div className="view-custom">
            <input value={title} maxLength={60} placeholder="Название, например «Что согласовать с юристом»" aria-label="Название взгляда" onChange={e => setTitle(e.target.value)} />
            {sameAsPreset && <div className="hint">«{sameAsPreset.title}» — готовый взгляд: выберите его в списке выше, описывать ничего не нужно.</div>}
            <textarea value={instruction} maxLength={400} placeholder="Что выписывать: одной-двумя фразами" aria-label="Что выписывать" onChange={e => setInstruction(e.target.value)} />
            <div className="row">
              <button className="btn s28 accent" disabled={!title.trim() || tooShort || !!sameAsPreset}
                onClick={() => void launch(`/api/meetings/${meeting.id}/views`, { title, instruction })}>Построить</button>
              <button className="btn s28" onClick={() => setAdding(false)}>Отмена</button>
            </div>
            {!sameAsPreset && (!title.trim() || tooShort) && <div className="hint">Чтобы «Построить» стало активным, введите название и опишите, что выписывать (не короче 8 символов).</div>}
          </div>
        </div>
      )}

      {views.length === 0 && !adding && !running && (
        <div className="muted-text">Взглядов пока нет. Например: «Только решения», «Риски» или «Для руководителя на 5 строк».</div>
      )}

      {views.length > 0 && (
        <>
          <div className="view-tabs" role="tablist">
            {views.map(v => (
              <button key={v.id} role="tab" aria-selected={v.id === current?.id} className={'view-tab' + (v.id === current?.id ? ' on' : '')} onClick={() => setActive(v.id)}>{v.title}</button>
            ))}
          </div>
          {current && (
            <section className="view-body" aria-label={`Взгляд «${current.title}»`}>
              <div className="view-source">
                {current.date && <span>Построен {current.date.split('-').reverse().join('.')}</span>}
                {current.model && <span>{current.model}</span>}
                <span>по стенограмме</span>
                {current.edited && <span className="view-edited">правили вручную</span>}
              </div>
              {current.items.length === 0 && <div className="muted-text">Подходящего в записи не нашлось</div>}
              {current.items.map((it, i) => (
                <div className="extra-item" key={i}>
                  <span>{it.text}</span>
                  {it.tSec != null && <button className="link-btn mono" onClick={() => onJump(it.tSec)}>{tc(it.tSec)}</button>}
                </div>
              ))}
              <div className="view-actions">
                {confirm === 'refresh' ? (
                  <>
                    <span className="hint bad">Раздел правили вручную, пересчёт заменит правки.</span>
                    <button className="btn s28 accent" disabled={running} onClick={() => void launch(`/api/meetings/${meeting.id}/views/${current.id}/refresh?force=true`)}>Заменить</button>
                    <button className="btn s28" onClick={() => setConfirm(null)}>Не надо</button>
                  </>
                ) : confirm === 'delete' ? (
                  <>
                    <span className="hint">Удалить только этот раздел. Остальная заметка не изменится.</span>
                    <button className="btn s28 accent" onClick={() => void remove(current)}>Удалить</button>
                    <button className="btn s28" onClick={() => setConfirm(null)}>Не надо</button>
                  </>
                ) : (
                  <>
                    <button className="btn s28" disabled={running}
                      onClick={() => (current.edited ? setConfirm('refresh') : void launch(`/api/meetings/${meeting.id}/views/${current.id}/refresh`))}>Пересчитать</button>
                    <button className="btn s28" disabled={running} onClick={() => setConfirm('delete')}>Удалить</button>
                  </>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}

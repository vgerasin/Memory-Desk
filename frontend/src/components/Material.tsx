import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { plural } from '../format'
import type { MapNode, MaterialKind, MaterialResult, MaterialTarget } from '../types'
import { IconOut, Segmented } from './ui'

const TITLES: Record<MaterialKind, string> = { protocol: 'Протокол встречи', map: 'Карта', deck: 'Презентация' }
const PERIODS = [{ key: '30', label: '30 дней' }, { key: '90', label: '90 дней' }, { key: '365', label: 'Год' }, { key: '3650', label: 'Всё время' }]

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url; link.download = name
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Tree({ node, depth = 0 }: { node: MapNode; depth?: number }) {
  return (
    <li>
      <span className={'map-node d' + Math.min(depth, 3)}>{node.text}</span>
      {node.children.length > 0 && <ul>{node.children.map((c, i) => <Tree key={i} node={c} depth={depth + 1} />)}</ul>}
    </li>
  )
}

interface ProtocolProgress {
  state: 'idle' | 'running' | 'done' | 'error'; step: number; steps: number; label: string; percent: number; elapsed: number
  result?: MaterialResult; message?: string
}

const pause = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

function endpoint(target: MaterialTarget, days: string): { path: string; body?: object } {
  const scope = { meetingId: target.meetingId, projectId: target.projectId, personId: target.personId, days: Number(days) }
  if (target.kind === 'protocol') return { path: `/api/meetings/${encodeURIComponent(target.meetingId ?? '')}/protocol` }
  return { path: target.kind === 'map' ? '/api/materials/mindmap' : '/api/materials/deck', body: scope }
}

/** Окно «Материалы»: протокол записи, карта и презентация. Текст можно поправить, скопировать, скачать или сохранить в Obsidian. */
export function MaterialModal() {
  const { material, setMaterial, showToast } = useApp()
  const [days, setDays] = useState('30')
  const [result, setResult] = useState<MaterialResult | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<ProtocolProgress | null>(null)
  const [saved, setSaved] = useState<{ path: string; url?: string } | null>(null)
  const seq = useRef(0)

  const applyResult = (data: MaterialResult) => {
    setResult(data)
    setText(data.markdown ?? (data.mermaid ? `# Карта: ${data.title}\n\n\`\`\`mermaid\n${data.mermaid}\n\`\`\`\n` : ''))
  }

  const generate = useCallback(async (target: MaterialTarget, period: string) => {
    const mine = ++seq.current
    setBusy(true); setError(''); setResult(null); setSaved(null); setProgress(null)
    try {
      if (target.kind === 'protocol' && target.meetingId) {
        // Протокол составляется в фоне: показываем этап и процент, пока модель пишет текст
        const base = `/api/meetings/${encodeURIComponent(target.meetingId)}/protocol`
        let state = await api.post<ProtocolProgress>(`${base}/start`)
        while (state.state === 'running') {
          if (mine !== seq.current) return
          setProgress(state)
          await pause(600)
          state = await api.get<ProtocolProgress>(`${base}/progress`)
        }
        if (mine !== seq.current) return
        if (state.state === 'error' || !state.result) throw new Error(state.message ?? 'Не удалось составить протокол')
        setProgress({ ...state, percent: 100 })
        applyResult(state.result)
        return
      }
      const { path, body } = endpoint(target, period)
      const data = await api.post<MaterialResult>(path, body)
      if (mine !== seq.current) return
      applyResult(data)
    } catch (e) {
      if (mine === seq.current) setError(errorText(e))
    } finally {
      if (mine === seq.current) setBusy(false)
    }
  }, [])

  // Протокол и материалы по одной записи строятся сразу; для проекта и человека сначала выбирают период.
  useEffect(() => {
    seq.current++
    setResult(null); setError(''); setSaved(null); setBusy(false); setProgress(null)
    if (material?.result) { applyResult(material.result); return }
    if (material && (material.kind === 'protocol' || material.meetingId)) void generate(material, days)
  }, [material]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!material) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMaterial(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [material, setMaterial])

  if (!material) return null
  const kind = material.kind
  const needsPeriod = kind !== 'protocol' && !material.meetingId
  const fileName = result?.fileName ?? `${kind === 'map' ? 'Карта — ' : ''}${result?.title ?? material.title}.md`

  const copy = async () => {
    try { await navigator.clipboard.writeText(kind === 'map' && result?.mermaid ? result.mermaid : text); showToast(kind === 'map' ? 'Код карты скопирован' : 'Текст скопирован') }
    catch { showToast('Не удалось скопировать — выделите текст вручную') }
  }
  const save = async () => {
    try {
      const out = await api.post<{ savedAs: string; obsidianUrl?: string }>('/api/materials/save', { kind: kind === 'map' ? 'map' : 'protocol', name: fileName, content: text })
      setSaved({ path: out.savedAs, url: out.obsidianUrl })
      showToast('Сохранено в Obsidian')
    } catch (e) { showToast(errorText(e)) }
  }

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setMaterial(null) }}>
      <div className="modal material" role="dialog" aria-label={TITLES[kind]}>
        <div className="prep-head">
          <div style={{ minWidth: 0 }}>
            <span className="label-caps">{TITLES[kind]}</span>
            <div className="h3 ellipsis-2">{result?.title ?? material.title}</div>
          </div>
          {result && <span className="prep-count">{plural(result.records ?? 1, 'запись', 'записи', 'записей')}</span>}
        </div>

        {needsPeriod && (
          <div className="material-period">
            <Segmented small items={PERIODS} value={days} onChange={v => setDays(v)} />
            <button className="btn accent s34" disabled={busy} onClick={() => void generate(material, days)}>
              {busy ? 'Собираю…' : result ? 'Собрать заново' : kind === 'deck' ? 'Создать презентацию' : 'Построить карту'}
            </button>
          </div>
        )}
        {!needsPeriod && busy && kind === 'protocol' && (
          <div className="proto-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0} aria-label="Составление протокола">
            <div className="proto-line">
              <b>{progress?.label ?? 'Запускаю…'}</b>
              <span className="num">{progress?.percent ?? 0}%</span>
            </div>
            <div className="track"><i style={{ width: (progress?.percent ?? 0) + '%' }} /></div>
            <div className="proto-sub">
              {progress ? `Шаг ${progress.step} из ${progress.steps} · прошло ${progress.elapsed} с` : 'Подготовка'}
              {progress && progress.step === 2 && ' · с локальной моделью этот шаг занимает от нескольких секунд до минуты'}
            </div>
          </div>
        )}
        {!needsPeriod && busy && kind !== 'protocol' && <div className="muted-text">Собираю…</div>}
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}

        {result && kind === 'protocol' && (
          <>
            <textarea className="material-text" value={text} onChange={e => setText(e.target.value)} aria-label="Текст протокола" spellCheck={false} />
            <div className="muted-text" style={{ fontSize: 13 }}>
              {result.mode === 'model' ? 'Ход обсуждения написан локальной моделью по фактам записи — проверьте перед отправкой.' : 'Протокол собран из извлечённых фактов без модели (Ollama не запущена или недоступна).'}
              {' '}Текст можно править здесь.
            </div>
          </>
        )}
        {result && kind === 'map' && result.tree && (
          <>
            <ul className="map-tree"><Tree node={result.tree} /></ul>
            <div className="muted-text" style={{ fontSize: 13 }}>В Obsidian карта сохраняется как схема Mermaid и рисуется в режиме чтения; код можно вставить в любой редактор диаграмм.</div>
          </>
        )}
        {result && kind === 'deck' && result.url && (
          <div className="material-deck">
            <div>
              <b>{plural(result.slides ?? 0, 'слайд', 'слайда', 'слайдов')}</b>
              <span className="muted-text"> · {result.mode === 'model' ? 'слайд «Главное» написала локальная модель' : 'слайд «Главное» собран из цифр и свежих резюме без модели'}</span>
            </div>
            <a className="btn accent s36" href={result.url} download style={{ textDecoration: 'none' }}>Скачать .pptx</a>
            <div className="muted-text" style={{ fontSize: 13 }}>Файл открывается в Keynote, PowerPoint и Google Slides. Копия лежит в папке «presentations» кабинета.</div>
          </div>
        )}
        {saved && (
          <div className="material-saved">
            Сохранено: <code>{saved.path}</code>
            {saved.url && <a href={saved.url} style={{ marginLeft: 8 }}>Открыть в Obsidian <IconOut /></a>}
          </div>
        )}

        <div className="modal-foot">
          {result && kind !== 'deck' && (
            <>
              <button className="btn s34" onClick={() => void copy()}>{kind === 'map' ? 'Копировать код' : 'Копировать'}</button>
              <button className="btn s34" onClick={() => download(fileName, text)}>Скачать .md</button>
              <button className="btn s34" onClick={() => void save()}>В Obsidian</button>
            </>
          )}
          <button className="btn accent s34" onClick={() => setMaterial(null)}>Закрыть</button>
        </div>
      </div>
    </div>
  )
}

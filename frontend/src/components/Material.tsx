import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { plural } from '../format'
import { navigate, recordLink } from '../router'
import type { ArtifactKind, MapNode, MaterialKind, MaterialResult, MaterialTarget } from '../types'
import { IconOut, Segmented } from './ui'
import { ExportMenu, MAP_FORMATS } from './ExportMenu'

const TITLES: Record<MaterialKind, string> = {
  protocol: 'Протокол встречи', map: 'Карта', deck: 'Презентация',
  memo: 'Служебная записка', brief: 'Бриф', followup: 'Письмо участникам (черновик)',
}
const isArtifact = (kind: MaterialKind): kind is ArtifactKind => kind === 'memo' || kind === 'brief' || kind === 'followup'

interface TimeLink { id: string; t: string; sec: number; text: string }

/** Таймкоды `⏱ ЧЧ:ММ:СС` из текста документа с записью, к которой они относятся (по названию записи в строке). */
function timeLinks(text: string, refs: NonNullable<MaterialResult['refs']>): TimeLink[] {
  const out: TimeLink[] = []
  const seen = new Set<string>()
  for (const line of text.split('\n')) {
    const m = /⏱\s*(\d{1,2}):(\d{2}):(\d{2})/.exec(line)
    if (!m) continue
    const ref = refs.length === 1 ? refs[0] : refs.find(r => line.includes(r.label))
    if (!ref) continue
    const sec = Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3])
    const key = ref.id + ':' + sec
    if (seen.has(key)) continue
    seen.add(key)
    const plain = line.replace(/\[\[[^\]|]*\|?([^\]]*)\]\]/g, '').replace(/[`*_>|#]/g, '').replace(/^\s*(?:[-*]|\d+\.)\s+/, '').replace(/⏱\s*[\d:]+/, '').replace(/\s+/g, ' ').replace(/[\s—,·-]+$/, '').trim()
    out.push({ id: ref.id, t: m[0].replace('⏱', '').trim().padStart(8, '0'), sec, text: plain.length > 90 ? plain.slice(0, 89) + '…' : plain })
  }
  return out
}
const PERIODS = [{ key: '30', label: '30 дней' }, { key: '90', label: '90 дней' }, { key: '365', label: 'Год' }, { key: '3650', label: 'Всё время' }]

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

function endpoint(target: MaterialTarget, days: string, addressee: string): { path: string; body?: object } {
  const scope = { meetingId: target.meetingId, projectId: target.projectId, personId: target.personId, days: Number(days) }
  if (isArtifact(target.kind)) return { path: '/api/materials/artifact', body: { ...scope, kind: target.kind, addressee } }
  if (target.kind === 'protocol') return { path: `/api/meetings/${encodeURIComponent(target.meetingId ?? '')}/protocol` }
  return { path: target.kind === 'map' ? '/api/materials/mindmap' : '/api/materials/deck', body: scope }
}

/** Окно «Материалы»: протокол записи, карта и презентация. Текст можно поправить, скопировать, скачать или сохранить в Obsidian. */
export function MaterialModal() {
  const { material, setMaterial, showToast } = useApp()
  const [days, setDays] = useState('30')
  const [addressee, setAddressee] = useState('')
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

  const generate = useCallback(async (target: MaterialTarget, period: string, to = '') => {
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
      const { path, body } = endpoint(target, period, to)
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
    if (material && (material.kind === 'protocol' || material.meetingId)) void generate(material, days, addressee)
  }, [material]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!material) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMaterial(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [material, setMaterial])

  if (!material) return null
  const kind = material.kind
  const artifact = isArtifact(kind)
  const needsPeriod = kind !== 'protocol' && !material.meetingId
  const links = result && artifact ? timeLinks(text, result.refs ?? []) : []
  const fileName = result?.fileName ?? `${kind === 'map' ? 'Карта — ' : ''}${result?.title ?? material.title}.md`

  const copy = async () => {
    try { await navigator.clipboard.writeText(kind === 'map' && result?.mermaid ? result.mermaid : text); showToast(kind === 'map' ? 'Код карты скопирован' : 'Текст скопирован') }
    catch { showToast('Не удалось скопировать — выделите текст вручную') }
  }
  const save = async () => {
    try {
      const saveKind = artifact ? kind : kind === 'map' ? 'map' : 'protocol'
      const out = await api.post<{ savedAs: string; obsidianUrl?: string }>('/api/materials/save', { kind: saveKind, name: fileName, content: text })
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

        {(needsPeriod || (kind === 'memo' && !!material.meetingId)) && (
          <div className="material-period">
            {needsPeriod && <Segmented small items={PERIODS} value={days} onChange={v => setDays(v)} />}
            {kind === 'memo' && (
              <input className="material-to" value={addressee} maxLength={200} onChange={e => setAddressee(e.target.value)}
                placeholder="Кому: должность, ФИО" aria-label="Адресат записки" />
            )}
            <button className="btn accent s34" disabled={busy} onClick={() => void generate(material, days, addressee)}>
              {busy ? 'Собираю…' : result ? (kind === 'memo' && !needsPeriod ? 'Подставить адресата' : 'Собрать заново') : kind === 'deck' ? 'Создать презентацию' : artifact ? 'Составить' : 'Построить карту'}
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
        {result && artifact && (
          <>
            <textarea className="material-text" value={text} onChange={e => setText(e.target.value)} aria-label={TITLES[kind]} spellCheck={false} />
            <div className="muted-text" style={{ fontSize: 13 }}>
              Каждый пункт взят из решений, задач и вопросов записи и помечен таймкодом места в записи.{' '}
              {result.mode === 'model' ? 'Вводную фразу написала локальная модель — проверьте её.' : 'Документ собран из извлечённых фактов без модели (Ollama не запущена или недоступна).'}
              {kind === 'followup' && ' Это черновик: письмо не отправляется, скопируйте его или скачайте.'}
              {' '}Текст можно править здесь.
            </div>
            {links.length > 0 && (
              <details className="doc-times">
                <summary>Места в записи · {links.length}</summary>
                <ul>
                  {links.map(l => (
                    <li key={l.id + l.sec}>
                      <button className="link-btn" onClick={() => { setMaterial(null); navigate(recordLink(l.id, { t: l.sec })) }}>⏱ {l.t}</button>
                      <span>{l.text}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {(result.skipped?.length ?? 0) > 0 && (
              <details className="doc-times skipped">
                <summary>Не вошло: нет места в записи · {result.skipped!.length}</summary>
                <ul>{result.skipped!.map((x, i) => <li key={i}><span>{x.text}</span></li>)}</ul>
              </details>
            )}
          </>
        )}
        {result && kind === 'map' && result.tree && (
          <>
            <ul className="map-tree"><Tree node={result.tree} /></ul>
            <div className="muted-text" style={{ fontSize: 13 }}>В Obsidian карта сохраняется как схема Mermaid. «Скачать карту» даёт PNG и SVG для презентаций и OPML для XMind, MindNode и OmniOutliner.</div>
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
              {kind === 'map' && result.tree
                ? <ExportMenu endpoint="/api/export/map" formats={MAP_FORMATS} label="Скачать карту" up body={() => ({ title: result.title, tree: result.tree })} />
                : <ExportMenu endpoint="/api/export" up body={() => ({ title: result.title, markdown: text, name: fileName.replace(/\.md$/i, '') })} />}
              <button className="btn s34" onClick={() => void save()}>В Obsidian</button>
            </>
          )}
          <button className="btn accent s34" onClick={() => setMaterial(null)}>Закрыть</button>
        </div>
      </div>
    </div>
  )
}

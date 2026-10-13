import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { plural } from '../format'
import { navigate, recordLink } from '../router'
import type { ArtifactKind, MapNode, MaterialKind, MaterialResult, MaterialTarget } from '../types'
import { IconOut, Segmented } from './ui'
import { ExportMenu, MAP_FORMATS } from './ExportMenu'
import { tr } from '../i18n'

const TITLES: Record<MaterialKind, string> = {
  protocol: tr('Протокол встречи'), map: tr('Карта'), deck: tr('Презентация'),
  memo: tr('Служебная записка'), brief: tr('Бриф'), followup: tr('Письмо участникам (черновик)'), digest: tr('Дайджест по проектам'),
}
const DIGEST_PERIODS = [{ key: 'this_week', label: tr('Эта неделя') }, { key: 'last_week', label: tr('Прошлая неделя') }, { key: 'this_month', label: tr('Этот месяц') }, { key: 'last_month', label: tr('Прошлый месяц') }]
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
const PERIODS = [{ key: '30', label: tr('30 дней') }, { key: '90', label: tr('90 дней') }, { key: '365', label: tr('Год') }, { key: '3650', label: tr('Всё время') }]

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
  if (target.kind === 'digest') return { path: '/api/materials/digest', body: { period: days, projectId: target.projectId } }
  if (isArtifact(target.kind)) return { path: '/api/materials/artifact', body: { ...scope, kind: target.kind, addressee } }
  if (target.kind === 'protocol') return { path: `/api/meetings/${encodeURIComponent(target.meetingId ?? '')}/protocol` }
  return { path: target.kind === 'map' ? '/api/materials/mindmap' : '/api/materials/deck', body: scope }
}

/** Окно «Материалы»: протокол записи, карта и презентация. Текст можно поправить, скопировать, скачать или сохранить в Obsidian. */
export function MaterialModal() {
  const { material, setMaterial, showToast } = useApp()
  const [days, setDays] = useState('30')
  const [addressee, setAddressee] = useState('')
  const [deck, setDeck] = useState<{ url: string; slides: number } | null>(null)
  const [deckBusy, setDeckBusy] = useState(false)
  const [result, setResult] = useState<MaterialResult | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<ProtocolProgress | null>(null)
  const [saved, setSaved] = useState<{ path: string; url?: string } | null>(null)
  const seq = useRef(0)

  const applyResult = (data: MaterialResult) => {
    setResult(data)
    setText(data.markdown ?? (data.mermaid ? tr('# Карта: {0}\n\n```mermaid\n{1}\n```\n', [data.title, data.mermaid]) : ''))
  }

  const generate = useCallback(async (target: MaterialTarget, period: string, to = '') => {
    const mine = ++seq.current
    setBusy(true); setError(''); setResult(null); setSaved(null); setProgress(null); setDeck(null)
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
        if (state.state === 'error' || !state.result) throw new Error(state.message ?? tr('Не удалось составить протокол'))
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
    setResult(null); setError(''); setSaved(null); setBusy(false); setProgress(null); setDeck(null)
    if (material?.kind === 'digest') setDays(d => (DIGEST_PERIODS.some(p => p.key === d) ? d : 'this_week'))
    else setDays(d => (/^\d+$/.test(d) ? d : '30'))
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
  const artifact = isArtifact(kind) || kind === 'digest'
  const needsPeriod = kind !== 'protocol' && !material.meetingId
  const links = result && artifact ? timeLinks(text, result.refs ?? []) : []
  const fileName = result?.fileName ?? `${kind === 'map' ? tr('Карта — ') : ''}${result?.title ?? material.title}.md`

  const copy = async () => {
    try { await navigator.clipboard.writeText(kind === 'map' && result?.mermaid ? result.mermaid : text); showToast(kind === 'map' ? tr('Код карты скопирован') : tr('Текст скопирован')) }
    catch { showToast(tr('Не удалось скопировать — выделите текст вручную')) }
  }
  const makeDeck = async () => {
    setDeckBusy(true)
    try {
      const out = await api.post<{ url: string; slides: number }>('/api/materials/digest/deck', { period: days, projectId: material.projectId })
      setDeck(out)
    } catch (e) { showToast(errorText(e)) } finally { setDeckBusy(false) }
  }
  const save = async () => {
    try {
      const saveKind = artifact ? kind : kind === 'map' ? 'map' : 'protocol'
      const out = await api.post<{ savedAs: string; obsidianUrl?: string }>('/api/materials/save', { kind: saveKind, name: fileName, content: text })
      setSaved({ path: out.savedAs, url: out.obsidianUrl })
      showToast(tr('Сохранено в Obsidian'))
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
            {needsPeriod && <Segmented small items={kind === 'digest' ? DIGEST_PERIODS : PERIODS} value={days} onChange={v => setDays(v)} />}
            {kind === 'memo' && (
              <input className="material-to" value={addressee} maxLength={200} onChange={e => setAddressee(e.target.value)}
                placeholder={tr('Кому: должность, ФИО')} aria-label={tr('Адресат записки')} />
            )}
            <button className="btn accent s34" disabled={busy} onClick={() => void generate(material, days, addressee)}>
              {busy ? tr('Собираю…') : result ? (kind === 'memo' && !needsPeriod ? tr('Подставить адресата') : tr('Собрать заново')) : kind === 'deck' ? tr('Создать презентацию') : artifact ? tr('Составить') : tr('Построить карту')}
            </button>
          </div>
        )}
        {needsPeriod && artifact && !result && !busy && !error && (
          <div className="muted-text">{tr('Выберите период и нажмите «Составить».')}</div>
        )}
        {!needsPeriod && busy && kind === 'protocol' && (
          <div className="proto-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0} aria-label={tr('Составление протокола')}>
            <div className="proto-line">
              <b>{progress?.label ?? tr('Запускаю…')}</b>
              <span className="num">{progress?.percent ?? 0}%</span>
            </div>
            <div className="track"><i style={{ width: (progress?.percent ?? 0) + '%' }} /></div>
            <div className="proto-sub">
              {progress ? tr('Шаг {0} из {1} · прошло {2} с', [progress.step, progress.steps, progress.elapsed]) : tr('Подготовка')}
              {progress && progress.step === 2 && tr(' · с локальной моделью этот шаг занимает от нескольких секунд до минуты')}
            </div>
          </div>
        )}
        {!needsPeriod && busy && kind !== 'protocol' && <div className="muted-text">{tr('Собираю…')}</div>}
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}

        {result && kind === 'protocol' && (
          <>
            <textarea className="material-text" value={text} onChange={e => setText(e.target.value)} aria-label={tr('Текст протокола')} spellCheck={false} />
            <div className="muted-text" style={{ fontSize: 13 }}>{tr('{0} {1}Текст можно править здесь.', [result.mode === 'model' ? tr('Ход обсуждения написан локальной моделью по фактам записи — проверьте перед отправкой.') : tr('Протокол собран из извлечённых фактов без модели (Ollama не запущена или недоступна).'), ' '])}</div>
          </>
        )}
        {result && artifact && (
          <>
            <textarea className="material-text" value={text} onChange={e => setText(e.target.value)} aria-label={TITLES[kind]} spellCheck={false} />
            <div className="muted-text" style={{ fontSize: 13 }}>{tr('Каждый пункт взят из решений, задач и вопросов записей и помечен таймкодом места в записи.{0} {1} {2} {3} {4}Текст можно править здесь.', [' ', result.mode === 'model' ? tr('Вводный абзац написала локальная модель — проверьте его.') : tr('Документ собран из извлечённых фактов без модели (Ollama не запущена или недоступна).'), kind === 'digest' && tr(' «Закрыто за период» — задачи, отмеченные выполненными в этом периоде; «Требует внимания» — просроченные, в том числе из прежних записей.'), kind === 'followup' && tr(' Это черновик: письмо не отправляется, скопируйте его или скачайте.'), ' '])}</div>
            {links.length > 0 && (
              <details className="doc-times">
                <summary>{tr('Места в записи · {0}', [links.length])}</summary>
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
                <summary>{tr('Не вошло: нет места в записи · {0}', [result.skipped!.length])}</summary>
                <ul>{result.skipped!.map((x, i) => <li key={i}><span>{x.text}</span></li>)}</ul>
              </details>
            )}
          </>
        )}
        {result && kind === 'map' && result.tree && (
          <>
            <ul className="map-tree"><Tree node={result.tree} /></ul>
            <div className="muted-text" style={{ fontSize: 13 }}>{tr('В Obsidian карта сохраняется как схема Mermaid. «Скачать карту» даёт PNG и SVG для презентаций и OPML для XMind, MindNode и OmniOutliner.')}</div>
          </>
        )}
        {result && kind === 'deck' && result.url && (
          <div className="material-deck">
            <div>
              <b>{plural(result.slides ?? 0, 'слайд', 'слайда', 'слайдов')}</b>
              <span className="muted-text"> · {result.mode === 'model' ? tr('слайд «Главное» написала локальная модель') : tr('слайд «Главное» собран из цифр и свежих резюме без модели')}</span>
            </div>
            <a className="btn accent s36" href={result.url} download style={{ textDecoration: 'none' }}>{tr('Скачать .pptx')}</a>
            <div className="muted-text" style={{ fontSize: 13 }}>{tr('Файл открывается в Keynote, PowerPoint и Google Slides. Копия лежит в папке «presentations» кабинета.')}</div>
          </div>
        )}
        {deck && (
          <div className="material-saved">{tr('Презентация готова: ')}{' '}{plural(deck.slides, 'слайд', 'слайда', 'слайдов')}.
            <a href={deck.url} download style={{ marginLeft: 8 }}>{tr('Скачать .pptx')}</a>
          </div>
        )}
        {saved && (
          <div className="material-saved">{tr('Сохранено: ')}{' '}<code>{saved.path}</code>
            {saved.url && <a href={saved.url} style={{ marginLeft: 8 }}>{tr('Открыть в Obsidian ')}{' '}<IconOut /></a>}
          </div>
        )}

        <div className="modal-foot">
          {result && kind !== 'deck' && (
            <>
              <button className="btn s34" onClick={() => void copy()}>{kind === 'map' ? tr('Копировать код') : tr('Копировать')}</button>
              {kind === 'map' && result.tree
                ? <ExportMenu endpoint="/api/export/map" formats={MAP_FORMATS} label={tr('Скачать карту')} up body={() => ({ title: result.title, tree: result.tree })} />
                : <ExportMenu endpoint="/api/export" up body={() => ({ title: result.title, markdown: text, name: fileName.replace(/\.md$/i, '') })} />}
              <button className="btn s34" onClick={() => void save()}>{tr('В Obsidian')}</button>
              {kind === 'digest' && (
                <button className="btn s34" disabled={deckBusy} onClick={() => void makeDeck()}>{deckBusy ? tr('Собираю…') : tr('Презентация')}</button>
              )}
            </>
          )}
          <button className="btn accent s34" onClick={() => setMaterial(null)}>{tr('Закрыть')}</button>
        </div>
      </div>
    </div>
  )
}

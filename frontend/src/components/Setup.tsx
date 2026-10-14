import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { tr, trServer } from '../i18n'

interface Component {
  id: string; title: string; group: 'system' | 'models' | 'extras'; note: string; state: 'ok' | 'missing' | 'error'
  required: boolean; detail: string; mb: number | null; action: '' | 'run' | 'hint'; actionLabel: string
}
interface Job {
  id: string; title: string; state: 'running' | 'done' | 'error' | 'cancelled'; message: string; percent: number | null; done: number; total: number; seconds: number; log: string[]
  stages: string[]; stage: number; note: string; bytes: boolean; speed: number | null; eta: number | null
}
interface Place { key: string; title: string; note: string; path: string; exists: boolean }
interface SetupInfo {
  components: Component[]; folders: { recordingsDir: string; obsidianDir: string; recordingsOk: boolean; obsidianOk: boolean; canPick: boolean }
  disk: { freeGb: number; needGb: number }; places: Place[]; ready: boolean; done: boolean; fresh: boolean; brew: boolean; needsWizard: boolean; job: Job | null
}

const STEPS = [tr('Система'), tr('Модели'), tr('Папки и доступы'), tr('Готово')]
const LLMS: [string, string, number][] = [['qwen3:4b', tr('быстрая, рекомендуется'), 2500], ['qwen3:8b', tr('аккуратнее, но медленнее'), 5200]]
const ASRS: [string, string, number][] = [['turbo', tr('рекомендуется'), 1600], ['large-v3', tr('точнее, дольше и больше'), 3100], ['medium', tr('легче'), 1500]]
const SEEN = 'md-setup-seen'

const gb = (mb: number) => (mb / 1000).toFixed(1)
const size = (mb: number | null) => mb ? tr('≈ {0} ГБ', [gb(mb)]) : ''
const clock = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
const etaText = (sec: number) => sec < 90 ? tr('осталось ≈ {0} с', [Math.max(5, Math.round(sec / 5) * 5)]) : tr('осталось ≈ {0} мин', [Math.ceil(sec / 60)])
const mbText = (bytes: number) => bytes >= 1024 ** 3 ? tr('{0} ГБ', [(bytes / 1024 ** 3).toFixed(1)]) : tr('{0} МБ', [Math.round(bytes / 1024 ** 2)])

/** Один раз за сессию открывает мастер, если сервер считает, что он нужен (чистая установка или не хватает обязательного). */
export function useSetupAutoOpen() {
  const { setSetupOpen } = useApp()
  useEffect(() => {
    let seen = false
    try { seen = sessionStorage.getItem(SEEN) === '1' } catch { /* без памяти сессии откроем */ }
    if (seen) return
    api.get<SetupInfo>('/api/setup').then(info => { if (info.needsWizard) setSetupOpen(true) }).catch(() => { /* под замком PIN или без связи: не мешаем */ })
  }, [setSetupOpen])
}

function Progress({ job }: { job: Job }) {
  const known = job.percent != null
  const amount = job.total && job.bytes ? `${mbText(job.done)} ${tr('из')} ${mbText(job.total)}` : ''
  return (
    <div className="setup-progress" role="status" aria-live="polite">
      {job.stages.length > 1 && (
        <ol className="setup-stages">
          {job.stages.map((name, i) => <li key={name} className={i < job.stage ? 'past' : i === job.stage ? 'now' : ''}>{trServer(name)}</li>)}
        </ol>
      )}
      <div className={'setup-bar' + (known ? '' : ' wait')}><i style={known ? { width: job.percent + '%' } : undefined} /></div>
      <div className="setup-meta">
        <span>{[amount, known ? `${job.percent}%` : '', job.speed ? tr('{0}/с', [mbText(job.speed)]) : ''].filter(Boolean).join(' · ') || tr('Работаю…')}</span>
        <span>{[job.eta != null ? etaText(job.eta) : '', tr('прошло {0}', [clock(job.seconds)])].filter(Boolean).join(' · ')}</span>
      </div>
      {job.note && <span className="hint setup-line" title={job.note}>{trServer(job.note)}</span>}
      {!job.note && job.message && <span className="hint setup-line">{job.message}</span>}
    </div>
  )
}

function Row({ item, job, busy, onRun }: { item: Component; job: Job | null; busy: boolean; onRun: (id: string) => void }) {
  const mine = job && job.id === item.id ? job : null
  const running = mine?.state === 'running'
  const icon = item.state === 'ok' ? '✓' : item.state === 'error' ? '!' : item.required ? '✗' : '–'
  return (
    <div className={'setup-row ' + item.state + (item.required ? '' : ' is-optional')}>
      <span className="setup-icon" aria-hidden>{icon}</span>
      <div className="setup-main">
        <div className="setup-title">{trServer(item.title)}{!item.required && <span className="setup-opt"> · {tr('необязательно')}</span>}</div>
        <span className="hint">{trServer(item.note)}{item.mb && item.state !== 'ok' ? ` · ${size(item.mb)}` : ''}</span>
        {item.state !== 'ok' && item.detail && <span className="hint setup-detail" title={item.detail}>{trServer(item.detail)}</span>}
        {running && <Progress job={mine!} />}
      </div>
      {item.state !== 'ok' && item.action === 'run' && !running && (
        <button className="btn s28 accent" disabled={busy} onClick={() => onRun(item.id)}>{item.state === 'error' ? tr('Повторить') : trServer(item.actionLabel) || tr('Установить')}</button>
      )}
      {item.state !== 'ok' && item.action === 'hint' && <span className="hint setup-hint">{item.id === 'ffmpeg' || item.id === 'ollama_bin' ? tr('Сначала поставьте Homebrew (brew.sh), затем «Проверить заново»') : ''}</span>}
    </div>
  )
}

export function SetupWizard() {
  const { setupOpen, setSetupOpen, setupStep, setSetupStep, showToast, invalidate } = useApp()
  const [info, setInfo] = useState<SetupInfo | null>(null)
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const [rec, setRec] = useState('')
  const [vault, setVault] = useState('')
  const [llmChoice, setLlmChoice] = useState('qwen3:4b')
  const [asrChoice, setAsrChoice] = useState('turbo')
  const loaded = useRef(false)

  const load = useCallback(async () => {
    try {
      const next = await api.get<SetupInfo>('/api/setup')
      setInfo(next)
      if (!loaded.current) { loaded.current = true; setRec(next.folders.recordingsDir); setVault(next.folders.obsidianDir) }
    } catch (e) { setError(errorText(e)) }
  }, [])

  useEffect(() => { if (setupOpen) { loaded.current = false; setStep(setupStep); void load() } }, [setupOpen, setupStep, load])
  const running = info?.job?.state === 'running'
  useEffect(() => {
    if (!setupOpen) return
    const timer = window.setInterval(() => { void load() }, running ? 1000 : 5000)
    return () => window.clearInterval(timer)
  }, [setupOpen, running, load])

  if (!setupOpen) return null
  const close = () => { try { sessionStorage.setItem(SEEN, '1') } catch { /* не страшно */ } setSetupOpen(false); setSetupStep(0) }
  const act = async (call: () => Promise<SetupInfo | unknown>) => {
    setError('')
    try { const next = await call(); if (next && (next as SetupInfo).components) setInfo(next as SetupInfo) } catch (e) { setError(errorText(e)) }
  }
  const run = (id: string) => act(() => api.post('/api/setup/run', { component: id }))
  const pickModels = (llm: string, asr: string) => {
    setLlmChoice(llm); setAsrChoice(asr)
    void act(() => api.post('/api/setup/models', { llm_model: llm === 'qwen3:4b' ? '' : llm, asr_model: asr }))
  }
  const saveFolders = () => act(async () => { const next = await api.post<SetupInfo>('/api/setup/folders', { recordings_dir: rec, obsidian_dir: vault }); showToast(tr('Папки сохранены')); invalidate(); return next })
  const pick = (kind: 'recordings' | 'vault') => act(async () => {
    const res = await api.post<{ path: string }>('/api/setup/pick-folder', { kind, start: kind === 'recordings' ? rec : vault })
    if (res.path) { if (kind === 'recordings') setRec(res.path); else setVault(res.path) }
    return null
  })
  const reveal = (key: string) => act(async () => { await api.post('/api/setup/reveal', { key }); return null })
  const access = () => act(async () => { await api.post('/api/integrations/apple/access'); showToast(tr('Запрос доступа отправлен: подтвердите его в окне macOS')) })
  const finish = async () => { await act(() => api.post('/api/setup/finish')); invalidate(); close() }

  const items = info?.components ?? []
  const group = (g: Component['group']) => items.filter(c => c.group === g)
  const job = info?.job ?? null
  const missing = items.filter(c => c.required && c.state !== 'ok')
  const lowDisk = !!info && info.disk.needGb > 0 && info.disk.freeGb < info.disk.needGb
  const busy = !!running

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) close() }}>
      <div className="modal setup" role="dialog" aria-label={tr('Первый запуск')}>
        <div>
          <h2 style={{ margin: 0 }}>{tr('Первый запуск')}</h2>
          <span className="hint">{tr('Проверим, что нужно для работы, и скачаем недостающее. Ничего не загружается без вашего нажатия.')}</span>
        </div>
        <div className="setup-steps" role="tablist">
          {STEPS.map((name, i) => (
            <button key={name} role="tab" aria-selected={step === i} className={'setup-step' + (step === i ? ' on' : '')} onClick={() => setStep(i)}>{i + 1}. {name}</button>
          ))}
        </div>

        {step === 0 && (
          <div className="setup-pane">
            {group('system').map(c => <Row key={c.id} item={c} job={job} busy={busy} onRun={id => void run(id)} />)}
            <div className="backup-plan">
              <button className="btn s28" onClick={() => void load()}>{tr('Проверить заново')}</button>
              {!!info && <span className="hint">{tr('Свободно на диске: {0} ГБ', [info.disk.freeGb])}</span>}
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="setup-pane">
            <div className="backup-plan setup-choice">
              <label htmlFor="setup-llm">{tr('Модель анализа')}</label>
              <select id="setup-llm" value={llmChoice} disabled={busy} onChange={e => pickModels(e.target.value, asrChoice)}>
                {LLMS.map(([id, note, mb]) => <option key={id} value={id}>{id} · {note} · {size(mb)}</option>)}
              </select>
              <label htmlFor="setup-asr">{tr('Распознавание речи')}</label>
              <select id="setup-asr" value={asrChoice} disabled={busy} onChange={e => pickModels(llmChoice, e.target.value)}>
                {ASRS.map(([id, note, mb]) => <option key={id} value={id}>{id} · {note} · {size(mb)}</option>)}
              </select>
            </div>
            {group('models').map(c => <Row key={c.id} item={c} job={job} busy={busy} onRun={id => void run(id)} />)}
            {lowDisk && info && <div className="error-box">{tr('Свободно {0} ГБ, а для недостающих моделей нужно около {1} ГБ. Освободите место или выберите модели поменьше.', [info.disk.freeGb, info.disk.needGb])}</div>}
            {group('extras').map(c => <Row key={c.id} item={c} job={job} busy={busy} onRun={id => void run(id)} />)}
            {running && <div className="backup-plan"><button className="btn s28" onClick={() => void act(() => api.post('/api/setup/cancel'))}>{tr('Остановить загрузку')}</button><span className="hint">{tr('Начатое не пропадёт: загрузка продолжится с того же места.')}</span></div>}
          </div>
        )}

        {step === 2 && (
          <div className="setup-pane">
            <span className="hint">{tr('Это папки для ваших материалов: программа сюда не устанавливается. Где лежит сама программа, показано на последнем шаге.')}</span>
            {([['recordings', 'setup-rec', tr('Папка с записями'), rec, setRec, info?.folders.recordingsDir, info?.folders.recordingsOk,
               tr('Сюда кладите аудиофайлы (например, из PLAUD). Обработанные автоматически уходят в архивную подпапку.')],
              ['vault', 'setup-vault', tr('Папка заметок в Obsidian'), vault, setVault, info?.folders.obsidianDir, info?.folders.obsidianOk,
               tr('Готовые заметки, протоколы и документы сохраняются сюда.')]] as const).map(([kind, id, label, value, setValue, saved, exists, hint]) => (
              <div className="field" key={id}>
                <label htmlFor={id}>{label}</label>
                <div className="setup-path">
                  <input id={id} type="text" value={value} spellCheck={false} onChange={e => setValue(e.target.value)} />
                  {info?.folders.canPick && <button className="btn s28" disabled={busy} onClick={() => void pick(kind)}>{tr('Выбрать…')}</button>}
                </div>
                <span className="hint">{hint}</span>
                {saved !== undefined && value.trim() === saved && <span className={exists ? 'hint setup-state-ok' : 'hint'}>{exists ? tr('Папка есть') : tr('Папки пока нет: создам при сохранении')}</span>}
              </div>
            ))}
            <div className="backup-plan">
              <button className="btn s28 accent" disabled={!rec.trim() || !vault.trim()} onClick={() => void saveFolders()}>{tr('Создать папки и сохранить')}</button>
              <span className="hint">{info?.folders.canPick ? tr('В окне «Выбрать…» есть кнопка «Новая папка».') : tr('Нужен полный путь, например ~/Documents/Записи.')}</span>
            </div>
            <div className="field">
              <label>{tr('Календарь и Напоминания')}</label>
              <span className="hint">{tr('Необязательно. Нужно для подготовки к встречам и задач в «Напоминаниях»; macOS спросит разрешение.')}</span>
              <div className="backup-plan"><button className="btn s28" onClick={() => void access()}>{tr('Запросить доступ')}</button></div>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="setup-pane">
            {missing.length === 0
              ? <div className="setup-ok">{tr('Всё необходимое на месте. Положите аудиофайл в папку записей и нажмите «Обработать» в кабинете.')}</div>
              : <>
                <div className="error-box">{tr('Не хватает обязательного: {0}. Без этого записи не обработаются.', [missing.map(m => trServer(m.title)).join(', ')])}</div>
                <button className="btn s28" onClick={() => setStep(missing.some(m => m.group === 'system') ? 0 : 1)}>{tr('Вернуться и доделать')}</button>
              </>}
            <div className="setup-places">
              <div className="setup-places-title">{tr('Где что лежит')}</div>
              {(info?.places ?? []).map(pl => (
                <div className="setup-place" key={pl.key}>
                  <div className="setup-main">
                    <div className="setup-title">{trServer(pl.title, true)}</div>
                    <span className="hint">{trServer(pl.note)}</span>
                    <code className="setup-path-text">{pl.path}</code>
                  </div>
                  {info?.folders.canPick && pl.exists && <button className="btn s28" onClick={() => void reveal(pl.key)}>{tr('Показать')}</button>}
                </div>
              ))}
            </div>
            <span className="hint">{tr('Эту проверку можно повторить в любой момент: «Настройки → Данные → Проверка системы».')}</span>
          </div>
        )}

        {error && <div className="error-box">{error}</div>}
        <div className="modal-foot">
          <button className="btn s34" onClick={close}>{tr('Закрыть')}</button>
          {step > 0 && <button className="btn s34" onClick={() => setStep(step - 1)}>{tr('Назад')}</button>}
          {step < 3 && <button className="btn s34 accent" onClick={() => setStep(step + 1)}>{tr('Дальше')}</button>}
          {step === 3 && <button className="btn s34 accent" onClick={() => void finish()}>{tr('Начать работу')}</button>}
        </div>
      </div>
    </div>
  )
}

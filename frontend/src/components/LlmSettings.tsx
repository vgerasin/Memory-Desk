import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { tr, trServer } from '../i18n'

interface LlmInfo {
  current: string; default: string; chosen: string; reachable: boolean; currentInstalled: boolean
  installed: { name: string; size: number; params: string; quant: string }[]
  suggested: { name: string; note: string; installed: boolean }[]
}
interface Check { ok: boolean; seconds: number; message: string; model: string }

const gb = (bytes: number) => (bytes / 1e9).toFixed(1)

/** «Модель анализа» в настройках: какая модель Ollama читает стенограммы, строит чат, подсказки и проверки решений. */
export function LlmSettings({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [info, setInfo] = useState<LlmInfo | null>(null)
  const [own, setOwn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<Check | null>(null)
  const [error, setError] = useState('')

  useEffect(() => { api.get<LlmInfo>('/api/llm').then(setInfo).catch(e => setError(errorText(e))) }, [])
  useEffect(() => { setCheck(null) }, [value])

  const known = !value || (info?.installed.some(m => m.name === value) ?? false) || (info?.suggested.some(m => m.name === value) ?? false)
  const custom = own || (!!info && !known)
  const effective = value || info?.default || 'qwen3:4b'

  const run = async () => {
    setBusy(true); setCheck(null); setError('')
    try { setCheck(await api.post<Check>('/api/llm/check', { model: effective })) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="field llm-settings">
      <label htmlFor="llm-model">{tr('Модель анализа')}</label>
      <span className="hint">{tr('Модель Ollama, которая пишет заметки по стенограмме и отвечает в чате. Новый выбор действует на новые записи, пересборку по шаблону, чат и подсказки; готовые заметки не меняются.')}</span>
      <div className="backup-plan">
        <select id="llm-model" value={custom ? 'custom' : value} onChange={e => {
          if (e.target.value === 'custom') { setOwn(true); return }
          setOwn(false); onChange(e.target.value)
        }}>
          <option value="">{tr('По умолчанию')} ({info?.default ?? 'qwen3:4b'})</option>
          {info?.installed.map(m => (
            <option key={m.name} value={m.name}>{m.name}{m.params ? ` · ${m.params}` : ''}{m.size ? ` · ${gb(m.size)} ${tr('ГБ')}` : ''}</option>
          ))}
          {info?.suggested.filter(s => !s.installed && !info.installed.some(m => m.name === s.name)).map(s => (
            <option key={s.name} value={s.name}>{s.name} ({tr('не скачана')})</option>
          ))}
          <option value="custom">{tr('Другая модель…')}</option>
        </select>
        {custom && (
          <input type="text" value={known ? '' : value} placeholder="qwen3:8b" spellCheck={false}
            aria-label={tr('Имя модели Ollama')} onChange={e => onChange(e.target.value.trim())} />
        )}
        <button className="btn s28" disabled={busy} onClick={() => void run()}>{busy ? tr('Проверяю…') : tr('Проверить')}</button>
      </div>
      {info && !info.reachable && <span className="hint">{tr('Ollama не отвечает: список скачанных моделей недоступен. Имя можно ввести вручную.')}</span>}
      {info?.reachable && value && !info.installed.some(m => m.name === value) && !!value && (
        <span className="hint">{tr('Эта модель ещё не скачана. В Терминале: ')}<code>ollama pull {value}</code></span>
      )}
      {info?.suggested.filter(s => s.name === value).map(s => <span key={s.name} className="hint">{trServer(s.note)}</span>)}
      {check && (
        <span className={'hint' + (check.ok ? '' : ' bad')} role="status">
          {check.ok ? tr('Модель отвечает за {0} с', [check.seconds]) : check.message}
        </span>
      )}
      {error && <span className="hint bad">{error}</span>}
    </div>
  )
}

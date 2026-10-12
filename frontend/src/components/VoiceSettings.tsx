import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { fmtBytes } from '../format'
import type { VoiceState } from '../types'

/** Блок «Голоса» в настройках: включение, состояние локальной модели и её разовая загрузка. */
export function VoiceSettings({ enabled, onToggle }: { enabled: boolean; onToggle: (value: boolean) => void }) {
  const [state, setState] = useState<VoiceState | null>(null)
  const [error, setError] = useState('')
  const running = state?.download.state === 'running'

  const load = useCallback(() => api.get<VoiceState>('/api/voice').then(setState).catch(e => setError(errorText(e))), [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => void load(), 1000)
    return () => clearInterval(timer)
  }, [running, load])

  const download = async () => {
    setError('')
    try { setState(await api.post<VoiceState>('/api/voice/model')) } catch (e) { setError(errorText(e)) }
  }
  const dl = state?.download
  const percent = dl && dl.total ? Math.round((dl.done / dl.total) * 100) : 0

  return (
    <div className="field voice-settings">
      <div className="switch-row">
        <div className="field" style={{ gap: 2 }}>
          <label>Узнавать людей по голосу</label>
          <span className="hint">
            Запись делится на говорящих по звуку, а знакомые голоса получают имена. Всё считается на этом компьютере.
            Голос человека запоминается только по вашему действию в разделе «Люди».
          </span>
        </div>
        <button className={'switch' + (enabled ? ' on' : '')} role="switch" aria-checked={enabled} onClick={() => onToggle(!enabled)}><i /></button>
      </div>
      {state && (
        <div className="voice-status">
          {state.dependencies && state.modelReady && !running && (
            <span className="hint">Голос готов и работает без интернета{state.profiles ? ` · запомнено голосов: ${state.profiles}` : ''}</span>
          )}
          {!(state.dependencies && state.modelReady) && !running && (
            <>
              <span className="hint">
                {state.dependencies ? '' : state.dependenciesReason + '. '}
                Подготовка выполняется один раз и требует интернета: ставятся компоненты и скачивается модель (около 90 МБ с публичной
                страницы Hugging Face, без регистрации). После этого всё работает офлайн.
              </span>
              <button className="btn s28" style={{ alignSelf: 'flex-start' }} onClick={() => void download()}>Подготовить голос</button>
            </>
          )}
          {running && dl?.phase === 'install' && (
            <>
              <div className="voice-bar indeterminate"><i /></div>
              <span className="hint">Устанавливаю компоненты — это занимает несколько минут, окно можно закрыть</span>
            </>
          )}
          {running && dl?.phase !== 'install' && (
            <>
              <div className="voice-bar"><i style={{ width: `${percent}%` }} /></div>
              <span className="hint">Скачиваю модель: {fmtBytes(dl?.done ?? 0)}{dl?.total ? ` из ${fmtBytes(dl.total)}` : ''}</span>
            </>
          )}
          {dl?.state === 'error' && !running && <span className="hint bad">{dl.message}</span>}
        </div>
      )}
      {error && <span className="hint bad">{error}</span>}
    </div>
  )
}

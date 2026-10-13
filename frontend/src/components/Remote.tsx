import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useLock } from '../lock'
import { useApp } from '../store'
import { localeTag, tr } from '../i18n'

interface RemoteDevice { id: string; name: string; created: number; lastSeen: number; lastIp: string; expires: number; current: boolean }
interface RemoteStatus {
  hasPin: boolean; enabled: boolean; running: boolean; port: number; days: number; daysOptions: number[]; transcript: boolean
  hosts: string[]; tls: boolean; urls: string[]; devices: RemoteDevice[]; failures: number; retryAfter: number; hardLimit: number
  error: string; tlsFolder: string; message?: string
}

function when(seconds: number): string {
  if (!seconds) return '—'
  return new Date(seconds * 1000).toLocaleString(localeTag(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Раздел «Доступ с телефона» в настройках: включение, адрес, срок входа, список устройств. Применяется сразу. */
export function RemoteBlock() {
  const { status: lockStatus } = useLock()
  const { showToast } = useApp()
  const [state, setState] = useState<RemoteStatus | null>(null)
  const [pin, setPin] = useState('')
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setState(await api.get<RemoteStatus>('/api/remote')) } catch (e) { setError(errorText(e)) }
  }, [])
  useEffect(() => { void load() }, [load, lockStatus.enabled])

  const run = async (action: () => Promise<RemoteStatus>, done = '') => {
    setBusy(true); setError('')
    try { setState(await action()); if (done) showToast(done) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const put = (body: Record<string, unknown>, done = '') => run(() => api.put<RemoteStatus>('/api/remote', body), done)
  const turnOn = async () => { await put({ enabled: true, pin }, tr('Доступ с телефона включён')); setPin(''); setAsking(false) }
  const copy = (text: string) => { void navigator.clipboard?.writeText(text); showToast(tr('Адрес скопирован')) }

  if (!state) return null
  return (
    <div className="field security remote">
      <label>{tr('Доступ с телефона')}</label>
      <span className="hint">
        {tr('Читать сводки, задачи и решения можно с телефона в той же домашней сети. Только чтение: записать, изменить, удалить, послушать звук или спросить чат с телефона нельзя. Вход по PIN, у каждого телефона свой отзываемый вход. В интернет доступ не открывается никогда.')}
      </span>
      {!state.hasPin && <span className="hint warn-note">{tr('Сначала задайте PIN выше: доступ с телефона открывается только по нему.')}</span>}
      {state.hasPin && (
        <div className="switch-row">
          <span className="hint">
            {state.enabled
              ? (state.running ? tr('Включён и работает.') : tr('Включён, но сервер не запущен.'))
              : (state.failures >= state.hardLimit ? tr('Выключен после неверных PIN: включите заново, когда убедитесь, что это были не вы.') : tr('Выключен.'))}
          </span>
          {state.enabled
            ? <button className="btn s28" disabled={busy} onClick={() => void put({ enabled: false }, tr('Доступ с телефона выключен'))}>{tr('Выключить')}</button>
            : !asking && <button className="btn s28 accent" disabled={busy} onClick={() => setAsking(true)}>{tr('Включить')}</button>}
        </div>
      )}
      {asking && !state.enabled && (
        <div className="pin-form">
          <input type="password" inputMode="numeric" autoComplete="off" placeholder={tr('Введите PIN, чтобы включить')} value={pin}
            maxLength={12} onChange={e => setPin(e.target.value.replace(/\D/g, ''))} aria-label={tr('PIN для включения')} />
          <div className="pin-actions">
            <button className="btn s28 muted" onClick={() => { setAsking(false); setPin(''); setError('') }}>{tr('Отмена')}</button>
            <button className="btn s28 accent" disabled={busy || pin.length < 4} onClick={() => void turnOn()}>{tr('Включить доступ')}</button>
          </div>
        </div>
      )}
      {state.enabled && (
        <>
          <div className="backup-plan">
            <span className="hint">{tr('Адрес для телефона (Safari или Chrome в той же сети Wi-Fi):')}</span>
            {state.urls.length === 0 && <span className="hint">{tr('Адрес компьютера в сети не найден: проверьте подключение к Wi-Fi.')}</span>}
            {state.urls.map(url => (
              <div className="switch-row" key={url}>
                <code>{url}</code>
                <button className="btn s28" onClick={() => copy(url)}>{tr('Скопировать')}</button>
              </div>
            ))}
            {state.error && <div className="error-box" style={{ marginTop: 0 }}>{tr('Не удалось открыть порт {0}: возможно, он занят', [state.port])}</div>}
          </div>
          <div className="backup-plan">
            <label htmlFor="remote-days">{tr('Вход на телефоне действует')}</label>
            <select id="remote-days" value={state.days} onChange={e => void put({ days: Number(e.target.value) })}>
              {state.daysOptions.map(d => <option key={d} value={d}>{d === 1 ? tr('1 день') : tr('{0} дней', [d])}</option>)}
            </select>
          </div>
          <div className="switch-row">
            <span className="hint">{tr('Показывать на телефоне стенограмму разговоров (по умолчанию выключено: это самое личное)')}</span>
            <button className={'switch' + (state.transcript ? ' on' : '')} role="switch" aria-checked={state.transcript} disabled={busy}
              aria-label={tr('Стенограмма на телефоне')} onClick={() => void put({ transcript: !state.transcript })}><i /></button>
          </div>
          <div className="backup-plan">
            <label>{tr('Устройства')}</label>
            {state.devices.length === 0 && <span className="hint">{tr('Ни один телефон пока не входил.')}</span>}
            {state.devices.map(d => (
              <div className="switch-row" key={d.id}>
                <span className="hint">
                  <strong>{d.name}</strong> · {d.lastIp} · {tr('был в сети')} {when(d.lastSeen)} · {tr('вход до')} {when(d.expires)}
                </span>
                <button className="btn s28 danger" disabled={busy} onClick={() => void run(() => api.del<RemoteStatus>(`/api/remote/devices/${d.id}`), tr('Устройство отключено'))}>{tr('Отключить')}</button>
              </div>
            ))}
            {state.devices.length > 1 && (
              <button className="btn s28" disabled={busy} onClick={() => void run(() => api.post<RemoteStatus>('/api/remote/revoke-all', {}), tr('Все устройства отключены'))}>{tr('Отключить все')}</button>
            )}
          </div>
          {!state.tls && (
            <span className="hint warn-note">
              {tr('Соединение по домашней сети идёт без шифрования (http): PIN и сводки видны тому, кто слушает ваш Wi-Fi. Включайте только в доверенной сети. Чтобы шифровать, положите сертификат cert.pem и ключ key.pem в папку {0} и перезапустите приложение (подробности в docs/REMOTE-THREATS.md).', [state.tlsFolder])}
            </span>
          )}
        </>
      )}
      <span className="hint">{tr('После {0} неверных PIN подряд доступ с телефона выключается сам. Смена или снятие PIN отключает все телефоны. Входы, неверные PIN и открытые записи видны в журнале действий.', [state.hardLimit])}</span>
      {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
    </div>
  )
}

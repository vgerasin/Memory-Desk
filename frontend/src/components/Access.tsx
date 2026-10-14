import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { localeTag, tr } from '../i18n'

interface Token { id: string; name: string; kind: 'cli' | 'mcp'; kindLabel: string; write: boolean; created: string; lastUsed: string; hint: string }
interface AccessList { items: Token[]; limit: number; launcher: string; url: string }
interface Made extends Token { token: string }

const when = (ts: string) => ts ? new Date(ts).toLocaleString(localeTag(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''

/** «Доступ для программ» в настройках → Безопасность: токены для командной строки и MCP. Токен виден один раз, при создании. */
export function AccessBlock() {
  const { showToast } = useApp()
  const [data, setData] = useState<AccessList | null>(null)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'cli' | 'mcp'>('mcp')
  const [write, setWrite] = useState(false)
  const [made, setMade] = useState<Made | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => { api.get<AccessList>('/api/access').then(setData).catch(e => setError(errorText(e))) }, [])
  useEffect(load, [load])

  const create = async () => {
    setBusy(true); setError('')
    try {
      setMade(await api.post<Made>('/api/access', { name, kind, write }))
      setName(''); setWrite(false); load()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const revoke = async (token: Token) => {
    try { await api.del('/api/access/' + token.id); showToast(tr('Токен отозван')); if (made?.id === token.id) setMade(null); load() }
    catch (e) { setError(errorText(e)) }
  }
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); showToast(tr('Скопировано')) } catch { showToast(tr('Не удалось скопировать — выделите текст вручную')) }
  }
  const launcher = data?.launcher ?? './memory-desk'
  const snippet = made && (made.kind === 'mcp'
    ? JSON.stringify({ mcpServers: { 'memory-desk': { command: launcher, args: ['mcp'], env: { MEMORY_DESK_TOKEN: made.token } } } }, null, 2)
    : `${launcher} login --token ${made.token}`)

  return (
    <div className="field access">
      <label>{tr('Доступ для программ')}</label>
      <span className="hint">{tr('Командная строка и MCP-серверы (например, для Claude Desktop) ходят в кабинет со своим токеном. Токен только читает записи, а право записи (добавить заметку, положить файл в очередь) включается отдельно. Править, удалять и выгружать записи токен не может. Каждое обращение видно в журнале, отозвать токен можно в один клик. PIN на токены не распространяется.')}</span>
      {data?.items.map(t => (
        <div key={t.id} className="switch-row token-row">
          <span><b>{t.name}</b> · {t.kind === 'mcp' ? 'MCP' : tr('Командная строка')} · {t.write ? tr('чтение и запись') : tr('только чтение')}<br />
            <span className="hint">{t.hint} · {tr('создан')} {when(t.created)} · {t.lastUsed ? tr('использован {0}', [when(t.lastUsed)]) : tr('не использовался')}</span></span>
          <button className="btn s28 danger" onClick={() => void revoke(t)}>{tr('Отозвать')}</button>
        </div>
      ))}
      {data && !data.items.length && <span className="hint">{tr('Токенов пока нет.')}</span>}
      <div className="backup-plan">
        <input type="text" value={name} maxLength={60} placeholder={tr('Название, например «Claude Desktop»')} aria-label={tr('Название токена')} onChange={e => setName(e.target.value)} />
        <select value={kind} onChange={e => setKind(e.target.value as 'cli' | 'mcp')} aria-label={tr('Для чего токен')}>
          <option value="mcp">MCP</option>
          <option value="cli">{tr('Командная строка')}</option>
        </select>
        <button className="btn s28 accent" disabled={busy || (data ? data.items.length >= data.limit : false)} onClick={() => void create()}>{tr('Выпустить токен')}</button>
      </div>
      <div className="switch-row">
        <span className="hint">{tr('Разрешить запись: добавлять заметки к записям и класть файлы в очередь обработки')}</span>
        <button className={'switch' + (write ? ' on' : '')} role="switch" aria-checked={write} aria-label={tr('разрешить запись')} onClick={() => setWrite(!write)}><i /></button>
      </div>
      {made && (
        <div className="token-made">
          <span className="hint warn-note">{tr('Токен показан один раз: сохраните его сейчас. Потеряете — выпустите новый.')}</span>
          <code className="token-code">{made.token}</code>
          <span className="hint">{made.kind === 'mcp' ? tr('Вставьте в настройки MCP-клиента (Claude Desktop: Settings → Developer → Edit Config):') : tr('Выполните в Терминале:')}</span>
          <pre className="token-code">{snippet}</pre>
          <div className="pin-actions">
            <button className="btn s28" onClick={() => void copy(made.token)}>{tr('Копировать токен')}</button>
            <button className="btn s28" onClick={() => void copy(snippet ?? '')}>{tr('Копировать настройку')}</button>
            <button className="btn s28 muted" onClick={() => setMade(null)}>{tr('Готово, скрыть')}</button>
          </div>
        </div>
      )}
      {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
    </div>
  )
}

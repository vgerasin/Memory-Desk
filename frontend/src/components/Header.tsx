import { useEffect, useState } from 'react'
import { api, ApiError, errorText } from '../api'
import { navigate, useRoute } from '../router'
import { useApp } from '../store'
import type { ReindexStatus, SystemStatus } from '../types'
import { IconGear, IconRefresh, IconSearch, Segmented } from './ui'

const TABS = [
  { key: '/', label: 'Сегодня' }, { key: '/inbox', label: 'Входящие' }, { key: '/records', label: 'Записи' },
  { key: '/projects', label: 'Проекты' }, { key: '/people', label: 'Люди' }, { key: '/tasks', label: 'Задачи' },
  { key: '/analytics', label: 'Аналитика' },
]
const STATE_COLOR = { ok: '#34C759', warn: '#FF9F0A', bad: '#FF3B30' }

function activeTab(path: string): string {
  if (path === '/archive') return '/records'
  const hit = TABS.find(t => t.key !== '/' && (path === t.key || path.startsWith(t.key + '/')))
  return hit ? hit.key : '/'
}

function SystemPopover({ status }: { status: SystemStatus | null }) {
  const { theme, toggleTheme, setOpenMenu, setSettingsOpen, setStorageOpen, showToast, invalidate } = useApp()
  const run = async (path: string, fallback: string) => {
    try { const r = await api.post<{ message?: string }>(path); showToast(r.message || fallback); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  const [reindex, setReindex] = useState<ReindexStatus | null>(null)
  const reindexRunning = reindex?.state === 'running'
  useEffect(() => {
    let alive = true
    api.get<ReindexStatus>('/api/system/reindex').then(r => { if (alive) setReindex(r) }).catch(() => undefined)
    return () => { alive = false }
  }, [])
  useEffect(() => {
    if (!reindexRunning) return
    const timer = window.setInterval(() => {
      api.get<ReindexStatus>('/api/system/reindex').then(r => {
        setReindex(r)
        if (r.state !== 'running') invalidate()
      }).catch(() => undefined)
    }, 600)
    return () => window.clearInterval(timer)
  }, [reindexRunning, invalidate])
  const startReindex = async () => {
    try {
      await api.post('/api/system/reindex')
      setReindex(await api.get<ReindexStatus>('/api/system/reindex'))
    } catch (e) {
      // 404 здесь значит, что запущен сервер старой версии: интерфейс уже обновлён, а сервер нет
      showToast(e instanceof ApiError && e.status === 404
        ? 'Сервер запущен со старой версией кода. Закройте его и запустите start.command заново' : errorText(e))
    }
  }
  const pct = reindex && reindex.total > 0 ? Math.min(100, Math.round(reindex.done / reindex.total * 100)) : 0
  return (
    <div className="sys" role="dialog" aria-label="Состояние системы">
      <div className="sys-title">Состояние системы</div>
      <div className="sys-sub">Всё работает локально · 127.0.0.1</div>
      <div className="sys-rows">
        {(status?.rows ?? []).map(r => (
          <div className="sys-row" key={r.key}>
            <span className="name"><i style={{ background: STATE_COLOR[r.state] }} />{r.name}</span>
            <span className="val">{r.value}</span>
          </div>
        ))}
        {!status && <div className="sys-row"><span className="val">Проверяю…</span></div>}
      </div>
      <div className="sys-buttons">
        <button className="sys-btn" onClick={() => { setOpenMenu(null); setSettingsOpen(true) }}>Настройки</button>
        <button className="sys-btn" onClick={() => run('/api/system/backup', 'Резервная копия создана')}>Резервная копия</button>
      </div>
      <div className="sys-buttons">
        <button className="sys-btn" onClick={() => void startReindex()} disabled={reindexRunning}>{reindexRunning ? 'Идёт…' : 'Переиндексировать'}</button>
        <button className="sys-btn" onClick={toggleTheme}>{theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}</button>
      </div>
      <div className="sys-buttons">
        <button className="sys-btn" onClick={() => { setOpenMenu(null); setStorageOpen(true) }}>Хранилище</button>
      </div>
      {reindex && ['running', 'done', 'error'].includes(reindex.state) && (
        <div className={'sys-reindex ' + reindex.state} role="status" aria-live="polite">
          {reindex.state === 'running' && (
            <>
              <div className="sys-reindex-line"><span>Переиндексация · {reindex.message.toLowerCase()}</span><span className="num">{reindex.total ? pct + '%' : ''}</span></div>
              <div className="sys-reindex-bar"><i style={{ width: (reindex.total ? pct : 4) + '%' }} /></div>
              <div className="sys-reindex-sub">{reindex.total ? `${reindex.done} из ${reindex.total} фрагментов · ` : ''}{reindex.records ? `${reindex.records} записей · ` : ''}{reindex.seconds} с</div>
            </>
          )}
          {reindex.state === 'done' && (
            <div className="sys-reindex-line"><span>✓ Индекс перестроен: {reindex.records} записей, {reindex.total} фрагментов</span><span className="num">{String(reindex.seconds).replace(".", ",")} с</span></div>
          )}
          {reindex.state === 'error' && <div className="sys-reindex-line"><span>Не удалось: {reindex.message}</span></div>}
        </div>
      )}
    </div>
  )
}

export function Header() {
  const route = useRoute()
  const { state, wide, openMenu, setOpenMenu, chat, showToast, invalidate, version } = useApp()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [scanning, setScanning] = useState(false)
  const sysOpen = openMenu === 'sys'
  const tab = activeTab(route.path)

  useEffect(() => {
    let alive = true
    api.get<SystemStatus>('/api/system').then(s => { if (alive) setStatus(s) }).catch(() => undefined)
    return () => { alive = false }
  }, [sysOpen, version, state.indexState])

  const scan = async () => {
    if (scanning) return
    setScanning(true)
    try {
      const result = await api.post<{ message: string }>('/api/jobs/scan')
      showToast(result.message)
      invalidate()
    } catch (e) { showToast(errorText(e)) } finally { setScanning(false) }
  }

  const running = state.running
  return (
    <header className="header">
      <div className="header-in">
        <div className="brand" onClick={() => navigate('/')}>
          <div className="brand-mark"><i /></div>
          {wide && <span className="brand-name">Memory Desk</span>}
        </div>
        <Segmented items={TABS.map(t => ({ ...t, badge: t.key === '/inbox' ? state.inboxCount : null }))}
          value={tab} onChange={key => navigate(key)} />
        <div className="header-spacer" />
        <div className="header-actions">
          <button className="hbtn" onClick={() => chat.openChat()} aria-label="Спросить архив">
            <IconSearch />
            {wide && <><span>Спросить архив</span><kbd>⌘K</kbd></>}
          </button>
          {running && (
            <button className="hbtn proc" onClick={() => navigate('/inbox')} title="Идёт обработка">
              <span className="ring" style={{ background: `conic-gradient(currentColor ${running.percent}%, color-mix(in srgb, currentColor 25%, transparent) 0)` }} />
              <span className="num">{Math.floor(running.percent)}%</span>
            </button>
          )}
          <button className="hbtn primary" onClick={scan} disabled={scanning} aria-label="Обработать новые">
            <IconRefresh />
            {wide && <span>Обработать новые</span>}
          </button>
          <div className={'dd' + (sysOpen ? ' open' : '')}>
            <button className="hbtn icon" onClick={() => setOpenMenu(sysOpen ? null : 'sys')} title="Состояние системы" aria-label="Состояние системы">
              <IconGear />
              <span className="status-dot" style={{ background: STATE_COLOR[status?.overall === 'bad' ? 'warn' : status?.overall ?? 'ok'] }} />
            </button>
            {sysOpen && <SystemPopover status={status} />}
          </div>
        </div>
      </div>
    </header>
  )
}

import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { fmtBytes } from '../format'
import type { AppleStatus, BackupItem, Settings } from '../types'
import { AppearanceSettings } from './AppearanceSettings'
import { DictionarySettings } from './DictionarySettings'
import { TemplateSettings } from './TemplateSettings'
import { VoiceSettings } from './VoiceSettings'

export function SettingsModal() {
  const { settingsOpen, setSettingsOpen, showToast, invalidate } = useApp()
  const [form, setForm] = useState<Settings | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [pending, setPending] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [apple, setApple] = useState<AppleStatus | null>(null)
  const [appleBusy, setAppleBusy] = useState(false)

  useEffect(() => {
    if (!settingsOpen) return
    setError('')
    api.get<Settings>('/api/settings').then(setForm).catch(e => setError(errorText(e)))
    api.get<{ items: BackupItem[] }>('/api/system/backups').then(r => setBackups(r.items)).catch(() => setBackups([]))
    api.get<AppleStatus>('/api/integrations/apple').then(setApple).catch(() => setApple(null))
    setPending('')
  }, [settingsOpen])

  if (!settingsOpen) return null
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => setForm(f => f ? { ...f, [key]: value } : f)

  const save = async () => {
    if (!form) return
    setSaving(true); setError('')
    try {
      await api.put('/api/settings', {
        recordings_dir: form.recordings_dir, obsidian_dir: form.obsidian_dir, archive_dir: form.archive_dir,
        retention_days: Number(form.retention_days) || 14, auto_process: form.auto_process, diarize: form.diarize, voice_enabled: form.voice_enabled, calendar_source: form.calendar_source,
        apple_calendar: form.apple_calendar, reminders_enabled: form.reminders_enabled,
        reminders_list: form.reminders_list, reminders_scope: form.reminders_scope, my_name: form.my_name,
        notify: form.notify, morning_brief: form.morning_brief, morning_hour: Number(form.morning_hour) || 0, backup_auto: form.backup_auto,
        backup_interval_hours: Number(form.backup_interval_hours) || 24, backup_keep: Number(form.backup_keep) || 7,
      })
      showToast('Настройки сохранены')
      invalidate()
      setSettingsOpen(false)
    } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }

  const accessLabel: Record<string, string> = {
    authorized: 'разрешён', denied: 'запрещён в системе', notDetermined: 'ещё не запрашивался',
    restricted: 'ограничен', writeOnly: 'только запись', unavailable: 'недоступен',
  }
  const dot = (v?: string) => (v === 'authorized' ? '#34C759' : v === 'notDetermined' ? '#FF9F0A' : '#FF3B30')
  const needsAccess = apple?.available && (apple.events !== 'authorized' || apple.reminders !== 'authorized')
  const appleCall = async (path: string, ok?: string) => {
    setAppleBusy(true); setError('')
    try {
      const r = await api.post<AppleStatus & { summary?: Record<string, number> }>(path)
      setApple(r)
      if (ok) {
        const sum = r.summary
        showToast(sum ? `${ok}: создано ${sum.created}, обновлено ${sum.updated + sum.completedInApp + sum.completedInReminders}` : ok)
      }
      invalidate()
    } catch (e) { setError(errorText(e)); api.get<AppleStatus>('/api/integrations/apple').then(setApple).catch(() => undefined) }
    finally { setAppleBusy(false) }
  }

  const restore = async (name: string) => {
    setRestoring(true); setError('')
    try {
      const r = await api.post<{ message: string }>('/api/system/restore', { name })
      showToast(r.message)
      invalidate()
      setSettingsOpen(false)
    } catch (e) { setError(errorText(e)) } finally { setRestoring(false); setPending('') }
  }

  const dirField = (key: 'recordings_dir' | 'obsidian_dir' | 'archive_dir', label: string, hint: string) => (
    <div className="field">
      <label htmlFor={key}>{label}</label>
      <input id={key} type="text" value={form?.[key] ?? ''} onChange={e => set(key, e.target.value)} spellCheck={false} />
      <span className={'hint' + (form?.exists && form.exists[key] === false ? ' bad' : '')}>
        {form?.exists && form.exists[key] === false ? 'Папка не найдена — будет создана при первом использовании или укажите другую' : hint}
      </span>
    </div>
  )

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setSettingsOpen(false) }}>
      <div className="modal" role="dialog" aria-label="Настройки">
        <div>
          <div className="h3">Настройки</div>
          <div className="subtitle">Всё хранится на этом компьютере</div>
        </div>
        {!form && !error && <div className="subtitle">Загрузка…</div>}
        {form && (
          <>
            <AppearanceSettings />
            {dirField('recordings_dir', 'Папка записей', 'Сюда PLAUD или вы сами кладёте аудиофайлы')}
            {dirField('obsidian_dir', 'Папка Obsidian', 'Готовые заметки и стенограммы')}
            {dirField('archive_dir', 'Архив обработанных записей', 'Куда переносится аудио после обработки')}
            <div className="field">
              <label htmlFor="retention">Хранить аудио, дней</label>
              <input id="retention" type="number" min={1} max={3650} value={form.retention_days}
                onChange={e => set('retention_days', Number(e.target.value))} />
            </div>
            <div className="field">
              <label htmlFor="calendar">Календарь (ICS-файл или ссылка)</label>
              <input id="calendar" type="text" value={form.calendar_source} placeholder="Необязательно"
                onChange={e => set('calendar_source', e.target.value)} spellCheck={false} />
              <span className="hint">Нужен для карточки «Следующая встреча». Без календаря показывается последняя запись.</span>
            </div>
            <div className="switch-row">
              <div className="field" style={{ gap: 2 }}>
                <label>Обрабатывать новые записи автоматически</label>
                <span className="hint">Файл берётся в работу через 10 секунд после появления в папке</span>
              </div>
              <button className={'switch' + (form.auto_process ? ' on' : '')} role="switch" aria-checked={form.auto_process}
                onClick={() => set('auto_process', !form.auto_process)}><i /></button>
            </div>
            <div className="switch-row">
              <div className="field" style={{ gap: 2 }}>
                <label>Уведомлять, когда запись готова</label>
                <span className="hint">Системное уведомление macOS: «Запись готова, проверьте 3 задачи»</span>
              </div>
              <button className={'switch' + (form.notify ? ' on' : '')} role="switch" aria-checked={form.notify}
                onClick={() => set('notify', !form.notify)}><i /></button>
            </div>
            <div className="switch-row">
              <div className="field" style={{ gap: 2 }}>
                <label>Утренняя сводка</label>
                <span className="hint">Одно уведомление в день: просроченное, встречи на сегодня, задачи на неделю. Требует включённых уведомлений</span>
              </div>
              <button className={'switch' + (form.morning_brief ? ' on' : '')} role="switch" aria-checked={form.morning_brief}
                onClick={() => set('morning_brief', !form.morning_brief)}><i /></button>
            </div>
            {form.morning_brief && (
              <div className="backup-plan">
                <label htmlFor="morning-hour">Не раньше</label>
                <select id="morning-hour" value={form.morning_hour} onChange={e => set('morning_hour', Number(e.target.value))}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                </select>
              </div>
            )}
            <TemplateSettings value={form.default_template} onChange={id => set('default_template', id)} />
            <DictionarySettings />
            <div className="apple-box">
              <div className="field" style={{ gap: 2 }}>
                <label>Календарь и Напоминания (macOS)</label>
                <span className="hint">Доступ выдаёт сама система, данные остаются на этом компьютере</span>
              </div>
              {apple && !apple.available && <span className="hint bad">{apple.reason}</span>}
              {apple?.available && (
                <div className="apple-state">
                  <span><i style={{ background: dot(apple.events) }} />Календарь: {accessLabel[apple.events] ?? apple.events}</span>
                  <span><i style={{ background: dot(apple.reminders) }} />Напоминания: {accessLabel[apple.reminders] ?? apple.reminders}</span>
                </div>
              )}
              {needsAccess && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className="btn s30" disabled={appleBusy} onClick={() => void appleCall('/api/integrations/apple/access')}>Разрешить доступ</button>
                  {(apple?.events === 'denied' || apple?.reminders === 'denied') &&
                    <span className="hint">Если окно не появилось: Системные настройки → Конфиденциальность и безопасность → Календари / Напоминания → включите для Терминала</span>}
                </div>
              )}
              <div className="switch-row">
                <div className="field" style={{ gap: 2 }}>
                  <label>Брать встречи из Календаря</label>
                  <span className="hint">«Ближайшая встреча», подсказка события для записи, кнопка «В Календарь»</span>
                </div>
                <button className={'switch' + (form.apple_calendar ? ' on' : '')} role="switch" aria-checked={form.apple_calendar}
                  onClick={() => set('apple_calendar', !form.apple_calendar)}><i /></button>
              </div>
              <div className="switch-row">
                <div className="field" style={{ gap: 2 }}>
                  <label>Отправлять подтверждённые задачи в Напоминания</label>
                  <span className="hint">Отметка «выполнено» синхронизируется в обе стороны</span>
                </div>
                <button className={'switch' + (form.reminders_enabled ? ' on' : '')} role="switch" aria-checked={form.reminders_enabled}
                  onClick={() => set('reminders_enabled', !form.reminders_enabled)}><i /></button>
              </div>
              {form.reminders_enabled && (
                <>
                  <div className="field">
                    <label htmlFor="rem-list">Список в Напоминаниях</label>
                    <input id="rem-list" type="text" value={form.reminders_list} onChange={e => set('reminders_list', e.target.value)} />
                  </div>
                  <div className="field">
                    <label>Какие задачи отправлять</label>
                    <div className="seg small" role="tablist" style={{ alignSelf: 'flex-start' }}>
                      <button role="tab" aria-selected={form.reminders_scope === 'mine'} className={form.reminders_scope === 'mine' ? 'on' : ''}
                        onClick={() => set('reminders_scope', 'mine')}>Мои и без ответственного</button>
                      <button role="tab" aria-selected={form.reminders_scope === 'all'} className={form.reminders_scope === 'all' ? 'on' : ''}
                        onClick={() => set('reminders_scope', 'all')}>Все</button>
                    </div>
                  </div>
                  <div className="field">
                    <label htmlFor="my-name">Как вас называют в записях</label>
                    <input id="my-name" type="text" value={form.my_name} placeholder="Например, Валерий" onChange={e => set('my_name', e.target.value)} />
                    <span className="hint">Задачи с этим ответственным считаются вашими (кроме «Вы», «Я» и без ответственного)</span>
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button className="btn s30" disabled={appleBusy} onClick={() => void appleCall('/api/integrations/reminders/sync', 'Синхронизировано')}>Синхронизировать сейчас</button>
                    {apple?.lastSync?.error && <span className="hint bad">{apple.lastSync.error}</span>}
                  </div>
                  <span className="hint">Сначала сохраните настройки, затем запускайте синхронизацию.</span>
                </>
              )}
            </div>
            <div className="switch-row">
              <div className="field" style={{ gap: 2 }}>
                <label>Определять говорящих при обработке</label>
                <span className="hint">Локальная модель оценивает по тексту, кто говорит. Добавляет около минуты на 25 минут записи; имена назначаются вручную</span>
              </div>
              <button className={'switch' + (form.diarize ? ' on' : '')} role="switch" aria-checked={form.diarize}
                onClick={() => set('diarize', !form.diarize)}><i /></button>
            </div>
            <VoiceSettings enabled={form.voice_enabled} onToggle={v => set('voice_enabled', v)} />
            <div className="field">
              <label>Резервные копии базы</label>
              <div className="switch-row">
                <span className="hint">Делать копии автоматически</span>
                <button className={'switch' + (form.backup_auto ? ' on' : '')} role="switch" aria-checked={form.backup_auto}
                  onClick={() => set('backup_auto', !form.backup_auto)}><i /></button>
              </div>
              {form.backup_auto && (
                <div className="backup-plan">
                  <label htmlFor="backup-interval">Раз в</label>
                  <select id="backup-interval" value={form.backup_interval_hours} onChange={e => set('backup_interval_hours', Number(e.target.value))}>
                    {[[6, '6 часов'], [12, '12 часов'], [24, 'сутки'], [72, '3 дня'], [168, 'неделю']].map(([v, label]) =>
                      <option key={v} value={v}>{label}</option>)}
                    {![6, 12, 24, 72, 168].includes(form.backup_interval_hours) && <option value={form.backup_interval_hours}>{form.backup_interval_hours} ч</option>}
                  </select>
                  <label htmlFor="backup-keep">хранить</label>
                  <input id="backup-keep" type="number" min={1} max={60} value={form.backup_keep}
                    onChange={e => set('backup_keep', Number(e.target.value))} />
                  <span className="hint">копий</span>
                </div>
              )}
              {backups.length === 0 && <span className="hint">Копий пока нет. Первая появится автоматически или создайте её в окне «Состояние системы».</span>}
              {backups.slice(0, 5).map(b => (
                <div className="backup-row" key={b.name}>
                  <span className="backup-name">
                    {new Date(b.createdAt).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    <span className="hint"> · {fmtBytes(b.size)}</span>
                  </span>
                  {pending === b.name
                    ? (
                      <span className="backup-confirm">
                        <button className="btn s28 muted" onClick={() => setPending('')}>Нет</button>
                        <button className="btn s28 accent" disabled={restoring} onClick={() => void restore(b.name)}>Да, восстановить</button>
                      </span>
                    )
                    : <button className="btn s28" onClick={() => setPending(b.name)}>Восстановить</button>}
                </div>
              ))}
              {pending && <span className="hint">Текущее состояние базы будет сохранено в новую копию до восстановления. Заметки Obsidian не меняются.</span>}
            </div>
          </>
        )}
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        <div className="modal-foot">
          <button className="btn s34" onClick={() => setSettingsOpen(false)}>Отмена</button>
          <button className="btn accent s34" onClick={save} disabled={!form || saving}>Сохранить</button>
        </div>
      </div>
    </div>
  )
}

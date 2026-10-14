import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { fmtBytes } from '../format'
import type { AppleStatus, BackupItem, Settings } from '../types'
import { AppearanceSettings } from './AppearanceSettings'
import { DictionarySettings } from './DictionarySettings'
import { AsrSettings } from './AsrSettings'
import { TemplateSettings } from './TemplateSettings'
import { VoiceSettings } from './VoiceSettings'
import { SecurityBlock } from './Security'
import { RemoteBlock } from './Remote'
import { currentLang, LANGS, localeTag, setLang, tr } from '../i18n'
import type { Lang } from '../i18n'

type Tab = 'general' | 'records' | 'templates' | 'speech' | 'calendar' | 'data' | 'security'
const TABS: Tab[] = ['general', 'records', 'templates', 'speech', 'calendar', 'data', 'security']

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
  const [tab, setTab] = useState<Tab>(() => {
    try { const saved = localStorage.getItem('md-settings-tab'); if (saved && TABS.includes(saved as Tab)) return saved as Tab } catch { /* без памяти браузера — первая вкладка */ }
    return 'general'
  })
  const pickTab = (next: Tab) => {
    setTab(next)
    try { localStorage.setItem('md-settings-tab', next) } catch { /* не страшно */ }
  }

  useEffect(() => {
    if (!settingsOpen) return
    setError('')
    api.get<Settings>('/api/settings').then(r => setForm({ ...r, ui_lang: currentLang() })).catch(e => setError(errorText(e)))
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
        backup_on_change: form.backup_on_change, note_writeback: form.note_writeback, mirror_enabled: form.mirror_enabled, suggestions_auto: form.suggestions_auto, dossier_model: form.dossier_model, ui_lang: form.ui_lang,
        asr_model: form.asr_model, asr_hint: form.asr_hint, asr_prep: form.asr_prep,
      })
      showToast(tr('Настройки сохранены'))
      invalidate()
      setSettingsOpen(false)
      if (form.ui_lang && form.ui_lang !== currentLang()) setLang(form.ui_lang as Lang)
    } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }

  const accessLabel: Record<string, string> = {
    authorized: tr('разрешён'), denied: tr('запрещён в системе'), notDetermined: tr('ещё не запрашивался'),
    restricted: tr('ограничен'), writeOnly: tr('только запись'), unavailable: tr('недоступен'),
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
        showToast(sum ? tr('{0}: создано {1}, обновлено {2}', [ok, sum.created, sum.updated + sum.completedInApp + sum.completedInReminders]) : ok)
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
        {form?.exists && form.exists[key] === false ? tr('Папка не найдена — будет создана при первом использовании или укажите другую') : hint}
      </span>
    </div>
  )

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setSettingsOpen(false) }}>
      <div className="modal settings" role="dialog" aria-label={tr('Настройки')}>
        <div className="set-head">
          <div className="h3">{tr('Настройки')}</div>
          <div className="subtitle">{tr('Всё хранится на этом компьютере')}</div>
        </div>
        {!form && !error && <div className="subtitle set-head">{tr('Загрузка…')}</div>}
        {form && (
          <div className="set-body">
            <div className="set-nav" role="tablist" aria-orientation="vertical">
            <button key="general" role="tab" aria-selected={tab === 'general'} className={'set-tab' + (tab === 'general' ? ' on' : '')} onClick={() => pickTab('general')}>{tr('Основные')}</button>
            <button key="records" role="tab" aria-selected={tab === 'records'} className={'set-tab' + (tab === 'records' ? ' on' : '')} onClick={() => pickTab('records')}>{tr('Записи')}</button>
            <button key="templates" role="tab" aria-selected={tab === 'templates'} className={'set-tab' + (tab === 'templates' ? ' on' : '')} onClick={() => pickTab('templates')}>{tr('Шаблоны')}</button>
            <button key="speech" role="tab" aria-selected={tab === 'speech'} className={'set-tab' + (tab === 'speech' ? ' on' : '')} onClick={() => pickTab('speech')}>{tr('Речь')}</button>
            <button key="calendar" role="tab" aria-selected={tab === 'calendar'} className={'set-tab' + (tab === 'calendar' ? ' on' : '')} onClick={() => pickTab('calendar')}>{tr('Календарь')}</button>
            <button key="data" role="tab" aria-selected={tab === 'data'} className={'set-tab' + (tab === 'data' ? ' on' : '')} onClick={() => pickTab('data')}>{tr('Данные')}</button>
            <button key="security" role="tab" aria-selected={tab === 'security'} className={'set-tab' + (tab === 'security' ? ' on' : '')} onClick={() => pickTab('security')}>{tr('Безопасность')}</button>
            </div>
            <div className="set-pane">
            {tab === 'general' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Внешний вид, язык, уведомления и сводка по утрам')}</div>
                <AppearanceSettings />
                <div className="field">
                  <label>{tr('Язык интерфейса')}</label>
                  <select className="lang-select" value={form.ui_lang ?? currentLang()} onChange={e => set('ui_lang', e.target.value)}>
                    {LANGS.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                  <span className="hint">{tr('Заметки, стенограммы и подсказки модели остаются на русском: от этого зависит разбор разделов заметки. Меняется только интерфейс; страница перезагрузится.')}</span>
                </div>
                <div className="field">
                  <label htmlFor="my-name">{tr('Как вас называют в записях')}</label>
                  <input id="my-name" type="text" value={form.my_name} placeholder={tr('Например, Валерий')} onChange={e => set('my_name', e.target.value)} />
                  <span className="hint">{tr('Задачи с этим ответственным считаются вашими (кроме «Вы», «Я» и без ответственного). Это имя ещё подсказывается модели распознавания речи')}</span>
                </div>
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Уведомлять, когда запись готова')}</label>
                    <span className="hint">{tr('Системное уведомление macOS: «Запись готова, проверьте 3 задачи»')}</span>
                  </div>
                  <button className={'switch' + (form.notify ? ' on' : '')} role="switch" aria-checked={form.notify}
                    onClick={() => set('notify', !form.notify)}><i /></button>
                </div>
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Утренняя сводка')}</label>
                    <span className="hint">{tr('Одно уведомление в день: просроченное, встречи на сегодня, задачи на неделю. Требует включённых уведомлений')}</span>
                  </div>
                  <button className={'switch' + (form.morning_brief ? ' on' : '')} role="switch" aria-checked={form.morning_brief}
                    onClick={() => set('morning_brief', !form.morning_brief)}><i /></button>
                </div>
                {form.morning_brief && (
                  <div className="backup-plan">
                    <label htmlFor="morning-hour">{tr('Не раньше')}</label>
                    <select id="morning-hour" value={form.morning_hour} onChange={e => set('morning_hour', Number(e.target.value))}>
                      {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                    </select>
                  </div>
                )}
              </div>
            )}
            {tab === 'records' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Откуда берутся записи, куда кладутся заметки и что делать после обработки')}</div>
                {dirField('recordings_dir', tr('Папка записей'), tr('Сюда PLAUD или вы сами кладёте аудиофайлы'))}
                {dirField('obsidian_dir', tr('Папка Obsidian'), tr('Готовые заметки и стенограммы'))}
                {dirField('archive_dir', tr('Архив обработанных записей'), tr('Куда переносится аудио после обработки'))}
                <div className="field">
                  <label htmlFor="retention">{tr('Хранить аудио, дней')}</label>
                  <input id="retention" type="number" min={1} max={3650} value={form.retention_days}
                    onChange={e => set('retention_days', Number(e.target.value))} />
                </div>
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Обрабатывать новые записи автоматически')}</label>
                    <span className="hint">{tr('Файл берётся в работу через 10 секунд после появления в папке')}</span>
                  </div>
                  <button className={'switch' + (form.auto_process ? ' on' : '')} role="switch" aria-checked={form.auto_process}
                    onClick={() => set('auto_process', !form.auto_process)}><i /></button>
                </div>
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Предложения после встречи')}</label>
                    <span className="hint">{tr('После обработки записи искать договорённости: созвон, письмо, напоминание. Показываются карточками, ничего не создаётся без вашего нажатия')}</span>
                  </div>
                  <button className={'switch' + (form.suggestions_auto !== false ? ' on' : '')} role="switch" aria-checked={form.suggestions_auto !== false}
                    onClick={() => set('suggestions_auto', form.suggestions_auto === false)}><i /></button>
                </div>
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('«О чём разговор» в досье')}</label>
                    <span className="hint">{tr('После обработки записи локальная модель коротко пересказывает последние встречи человека и проекта. Выключено — остаётся пересказ из кратких содержаний без модели')}</span>
                  </div>
                  <button className={'switch' + (form.dossier_model !== false ? ' on' : '')} role="switch" aria-checked={form.dossier_model !== false}
                    onClick={() => set('dossier_model', form.dossier_model === false)}><i /></button>
                </div>
              </div>
            )}
            {tab === 'templates' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Какой шаблон заметки используется по умолчанию, и свои шаблоны')}</div>
                <TemplateSettings value={form.default_template} onChange={id => set('default_template', id)} />
              </div>
            )}
            {tab === 'speech' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Расшифровка речи, словарь терминов и определение говорящих')}</div>
                <AsrSettings model={form.asr_model ?? 'turbo'} hint={form.asr_hint ?? true} prep={form.asr_prep ?? 'off'}
                  onChange={(key, value) => set(key, value as never)} />
                <DictionarySettings />
                <div className="switch-row">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Определять говорящих при обработке')}</label>
                    <span className="hint">{tr('Локальная модель оценивает по тексту, кто говорит. Добавляет около минуты на 25 минут записи; имена назначаются вручную')}</span>
                  </div>
                  <button className={'switch' + (form.diarize ? ' on' : '')} role="switch" aria-checked={form.diarize}
                    onClick={() => set('diarize', !form.diarize)}><i /></button>
                </div>
                <VoiceSettings enabled={form.voice_enabled} onToggle={v => set('voice_enabled', v)} />
              </div>
            )}
            {tab === 'calendar' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Откуда брать встречи и куда отправлять задачи')}</div>
                <div className="field">
                  <label htmlFor="calendar">{tr('Календарь (ICS-файл или ссылка)')}</label>
                  <input id="calendar" type="text" value={form.calendar_source} placeholder={tr('Необязательно')}
                    onChange={e => set('calendar_source', e.target.value)} spellCheck={false} />
                  <span className="hint">{tr('Нужен для карточки «Следующая встреча». Без календаря показывается последняя запись.')}</span>
                </div>
                <div className="apple-box">
                  <div className="field" style={{ gap: 2 }}>
                    <label>{tr('Календарь и Напоминания (macOS)')}</label>
                    <span className="hint">{tr('Доступ выдаёт сама система, данные остаются на этом компьютере')}</span>
                  </div>
                  {apple && !apple.available && <span className="hint bad">{apple.reason}</span>}
                  {apple?.available && (
                    <div className="apple-state">
                      <span><i style={{ background: dot(apple.events) }} />{tr('Календарь: ')}{' '}{accessLabel[apple.events] ?? apple.events}</span>
                      <span><i style={{ background: dot(apple.reminders) }} />{tr('Напоминания: ')}{' '}{accessLabel[apple.reminders] ?? apple.reminders}</span>
                    </div>
                  )}
                  {needsAccess && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <button className="btn s30" disabled={appleBusy} onClick={() => void appleCall('/api/integrations/apple/access')}>{tr('Разрешить доступ')}</button>
                      {(apple?.events === 'denied' || apple?.reminders === 'denied') &&
                        <span className="hint">{tr('Если окно не появилось: Системные настройки → Конфиденциальность и безопасность → Календари / Напоминания → включите для Терминала')}</span>}
                    </div>
                  )}
                  <div className="switch-row">
                    <div className="field" style={{ gap: 2 }}>
                      <label>{tr('Брать встречи из Календаря')}</label>
                      <span className="hint">{tr('«Ближайшая встреча», подсказка события для записи, кнопка «В Календарь»')}</span>
                    </div>
                    <button className={'switch' + (form.apple_calendar ? ' on' : '')} role="switch" aria-checked={form.apple_calendar}
                      onClick={() => set('apple_calendar', !form.apple_calendar)}><i /></button>
                  </div>
                  <div className="switch-row">
                    <div className="field" style={{ gap: 2 }}>
                      <label>{tr('Отправлять подтверждённые задачи в Напоминания')}</label>
                      <span className="hint">{tr('Отметка «выполнено» синхронизируется в обе стороны')}</span>
                    </div>
                    <button className={'switch' + (form.reminders_enabled ? ' on' : '')} role="switch" aria-checked={form.reminders_enabled}
                      onClick={() => set('reminders_enabled', !form.reminders_enabled)}><i /></button>
                  </div>
                  {form.reminders_enabled && (
                    <>
                      <div className="field">
                        <label htmlFor="rem-list">{tr('Список в Напоминаниях')}</label>
                        <input id="rem-list" type="text" value={form.reminders_list} onChange={e => set('reminders_list', e.target.value)} />
                      </div>
                      <div className="field">
                        <label>{tr('Какие задачи отправлять')}</label>
                        <div className="seg small" role="tablist" style={{ alignSelf: 'flex-start' }}>
                          <button role="tab" aria-selected={form.reminders_scope === 'mine'} className={form.reminders_scope === 'mine' ? 'on' : ''}
                            onClick={() => set('reminders_scope', 'mine')}>{tr('Мои и без ответственного')}</button>
                          <button role="tab" aria-selected={form.reminders_scope === 'all'} className={form.reminders_scope === 'all' ? 'on' : ''}
                            onClick={() => set('reminders_scope', 'all')}>{tr('Все')}</button>
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <button className="btn s30" disabled={appleBusy} onClick={() => void appleCall('/api/integrations/reminders/sync', tr('Синхронизировано'))}>{tr('Синхронизировать сейчас')}</button>
                        {apple?.lastSync?.error && <span className="hint bad">{apple.lastSync.error}</span>}
                      </div>
                      <span className="hint">{tr('Сначала сохраните настройки, затем запускайте синхронизацию.')}</span>
                    </>
                  )}
                </div>
              </div>
            )}
            {tab === 'data' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('Сводки в Obsidian и резервные копии базы')}</div>
                <div className="field">
                  <label>{tr('Задачи и решения в Obsidian')}</label>
                  <div className="switch-row">
                    <span className="hint">{tr('Сводки «Задачи» и «Решения» по проектам в папке «Сводки» (обновляются сами, править их не нужно)')}</span>
                    <button className={'switch' + (form.mirror_enabled ? ' on' : '')} role="switch" aria-checked={form.mirror_enabled}
                      onClick={() => set('mirror_enabled', !form.mirror_enabled)}><i /></button>
                  </div>
                  <div className="switch-row">
                    <span className="hint">{tr('Дополнительно менять строки в самих заметках записей (статус, срок, ответственный). По умолчанию выключено: рабочее состояние хранится в базе, заметки остаются как извлечено')}</span>
                    <button className={'switch' + (form.note_writeback ? ' on' : '')} role="switch" aria-checked={form.note_writeback}
                      onClick={() => set('note_writeback', !form.note_writeback)}><i /></button>
                  </div>
                </div>
                <div className="field">
                  <label>{tr('Резервные копии базы')}</label>
                  <div className="switch-row">
                    <span className="hint">{tr('Делать копии автоматически')}</span>
                    <button className={'switch' + (form.backup_auto ? ' on' : '')} role="switch" aria-checked={form.backup_auto}
                      onClick={() => set('backup_auto', !form.backup_auto)}><i /></button>
                  </div>
                  {form.backup_auto && (
                    <div className="switch-row">
                      <span className="hint">{tr('Ещё и после изменений: не чаще раза в 15 минут')}</span>
                      <button className={'switch' + (form.backup_on_change ? ' on' : '')} role="switch" aria-checked={form.backup_on_change}
                        onClick={() => set('backup_on_change', !form.backup_on_change)}><i /></button>
                    </div>
                  )}
                  {form.backup_auto && (
                    <div className="backup-plan">
                      <label htmlFor="backup-interval">{tr('Раз в')}</label>
                      <select id="backup-interval" value={form.backup_interval_hours} onChange={e => set('backup_interval_hours', Number(e.target.value))}>
                        {[[6, tr('6 часов')], [12, tr('12 часов')], [24, tr('сутки')], [72, tr('3 дня')], [168, tr('неделю')]].map(([v, label]) =>
                          <option key={v} value={v}>{label}</option>)}
                        {![6, 12, 24, 72, 168].includes(form.backup_interval_hours) && <option value={form.backup_interval_hours}>{tr('{0} ч', [form.backup_interval_hours])}</option>}
                      </select>
                      <label htmlFor="backup-keep">{tr('хранить')}</label>
                      <input id="backup-keep" type="number" min={1} max={60} value={form.backup_keep}
                        onChange={e => set('backup_keep', Number(e.target.value))} />
                      <span className="hint">{tr('копий')}</span>
                    </div>
                  )}
                  {backups.length === 0 && <span className="hint">{tr('Копий пока нет. Первая появится автоматически или создайте её в окне «Состояние системы».')}</span>}
                  {backups.slice(0, 5).map(b => (
                    <div className="backup-row" key={b.name}>
                      <span className="backup-name">
                        {new Date(b.createdAt).toLocaleString(localeTag(), { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                        <span className="hint"> · {fmtBytes(b.size)}</span>
                      </span>
                      {pending === b.name
                        ? (
                          <span className="backup-confirm">
                            <button className="btn s28 muted" onClick={() => setPending('')}>{tr('Нет')}</button>
                            <button className="btn s28 accent" disabled={restoring} onClick={() => void restore(b.name)}>{tr('Да, восстановить')}</button>
                          </span>
                        )
                        : <button className="btn s28" onClick={() => setPending(b.name)}>{tr('Восстановить')}</button>}
                    </div>
                  ))}
                  {pending && <span className="hint">{tr('Текущее состояние базы будет сохранено в новую копию до восстановления. Заметки Obsidian не меняются.')}</span>}
                </div>
              </div>
            )}
            {tab === 'security' && (
              <div className="set-pane-in">
                <div className="set-lead">{tr('PIN-блокировка, журнал действий и доступ с телефона')}</div>
                <SecurityBlock />
                <RemoteBlock />
              </div>
            )}
            </div>
          </div>
        )}
        {error && <div className="error-box set-error">{error}</div>}
        <div className="modal-foot set-foot">
          <button className="btn s34" onClick={() => setSettingsOpen(false)}>{tr('Отмена')}</button>
          <button className="btn accent s34" onClick={save} disabled={!form || saving}>{tr('Сохранить')}</button>
        </div>
      </div>
    </div>
  )
}

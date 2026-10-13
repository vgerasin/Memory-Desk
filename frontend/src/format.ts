import { formatDay, lc, localeTag, plural, tr } from './i18n'

export { plural }

export function parseDate(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || '')
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}

/** Целых дней от даты до сегодня: 0 — сегодня, 1 — вчера, −1 — завтра. */
export function daysAgo(value: string): number {
  const d = parseDate(value)
  if (!d) return NaN
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((today.getTime() - d.getTime()) / 864e5)
}

export function dayLabel(value: string): string {
  const n = daysAgo(value)
  if (n === 0) return tr('Сегодня')
  if (n === 1) return tr('Вчера')
  if (n === -1) return tr('Завтра')
  const d = parseDate(value)
  if (!d) return value || tr('Без даты')
  return formatDay(d, d.getFullYear() !== new Date().getFullYear())
}

const pad = (v: number) => String(v).padStart(2, '0')

/** 1:02:03 или 21:08 — длительность. */
export function fmt(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return ''
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${m}:${pad(x)}`
}

/** Таймкод 00:30 / 1:05:09 для ссылок на место в записи. */
export function tc(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${pad(m)}:${pad(x)}`
}

export function due(iso: string | null, text: string): { label: string; color: string } {
  if (iso) {
    const n = daysAgo(iso)
    if (n === 0) return { label: tr('сегодня'), color: '#E0352B' }
    if (n > 0) return { label: tr('просрочено'), color: '#E0352B' }
    return { label: tr('до ') + lc(dayLabel(iso)), color: 'var(--text2)' }
  }
  if (text) return { label: text, color: 'var(--text2)' }
  return { label: tr('без срока'), color: 'var(--text3)' }
}

export function ownerLabel(owner: string): string {
  return !owner ? tr('Не назначен') : owner === 'Я' || owner === 'Вы' ? tr('Вы') : owner // i18n-ignore: «Я» и «Вы» — метки владельца в данных, не надписи
}

export function longDate(now = new Date()): string {
  const s = now.toLocaleDateString(localeTag(), { weekday: 'long', day: 'numeric', month: 'long' })
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function clockOf(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function meetingWhen(start: string): string {
  const d = new Date(start)
  if (isNaN(d.getTime())) return ''
  const iso = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  const n = daysAgo(iso)
  const rel = n === 0 ? tr('сегодня') : n === -1 ? tr('завтра') : lc(dayLabel(iso))
  return tr('{0} в {1}:{2}', [rel, pad(d.getHours()), pad(d.getMinutes())])
}

export function etaLabel(seconds: number | null): string {
  if (seconds == null) return '—'
  return tr('≈ {0} мин', [Math.max(1, Math.round(seconds / 60))])
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return tr('{0} Б', [bytes])
  if (bytes < 1024 ** 2) return tr('{0} КБ', [Math.round(bytes / 1024)])
  return tr('{0} МБ', [(bytes / 1024 ** 2).toFixed(1)])
}

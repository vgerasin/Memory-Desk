const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

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
  if (n === 0) return 'Сегодня'
  if (n === 1) return 'Вчера'
  if (n === -1) return 'Завтра'
  const d = parseDate(value)
  if (!d) return value || 'Без даты'
  const label = `${d.getDate()} ${MONTHS[d.getMonth()]}`
  return d.getFullYear() === new Date().getFullYear() ? label : `${label} ${d.getFullYear()}`
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

export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100
  return `${n} ${m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many}`
}

export function due(iso: string | null, text: string): { label: string; color: string } {
  if (iso) {
    const n = daysAgo(iso)
    if (n === 0) return { label: 'сегодня', color: '#E0352B' }
    if (n > 0) return { label: 'просрочено', color: '#E0352B' }
    return { label: 'до ' + dayLabel(iso).toLowerCase(), color: 'var(--text2)' }
  }
  if (text) return { label: text, color: 'var(--text2)' }
  return { label: 'без срока', color: 'var(--text3)' }
}

export function ownerLabel(owner: string): string {
  return !owner || owner === 'Я' ? (owner ? 'Вы' : 'Не назначен') : owner
}

export function longDate(now = new Date()): string {
  const s = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
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
  const day = dayLabel(iso)
  const rel = day === 'Сегодня' ? 'сегодня' : day === 'Завтра' ? 'завтра' : day.toLowerCase()
  return `${rel} в ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function etaLabel(seconds: number | null): string {
  if (seconds == null) return '—'
  return `≈ ${Math.max(1, Math.round(seconds / 60))} мин`
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} КБ`
  return `${(bytes / 1024 ** 2).toFixed(1)} МБ`
}

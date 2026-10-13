/** Языки интерфейса. Ключ перевода — русский исходный текст: русский язык работает без словаря, остальные берут строку из `locales/<язык>.json`.
 *  Язык выбирается до первой отрисовки и при смене перезагружает страницу (так надписи, собранные при загрузке, не расходятся). */
export type Lang = 'ru' | 'en' | 'fr' | 'de' | 'es'

export const LANGS: { id: Lang; name: string; locale: string }[] = [
  { id: 'ru', name: 'Русский', locale: 'ru-RU' },
  { id: 'en', name: 'English', locale: 'en-GB' },
  { id: 'fr', name: 'Français', locale: 'fr-FR' },
  { id: 'de', name: 'Deutsch', locale: 'de-DE' },
  { id: 'es', name: 'Español', locale: 'es-ES' },
]
const STORAGE_KEY = 'md-lang'
const PSEUDO = 'xx'     // для проверки вёрстки: все надписи длиннее на 30 %, включается ?lang=xx

type Dict = Record<string, string | string[]>
let dict: Dict = {}
let lang: Lang = 'ru'
let pseudo = false
let patterns: { re: RegExp; to: string }[] | null = null

function stored(): string {
  try { return localStorage.getItem(STORAGE_KEY) || '' } catch { return '' }
}

export function currentLang(): Lang { return lang }
export function localeTag(): string { return LANGS.find(l => l.id === lang)?.locale ?? 'ru-RU' }

/** Выбирает язык и подгружает словарь; вызывается один раз до загрузки остального кода. */
export async function initLang(): Promise<void> {
  let want = stored()
  try {
    const q = new URLSearchParams(window.location.search).get('lang')
    if (q === PSEUDO) pseudo = true
    else if (q && LANGS.some(l => l.id === q)) want = q
  } catch { /* адресной строки нет */ }
  lang = (LANGS.some(l => l.id === want) ? want : 'ru') as Lang
  document.documentElement.lang = lang
  if (lang === 'ru') return
  try {
    dict = (await import(`./locales/${lang}.json`)).default as Dict
  } catch { lang = 'ru' }
}

/** Сохраняет выбор и перезагружает страницу. */
export function setLang(next: Lang): void {
  try { localStorage.setItem(STORAGE_KEY, next) } catch { /* без хранилища язык не запомнится */ }
  window.location.reload()
}

/** Запоминает язык, выбранный в другом окне или на другом устройстве (из настроек сервера); true — страницу нужно перезагрузить. */
export function adoptLang(next: string): boolean {
  if (!LANGS.some(l => l.id === next) || next === lang || pseudo) return false
  try {
    if (sessionStorage.getItem('md-lang-synced') === next) return false
    sessionStorage.setItem('md-lang-synced', next)
    localStorage.setItem(STORAGE_KEY, next)
  } catch { return false }
  return true
}

function fill(text: string, args?: unknown[]): string {
  return args ? text.replace(/\{(\d+)\}/g, (_m, i) => String(args[Number(i)] ?? '')) : text
}

function stretch(text: string): string {
  const extra = Math.ceil(text.length * 0.3)
  return text + ' ' + 'ъ'.repeat(Math.max(2, extra - 1))
}

/** Перевод строки интерфейса; `{0}`, `{1}` в ключе заменяются значениями из `args`. */
export function tr(key: string, args?: unknown[]): string {
  if (pseudo) return stretch(fill(key, args))
  const hit = dict[key]
  return fill(typeof hit === 'string' ? hit : key, args)
}

// ------------------------------------------------------------------ множественное число

/** `n` + слово в нужной форме. Русские формы (один, несколько, много) — ключ перевода, в других языках две формы: один и остальное. */
export function plural(n: number, one: string, few: string, many: string): string {
  if (lang === 'ru' || pseudo) {
    const m10 = n % 10, m100 = n % 100
    const form = m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many
    return pseudo ? stretch(`${n} ${form}`) : `${n} ${form}`
  }
  const forms = dict[`${one}|${few}|${many}`]
  if (!Array.isArray(forms)) return `${n} ${many}`
  const category = new Intl.PluralRules(localeTag()).select(n)
  return `${n} ${category === 'one' ? forms[0] : forms[1] ?? forms[0]}`
}

// ------------------------------------------------------------------ тексты сервера

function compile(): { re: RegExp; to: string }[] {
  if (patterns) return patterns
  patterns = []
  for (const [key, value] of Object.entries(dict)) {
    if (typeof value !== 'string' || !/\{\d+\}/.test(key)) continue
    const source = key.split(/(\{\d+\})/).map(part => (/^\{\d+\}$/.test(part) ? '(.+?)' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('')
    const order = [...key.matchAll(/\{(\d+)\}/g)].map(m => Number(m[1]))
    // порядок групп в тексте может отличаться от порядка в переводе: переименовываем плейсхолдеры по номеру группы
    const to = value.replace(/\{(\d+)\}/g, (_m, i) => `{${order.indexOf(Number(i))}}`)
    patterns.push({ re: new RegExp('^' + source + '$', 's'), to })
  }
  return patterns
}

/** Перевод текста, который прислал сервер (по-русски): точное совпадение или (если `exact` не задано) шаблон с `{0}`. Чужой текст остаётся как есть. */
export function trServer(text: string, exact = false): string {
  if (lang === 'ru' || pseudo || !text) return pseudo && text ? stretch(text) : text
  const hit = dict[text]
  if (typeof hit === 'string') return hit
  if (exact) return text
  for (const { re, to } of compile()) {
    const m = re.exec(text)
    if (m) return to.replace(/\{(\d+)\}/g, (_x, i) => m[Number(i) + 1] ?? '')
  }
  return text
}

/** Поля ответов сервера с готовыми надписями и сообщениями: переводятся и по шаблонам. */
export const SERVER_TEXT_KEYS = new Set(['detail', 'message', 'label', 'statusLabel', 'kindLabel', 'reasons', 'warnings', 'headline', 'hint', 'note', 'problems',
  'state_text', 'actorLabel', 'object', 'resultLabel', 'empty', 'reason', 'dependenciesReason', 'value', 'warning'])
/** Поля, где бывает и название, написанное человеком (заметки, проекты, люди): переводится только точное совпадение со словарём. */
export const SERVER_NAME_KEYS = new Set(['title', 'name', 'description', 'stage', 'stage_label', 'clean', 'cabinet', 'cli', 'phone', 'runner', 'system', 'ok', 'error', 'denied'])

/** Переводит в ответе сервера только поля, в которых он присылает надписи для интерфейса; содержимое записей не трогает. */
export function localize<T>(value: T): T {
  if (lang === 'ru' && !pseudo) return value
  const walk = (node: unknown, key: string): unknown => {
    if (typeof node === 'string') return SERVER_TEXT_KEYS.has(key) ? trServer(node) : SERVER_NAME_KEYS.has(key) ? trServer(node, true) : node
    if (Array.isArray(node)) return node.map(item => walk(item, key))
    if (node && typeof node === 'object') {
      const out: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(node)) out[k] = walk(v, k)
      return out
    }
    return node
  }
  return walk(value, '') as T
}

/** Нижний регистр для надписей, вставляемых в середину фразы; в немецком существительные и месяцы пишутся с заглавной, поэтому там регистр не меняется. */
export function lc(text: string): string {
  return lang === 'de' ? text : text.toLocaleLowerCase(localeTag())
}

// ------------------------------------------------------------------ даты

export function formatDay(d: Date, withYear: boolean): string {
  return new Intl.DateTimeFormat(localeTag(), { day: 'numeric', month: 'long', ...(withYear ? { year: 'numeric' } : {}) }).format(d)
}

/** «чт, 15 октября» на языке интерфейса; из ISO-даты `YYYY-MM-DD`. */
export function formatWeekdayDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return ''
  return new Intl.DateTimeFormat(localeTag(), { weekday: 'short', day: 'numeric', month: 'long' }).format(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
}

export function monthShort(index: number): string {
  return new Intl.DateTimeFormat(localeTag(), { month: 'short' }).format(new Date(2026, index, 1)).replace('.', '')
}

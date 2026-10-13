import { localize, tr, trServer } from './i18n'
export class ApiError extends Error {
  status: number
  /** разобранное тело ответа с ошибкой (например, `retryAfter` у неверного PIN) */
  data: Record<string, unknown>
  constructor(status: number, message: string, data: Record<string, unknown> = {}) { super(message); this.status = status; this.data = data }
}

/** Сервер ответил 423: приложение заблокировано PIN. Экран блокировки слушает это событие и закрывает содержимое. */
export const LOCKED_EVENT = 'md-locked'

async function fail(response: Response): Promise<never> {
  let detail = response.statusText
  let data: Record<string, unknown> = {}
  try {
    data = await response.json()
    detail = typeof data.detail === 'string' ? trServer(data.detail) : JSON.stringify(data.detail)
  } catch { /* ответ без JSON */ }
  if (response.status === 423) window.dispatchEvent(new Event(LOCKED_EVENT))
  throw new ApiError(response.status, detail, data)
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Memory-Desk': '1' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiError(0, tr('Нет связи с Memory Desk. Проверьте, что приложение запущено.'))
  }
  if (!response.ok) return fail(response)
  return (response.status === 204 ? undefined : localize(await response.json())) as T
}

/** Скачивание файла, который собирает сервер (экспорт документов): имя берётся из заголовка ответа. */
export async function downloadFile(path: string, body: unknown): Promise<string> {
  let response: Response
  try {
    response = await fetch(path, { method: 'POST', headers: { 'X-Memory-Desk': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  } catch {
    throw new ApiError(0, tr('Нет связи с Memory Desk. Проверьте, что приложение запущено.'))
  }
  if (!response.ok) return fail(response)
  const header = response.headers.get('Content-Disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1]
  const name = encoded ? decodeURIComponent(encoded) : (/filename="([^"]+)"/i.exec(header)?.[1] ?? 'document')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url; link.download = name
  document.body.appendChild(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
  return name
}

/** Отправка файла телом запроса (вложение к записи): имя уходит в параметре, файл остаётся на этом компьютере. */
async function upload<T>(path: string, file: Blob, name: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${path}?name=${encodeURIComponent(name)}`, { method: 'POST', headers: { 'X-Memory-Desk': '1', 'Content-Type': 'application/octet-stream' }, body: file })
  } catch {
    throw new ApiError(0, tr('Нет связи с Memory Desk. Проверьте, что приложение запущено.'))
  }
  if (!response.ok) return fail(response)
  return localize(await response.json()) as T
}

export const api = {
  upload,
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : tr('Что-то пошло не так')
}

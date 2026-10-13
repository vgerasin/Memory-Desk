import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { useApi } from '../useApi'
import type { AttachmentDto, MeetingDetail } from '../types'

const ACCEPT = 'image/png,image/jpeg,image/webp,image/heic,image/heif,image/tiff,image/gif,image/bmp,.txt,.md,.markdown,.heic'

function size(bytes: number) {
  return bytes >= 1048576 ? (bytes / 1048576).toFixed(1).replace('.', ',') + ' МБ' : Math.max(1, Math.round(bytes / 1024)) + ' КБ'
}

function Item({ meetingId, item, onChanged }: { meetingId: string; item: AttachmentDto; onChanged: () => void }) {
  const { showToast } = useApp()
  const [text, setText] = useState(item.text)
  const [name, setName] = useState(item.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState(item.warning ?? '')
  const [confirm, setConfirm] = useState<'delete' | 'recognize' | null>(null)
  const [big, setBig] = useState(false)
  useEffect(() => { setText(item.text); setName(item.name) }, [item.text, item.name, item.id])
  const dirty = text !== item.text || name !== item.name
  const base = `/api/meetings/${meetingId}/attachments/${item.id}`

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setError('')
    try { await fn(); onChanged(); if (done) showToast(done) } catch (e) { setError(errorText(e)); setConfirm(null) }
    setBusy(false)
  }
  const save = () => run(() => api.patch(base, { text, name }), 'Текст сохранён')
  const recognize = (force: boolean) => run(async () => {
    const r = await api.post<AttachmentDto>(`${base}/recognize${force ? '?force=true' : ''}`)
    setWarning(r.warning ?? ''); setConfirm(null)
  }, 'Снимок распознан заново')
  const remove = () => run(() => api.del(base), 'Вложение удалено')
  const copy = async () => { try { await navigator.clipboard.writeText(text); showToast('Текст скопирован') } catch { setError('Не удалось скопировать') } }

  return (
    <section className="att" aria-label={`Вложение «${item.name}»`}>
      {item.kind === 'image' && item.url && (
        <button className={'att-thumb' + (big ? ' big' : '')} onClick={() => setBig(!big)} title={big ? 'Уменьшить' : 'Увеличить'} aria-label={big ? 'Уменьшить снимок' : 'Увеличить снимок'}>
          <img src={item.url} alt={item.name} loading="lazy" />
        </button>
      )}
      <div className="att-main">
        <div className="att-head">
          <input className="att-name" value={name} maxLength={80} aria-label="Название вложения" onChange={e => setName(e.target.value)} />
          <span className={'att-badge' + (item.draft ? ' draft' : '')}>
            {item.kind === 'text' ? 'Текст' : item.draft ? 'Черновик распознавания' : 'Проверено'}
          </span>
          <span className="att-meta">{item.kind === 'image' ? (item.engine ? `${item.engine} · ` : '') : ''}{size(item.size)}</span>
        </div>
        {!item.exists && <div className="hint bad">Файл не найден рядом с заметкой: возможно, его удалили или переместили. Текст ниже остался в заметке.</div>}
        {item.kind === 'image' && item.draft && item.text && (
          <div className="hint">Текст распознан автоматически. Рукописное и снимки под углом читаются хуже печатного: сверьте со снимком и поправьте.</div>
        )}
        {warning && <div className="hint bad" role="status">{warning}</div>}
        <textarea className="att-text" value={text} rows={Math.min(14, Math.max(3, text.split('\n').length + 1))} aria-label="Текст вложения"
          placeholder={item.kind === 'image' ? 'Текст на снимке не найден. Впишите его сами, если он нужен для поиска.' : ''}
          onChange={e => setText(e.target.value)} />
        {error && <div className="hint bad" role="alert">{error}</div>}
        <div className="att-actions">
          {confirm === 'delete' ? (
            <>
              <span className="hint">Убрать вложение из заметки и стереть файл?</span>
              <button className="btn s28 accent" disabled={busy} onClick={() => void remove()}>Удалить</button>
              <button className="btn s28" onClick={() => setConfirm(null)}>Не надо</button>
            </>
          ) : confirm === 'recognize' ? (
            <>
              <span className="hint bad">Текст правили вручную; повторное распознавание заменит его.</span>
              <button className="btn s28 accent" disabled={busy} onClick={() => void recognize(true)}>Заменить</button>
              <button className="btn s28" onClick={() => setConfirm(null)}>Не надо</button>
            </>
          ) : (
            <>
              <button className="btn s28 accent" disabled={busy || !dirty} onClick={() => void save()}>Сохранить</button>
              {item.kind === 'image' && item.exists && (
                <button className="btn s28" disabled={busy} onClick={() => (item.draft || !item.text ? void recognize(false) : setConfirm('recognize'))}>Распознать заново</button>
              )}
              {text && <button className="btn s28" onClick={() => void copy()}>Копировать текст</button>}
              {item.url && item.kind === 'image' && <a className="btn s28" style={{ textDecoration: 'none' }} href={item.url} target="_blank" rel="noreferrer">Открыть снимок</a>}
              <button className="btn s28 danger" disabled={busy} onClick={() => setConfirm('delete')}>Удалить</button>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

/** Вкладка «Вложения»: фото доски, слайда, листа и текстовые заметки. Текст с фото распознаётся на этом компьютере. */
export function AttachmentsPanel({ meeting }: { meeting: MeetingDetail }) {
  const { showToast, invalidate } = useApp()
  const { data: meta } = useApi<{ engines: string[] }>(`/api/meetings/${meeting.id}/attachments`)
  const items = meeting.attachments ?? []
  const [busy, setBusy] = useState(0)
  const [error, setError] = useState('')
  const [over, setOver] = useState(false)
  const [writing, setWriting] = useState(false)
  const [noteName, setNoteName] = useState('')
  const [noteText, setNoteText] = useState('')
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { setError(''); setWriting(false) }, [meeting.id])

  const send = async (files: { file: Blob; name: string }[]) => {
    setError('')
    for (const { file, name } of files) {
      setBusy(n => n + 1)
      try {
        const r = await api.upload<AttachmentDto>(`/api/meetings/${meeting.id}/attachments`, file, name)
        invalidate()
        showToast(r.already ? `«${name}» уже добавлен` : r.warning ? `«${name}» добавлен. ${r.warning}` : r.kind === 'image' ? `«${name}» добавлен и распознан` : `«${name}» добавлен`)
      } catch (e) { setError(`${name}: ${errorText(e)}`) }
      setBusy(n => n - 1)
    }
  }
  const pick = (list: FileList | null) => { if (list?.length) void send(Array.from(list).map(f => ({ file: f, name: f.name }))) }
  // вставка снимка из буфера (например, скриншот) прямо на вкладке
  const onPaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files).filter(f => f.type.startsWith('image/'))
    if (!files.length) return
    e.preventDefault()
    void send(files.map(f => ({ file: f, name: f.name && f.name !== 'image.png' ? f.name : `Снимок из буфера ${new Date().toLocaleTimeString('ru-RU').replace(/:/g, '-')}.png` })))
  }
  const addNote = async () => {
    setError(''); setBusy(n => n + 1)
    try {
      await api.post(`/api/meetings/${meeting.id}/attachments/text`, { name: noteName, text: noteText })
      setWriting(false); setNoteName(''); setNoteText(''); invalidate(); showToast('Заметка добавлена')
    } catch (e) { setError(errorText(e)) }
    setBusy(n => n - 1)
  }
  const engines = meta?.engines ?? null

  return (
    <div className="atts" onPaste={onPaste}>
      <div
        className={'att-drop' + (over ? ' over' : '')}
        onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files) }}
      >
        <div>
          <b>Фото доски, слайда, листа или текстовый файл</b>
          <div className="hint">Перетащите файл сюда, выберите его или вставьте снимок из буфера (⌘V). Файлы остаются на этом компьютере, рядом с заметкой, текст с фото распознаётся локально и попадает в поиск, чат и протокол.</div>
        </div>
        <div className="row">
          <button className="btn s28 accent" disabled={busy > 0} onClick={() => input.current?.click()}>Выбрать файл</button>
          <button className="btn s28" disabled={busy > 0} onClick={() => setWriting(w => !w)}>Написать заметку</button>
          <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={e => { pick(e.target.files); e.target.value = '' }} />
        </div>
      </div>
      {engines !== null && engines.length === 0 && (
        <div className="hint bad">Движка распознавания нет: фото сохранятся без текста. На macOS Vision ставится при запуске start.command; запасной вариант — brew install tesseract tesseract-lang.</div>
      )}
      {busy > 0 && <div className="hint" role="status">Добавляю и распознаю… Фото распознаётся от нескольких секунд до минуты.</div>}
      {error && <div className="hint bad" role="alert">{error}</div>}

      {writing && (
        <div className="att-write">
          <input value={noteName} maxLength={80} placeholder="Название (необязательно)" aria-label="Название заметки" onChange={e => setNoteName(e.target.value)} />
          <textarea value={noteText} rows={6} placeholder="Текст заметки" aria-label="Текст заметки" onChange={e => setNoteText(e.target.value)} />
          <div className="row">
            <button className="btn s28 accent" disabled={busy > 0 || !noteText.trim()} onClick={() => void addNote()}>Добавить</button>
            <button className="btn s28" onClick={() => setWriting(false)}>Отмена</button>
          </div>
        </div>
      )}

      {items.length === 0 && !writing && <div className="muted-text">Вложений пока нет.</div>}
      {items.map(it => <Item key={it.id} meetingId={meeting.id} item={it} onChanged={invalidate} />)}
    </div>
  )
}

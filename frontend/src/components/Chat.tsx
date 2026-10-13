import { useEffect, useRef, useState } from 'react'
import { navigate, recordLink } from '../router'
import { useApp } from '../store'
import type { ChatScope } from '../types'
import { dayLabel, tc } from '../format'
import { IconClose, IconPlay, IconSend } from './ui'
import { ExportMenu } from './ExportMenu'
import { lc, tr } from '../i18n'

const SUGGESTIONS = [tr('Что сделано за последнюю неделю?'), tr('Итоги за месяц по каждому проекту'), tr('Какие задачи сейчас открыты?'), tr('О чём были последние записи?')]

/** Мини-разметка ответа: **жирный**, _курсив_, списки «- ». Без HTML, поэтому безопасно. */
function inline(text: string, key: string) {
  return text.split(/(\*\*[^*]+\*\*|_[^_]+_)/g).filter(Boolean).map((part, i) =>
    part.startsWith('**') ? <strong key={key + i}>{part.slice(2, -2)}</strong>
      : part.length > 2 && part.startsWith('_') && part.endsWith('_') ? <em key={key + i}>{part.slice(1, -1)}</em> : part)
}

export function RichText({ text }: { text: string }) {
  const blocks: JSX.Element[] = []
  let list: string[] = []
  const flush = () => {
    if (list.length) { const items = list; blocks.push(<ul key={'u' + blocks.length}>{items.map((l, i) => <li key={i}>{inline(l, 'l' + i)}</li>)}</ul>); list = [] }
  }
  text.split('\n').forEach((line, i) => {
    if (/^\s*[-•]\s+/.test(line)) { list.push(line.replace(/^\s*[-•]\s+/, '')); return }
    flush()
    if (line.trim()) blocks.push(<p key={'p' + i}>{inline(line, 'p' + i)}</p>)
  })
  flush()
  return <>{blocks}</>
}

export function Chat() {
  const { chat, meta, currentRecord, setMaterial } = useApp()
  const [input, setInput] = useState('')
  const scroll = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLInputElement>(null)

  useEffect(() => { if (chat.open) window.setTimeout(() => field.current?.focus(), 80) }, [chat.open])
  useEffect(() => { const el = scroll.current; if (el) el.scrollTop = el.scrollHeight }, [chat.messages])

  if (!chat.open) return null

  const scopes: ChatScope[] = [{ kind: 'all' }]
  if (currentRecord) scopes.push({ kind: 'record', id: currentRecord, label: tr('Текущая запись') })
  for (const p of meta?.projects ?? []) scopes.push({ kind: 'project', id: p.id, title: p.title, label: tr('Проект: ') + p.title })
  for (const p of (meta?.people ?? []).slice(0, 8)) scopes.push({ kind: 'person', id: p.id, label: tr('Человек: ') + p.name.split(' ')[0] })
  scopes.push({ kind: 'days', days: 7, label: tr('7 дней') })
  const labelOf = (s: ChatScope) => ('label' in s ? s.label : tr('Весь архив'))
  const sameScope = (a: ChatScope, b: ChatScope) =>
    a.kind === b.kind && ('id' in a ? 'id' in b && a.id === b.id : true) && ('days' in a ? 'days' in b && a.days === b.days : true)

  const submit = () => { if (input.trim() && !chat.busy) { chat.send(input); setInput('') } }

  return (
    <>
      <div className="scrim" onClick={chat.closeChat} />
      <aside className="chat" role="dialog" aria-label={tr('Спросить архив')}>
        <div className="chat-head">
          <div>
            <span className="chat-title">{tr('Спросить архив')}</span>
            <span className="chat-sub">{tr('Ответы только по вашим записям, с источниками')}</span>
          </div>
          <button className="round-btn" onClick={chat.closeChat} aria-label={tr('Закрыть')}><IconClose /></button>
        </div>
        <div className="scopes">
          {scopes.map(s => (
            <button key={labelOf(s)} className={'scope' + (sameScope(s, chat.scope) ? ' on' : '')} onClick={() => chat.setScope(s)}>
              {labelOf(s)}
            </button>
          ))}
        </div>
        <div className="chat-scroll" ref={scroll}>
          {!chat.messages.length && (
            <div className="suggest">
              <span>{tr('Например')}</span>
              {SUGGESTIONS.map(t => <button key={t} onClick={() => chat.send(t)}>{t}</button>)}
            </div>
          )}
          {chat.messages.map((m, index) => m.role === 'user' ? (
            <div key={m.id} className="bubble-user">{m.text}</div>
          ) : (
            <div key={m.id} className="bot">
              <div className={'bot-text' + (m.pending ? ' pending' : '') + (m.error ? ' error' : '') + (m.digest || m.material ? ' rich' : '')}>
                {m.digest || m.material ? <RichText text={m.text} /> : m.text}
              </div>
              {m.material && m.material.kind === 'deck' && m.material.result.url && (
                <a className="btn accent s34 chat-file" href={m.material.result.url} download>{tr('Скачать презентацию (.pptx)')}</a>
              )}
              {m.material && m.material.kind !== 'deck' && (
                <button className="btn s34 chat-file" onClick={() => setMaterial({ kind: m.material!.kind, title: m.material!.result.title, result: m.material!.result })}>{tr('Открыть окно: править, скачать, в Obsidian')}</button>
              )}
              {!m.pending && !m.error && !m.material && (m.sources?.length ?? 0) > 0 && (
                <div className="save-answer">
                  {m.saved ? (
                    <>
                      <span className="saved-mark">{tr('Сохранено в Obsidian')}</span>
                      <a className="link-btn" href={m.saved.obsidianUrl}>{tr('Открыть заметку')}</a>
                    </>
                  ) : (
                    <button className="btn s28" disabled={m.saving} onClick={() => void chat.saveAnswer(m.id)}>
                      {m.saving ? tr('Сохраняю…') : tr('Сохранить в Obsidian')}
                    </button>
                  )}
                  <ExportMenu endpoint="/api/chat/export" className="btn s28" up
                    body={() => chat.answerPayload(m.id)} />
                  {m.saveError && <span className="save-error" role="alert">{m.saveError}</span>}
                </div>
              )}
              {m.digest && m.sources && m.sources.length > 0 && (
                <div className="digest-note">{tr('Записи за период: {0}', [m.digest.shown < m.digest.records ? tr('последние {0} из {1}', [m.digest.shown, m.digest.records]) : m.digest.records])}</div>
              )}
              {(m.sources ?? []).map((s, i) => (
                <div className="source" key={i}>
                  <div className="source-top">
                    <span className="source-title">{s.title}</span>
                    <span className="source-date">{[s.date ? dayLabel(s.date) : '', s.time].filter(Boolean).join(', ')}</span>
                  </div>
                  <div className="source-text">«{s.excerpt}»</div>
                  {s.meetingId && (
                    <button className="source-open" onClick={() => { chat.closeChat(); navigate(recordLink(s.meetingId, { t: s.tSec })) }}>
                      <IconPlay size={9} />
                      {s.tSec != null && <span className="num">{tc(s.tSec)}</span>}
                      <span>{s.tSec != null ? tr('· открыть') : tr('Открыть запись')}</span>
                    </button>
                  )}
                </div>
              ))}
              {index === chat.messages.length - 1 && !m.pending && !m.error && (m.suggestions?.length ?? 0) > 0 && (
                <div className="followups" aria-label={tr('Что спросить дальше')}>
                  <span>{tr('Что спросить дальше')}</span>
                  {m.suggestions!.map(q => <button key={q} disabled={chat.busy} onClick={() => chat.send(q)}>{q}</button>)}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="chat-foot">
          <div className="chat-input">
            <input ref={field} value={input} onChange={e => setInput(e.target.value)} placeholder={tr('Вопрос · ') + lc(labelOf(chat.scope))}
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }} />
            <button className="send" onClick={submit} disabled={chat.busy} aria-label={tr('Отправить')}><IconSend /></button>
          </div>
          {chat.messages.length > 0 && <button className="chat-clear" onClick={chat.clear}>{tr('Очистить беседу')}</button>}
        </div>
      </aside>
    </>
  )
}

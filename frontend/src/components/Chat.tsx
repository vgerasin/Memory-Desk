import { useEffect, useRef, useState } from 'react'
import { navigate, recordLink } from '../router'
import { useApp } from '../store'
import type { ChatScope } from '../types'
import { dayLabel, tc } from '../format'
import { IconClose, IconPlay, IconSend } from './ui'

const SUGGESTIONS = ['Что сделано за последнюю неделю?', 'Итоги за месяц по каждому проекту', 'Какие задачи сейчас открыты?', 'О чём были последние записи?']

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
  if (currentRecord) scopes.push({ kind: 'record', id: currentRecord, label: 'Текущая запись' })
  for (const p of meta?.projects ?? []) scopes.push({ kind: 'project', id: p.id, title: p.title, label: 'Проект: ' + p.title })
  for (const p of (meta?.people ?? []).slice(0, 8)) scopes.push({ kind: 'person', id: p.id, label: 'Человек: ' + p.name.split(' ')[0] })
  scopes.push({ kind: 'days', days: 7, label: '7 дней' })
  const labelOf = (s: ChatScope) => ('label' in s ? s.label : 'Весь архив')
  const sameScope = (a: ChatScope, b: ChatScope) =>
    a.kind === b.kind && ('id' in a ? 'id' in b && a.id === b.id : true) && ('days' in a ? 'days' in b && a.days === b.days : true)

  const submit = () => { if (input.trim() && !chat.busy) { chat.send(input); setInput('') } }

  return (
    <>
      <div className="scrim" onClick={chat.closeChat} />
      <aside className="chat" role="dialog" aria-label="Спросить архив">
        <div className="chat-head">
          <div>
            <span className="chat-title">Спросить архив</span>
            <span className="chat-sub">Ответы только по вашим записям, с источниками</span>
          </div>
          <button className="round-btn" onClick={chat.closeChat} aria-label="Закрыть"><IconClose /></button>
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
              <span>Например</span>
              {SUGGESTIONS.map(t => <button key={t} onClick={() => chat.send(t)}>{t}</button>)}
            </div>
          )}
          {chat.messages.map(m => m.role === 'user' ? (
            <div key={m.id} className="bubble-user">{m.text}</div>
          ) : (
            <div key={m.id} className="bot">
              <div className={'bot-text' + (m.pending ? ' pending' : '') + (m.error ? ' error' : '') + (m.digest || m.material ? ' rich' : '')}>
                {m.digest || m.material ? <RichText text={m.text} /> : m.text}
              </div>
              {m.material && m.material.kind === 'deck' && m.material.result.url && (
                <a className="btn accent s34 chat-file" href={m.material.result.url} download>Скачать презентацию (.pptx)</a>
              )}
              {m.material && m.material.kind !== 'deck' && (
                <button className="btn s34 chat-file" onClick={() => setMaterial({ kind: m.material!.kind, title: m.material!.result.title, result: m.material!.result })}>
                  Открыть окно: править, скачать, в Obsidian
                </button>
              )}
              {m.digest && m.sources && m.sources.length > 0 && (
                <div className="digest-note">Записи за период: {m.digest.shown < m.digest.records ? `последние ${m.digest.shown} из ${m.digest.records}` : m.digest.records}</div>
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
                      <span>{s.tSec != null ? '· открыть' : 'Открыть запись'}</span>
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="chat-foot">
          <div className="chat-input">
            <input ref={field} value={input} onChange={e => setInput(e.target.value)} placeholder={'Вопрос · ' + labelOf(chat.scope).toLowerCase()}
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }} />
            <button className="send" onClick={submit} disabled={chat.busy} aria-label="Отправить"><IconSend /></button>
          </div>
          {chat.messages.length > 0 && <button className="chat-clear" onClick={chat.clear}>Очистить беседу</button>}
        </div>
      </aside>
    </>
  )
}

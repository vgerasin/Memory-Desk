import { useEffect, useRef, useState } from 'react'
import { due, ownerLabel, tc } from '../format'
import { useActions } from '../actions'
import { useApp } from '../store'
import type { DecisionDto, TaskDto } from '../types'
import { Checkbox, IconEdit } from './ui'

type TaskPatch = { text?: string; owner?: string; deadline?: string }

interface TaskProps {
  task: TaskDto; first: boolean
  /** что показывать в ссылке на источник: таймкод или «Встреча · таймкод» */
  source: 'time' | 'meeting'
  onToggle: () => void; onConfirm: () => void; onReject: () => void; onOpen: () => void
  /** если передан — у строки появляется карандаш; возвращает true, когда правка сохранена */
  onSave?: (patch: TaskPatch) => Promise<boolean>
  /** если передан — рядом с карандашом появляется «В Календарь» (только у задач со сроком) */
  onCalendar?: () => void
}

export function DraftButtons({ onConfirm, onReject }: { onConfirm: () => void; onReject: () => void }) {
  return (
    <div className="actions">
      <button className="btn s28 muted" onClick={onReject}>Отклонить</button>
      <button className="btn s28 accent" onClick={onConfirm}>Подтвердить</button>
    </div>
  )
}

function EditButton({ onClick }: { onClick: () => void }) {
  return <button className="item-edit-btn" onClick={onClick} title="Править" aria-label="Править"><IconEdit size={14} /></button>
}

function TaskEditor({ task, onSave, onCancel }: { task: TaskDto; onSave: (patch: TaskPatch) => Promise<boolean>; onCancel: () => void }) {
  const { meta } = useApp()
  const [text, setText] = useState(task.text)
  const [owner, setOwner] = useState(task.owner)
  const [date, setDate] = useState(task.due ?? '')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const listId = 'owners-' + task.id
  const valid = text.trim().length > 0

  const save = async () => {
    if (!valid || busy) return
    setBusy(true)
    const patch: TaskPatch = { text: text.trim(), owner: owner.trim() }
    if (touched) patch.deadline = date || 'не указан'
    const ok = await onSave(patch)
    setBusy(false)
    if (ok) onCancel()
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLElement).tagName !== 'TEXTAREA') { e.preventDefault(); void save() }
    if (e.key === 'Escape') { e.stopPropagation(); onCancel() }
  }
  return (
    <div className="item-editor" onKeyDown={onKey}>
      <textarea autoFocus value={text} onChange={e => setText(e.target.value)} rows={2} aria-label="Текст задачи" />
      <div className="item-editor-row">
        <label>
          <span>Ответственный</span>
          <input list={listId} value={owner} onChange={e => setOwner(e.target.value)} placeholder="Не назначен" spellCheck={false} />
          <datalist id={listId}>
            {(meta?.people ?? []).map(p => <option key={p.id} value={p.name} />)}
          </datalist>
        </label>
        <label>
          <span>Срок</span>
          <input type="date" value={date} onChange={e => { setDate(e.target.value); setTouched(true) }} />
        </label>
        {(date || (task.dueText && !touched)) && (
          <button className="btn link s28" onClick={() => { setDate(''); setTouched(true) }}>Без срока</button>
        )}
      </div>
      {task.dueText && !touched && <div className="item-editor-hint">Срок из записи: «{task.dueText}» — выберите дату, чтобы заменить</div>}
      <div className="item-editor-foot">
        <button className="btn s28 muted" onClick={onCancel}>Отмена</button>
        <button className="btn s28 accent" onClick={() => void save()} disabled={!valid || busy}>Сохранить</button>
      </div>
    </div>
  )
}


const isoDay = (offset: number) => {
  const d = new Date(); d.setDate(d.getDate() + offset)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Срок задачи: подпись-кнопка; по клику — быстрый выбор даты прямо в строке. */
export function DueButton({ task, className }: { task: TaskDto; className?: string }) {
  const actions = useActions()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  const d = due(task.due, task.dueText)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc, true)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc, true) }
  }, [open])
  const pick = async (deadline: string) => {
    if (busy) return
    setBusy(true)
    const ok = await actions.editTask(task.id, { deadline })
    setBusy(false)
    if (ok) setOpen(false)
  }
  const quick: [string, string][] = [['Сегодня', isoDay(0)], ['Завтра', isoDay(1)], ['Через неделю', isoDay(7)]]
  return (
    <span className="due-wrap" ref={box}>
      <button type="button" className={'due-btn' + (className ? ' ' + className : '')} onClick={e => { e.stopPropagation(); setOpen(o => !o) }}
        style={{ color: task.done ? 'var(--text3)' : d.color }} title="Изменить срок" aria-haspopup="dialog" aria-expanded={open}>
        {d.label}
      </button>
      {open && (
        <div className="due-pop" role="dialog" aria-label="Срок задачи" onClick={e => e.stopPropagation()}>
          <div className="due-quick">
            {quick.map(([label, value]) => (
              <button type="button" key={label} className={'chip' + (task.due === value ? ' on' : '')} disabled={busy} onClick={() => void pick(value)}>{label}</button>
            ))}
          </div>
          <input type="date" aria-label="Дата срока" value={task.due ?? ''} disabled={busy}
            onChange={e => { if (e.target.value) void pick(e.target.value) }} />
          {(task.due || task.dueText) && <button type="button" className="btn link s28" disabled={busy} onClick={() => void pick('')}>Без срока</button>}
          {task.dueText && !task.due && <div className="due-hint">Из записи: «{task.dueText}»</div>}
        </div>
      )}
    </span>
  )
}

export function TaskRow({ task, first, source, onToggle, onConfirm, onReject, onOpen, onSave, onCalendar }: TaskProps) {
  const [editing, setEditing] = useState(false)
  const src = source === 'meeting' ? [task.meetingTitle, tc(task.tSec)].filter(Boolean).join(' · ') : tc(task.tSec)
  if (editing && onSave) {
    return (
      <div className={'item' + (first ? ' first' : '')}>
        <TaskEditor task={task} onSave={onSave} onCancel={() => setEditing(false)} />
      </div>
    )
  }
  return (
    <div className={'item editable' + (first ? ' first' : '')}>
      {!task.draft && <Checkbox on={task.done} onClick={onToggle} label={task.done ? 'Вернуть в работу' : 'Отметить выполненной'} />}
      <div className="body">
        <div className="text-row" style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <div className={'text' + (task.done ? ' done' : '')}>{task.text}</div>
          {task.draft && <span className="draft-badge">Черновик</span>}
        </div>
        <div className="meta">
          <span>{ownerLabel(task.owner)}</span><span>·</span>
          <DueButton task={task} />
          {src && <><span>·</span><span className="src" onClick={onOpen}>{src}</span></>}
        </div>
      </div>
      {onCalendar && <button className="item-edit-btn cal" onClick={onCalendar} title="Добавить срок в Календарь" aria-label="Добавить срок в Календарь">📅</button>}
      {onSave && <EditButton onClick={() => setEditing(true)} />}
      {task.draft && <DraftButtons onConfirm={onConfirm} onReject={onReject} />}
    </div>
  )
}

export function DecisionRow({ decision, first, source, onConfirm, onReject, onOpen, onSave }: {
  decision: DecisionDto; first: boolean; source: string
  onConfirm: () => void; onReject: () => void; onOpen: () => void
  onSave?: (patch: { text: string }) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(decision.text)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!onSave || !text.trim() || busy) return
    setBusy(true)
    const ok = await onSave({ text: text.trim() })
    setBusy(false)
    if (ok) setEditing(false)
  }
  if (editing && onSave) {
    return (
      <div className={'item' + (first ? ' first' : '')}>
        <div className="item-editor" onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void save() }
          if (e.key === 'Escape') { e.stopPropagation(); setEditing(false) }
        }}>
          <textarea autoFocus value={text} onChange={e => setText(e.target.value)} rows={2} aria-label="Текст решения" />
          <div className="item-editor-foot">
            <button className="btn s28 muted" onClick={() => { setText(decision.text); setEditing(false) }}>Отмена</button>
            <button className="btn s28 accent" onClick={() => void save()} disabled={!text.trim() || busy}>Сохранить</button>
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className={'item editable' + (first ? ' first' : '')}>
      <div className="body">
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <div className="text">{decision.text}</div>
          {decision.draft && <span className="draft-badge">Черновик</span>}
        </div>
        <div className="meta">
          <span className="src" onClick={onOpen}>{source}</span>
          {decision.status === 'confirmed' && <span>· подтверждено</span>}
        </div>
      </div>
      {onSave && <EditButton onClick={() => { setText(decision.text); setEditing(true) }} />}
      {decision.draft && <DraftButtons onConfirm={onConfirm} onReject={onReject} />}
    </div>
  )
}

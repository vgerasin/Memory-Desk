import { useEffect, useState } from 'react'
import { navigate, recordLink, useRoute } from '../router'
import { useApi } from '../useApi'
import { useActions } from '../actions'
import { dayLabel, tc } from '../format'
import type { DecisionDto, TaskDto } from '../types'
import { Checkbox, ErrorBox, Loading, Segmented } from '../components/ui'
import { DecisionRow, TaskRow } from '../components/Items'
import { ConflictsPanel } from '../components/ConflictsPanel'
import { BulkBar, BulkDue, BulkOwner, MoreButton, PAGE, useBulk } from '../components/Bulk'

interface Registry<T> { items: T[]; total: number; counts: { tasks: number; decisions: number; stuck?: number } }

export function Tasks() {
  const route = useRoute()
  const kind = route.params.get('kind') === 'decisions' ? 'decisions' : 'tasks'
  const stuckOnly = kind === 'tasks' && route.params.get('stuck') === '1'
  const [limit, setLimit] = useState(PAGE)
  useEffect(() => setLimit(PAGE), [kind, stuckOnly])
  const { data, error } = useApi<Registry<TaskDto & DecisionDto>>(`/api/tasks?kind=${kind}&limit=${limit}${stuckOnly ? '&stuck=1' : ''}`)
  const actions = useActions()
  const bulk = useBulk()
  useEffect(() => bulk.stop(), [kind, stuckOnly]) // eslint-disable-line react-hooks/exhaustive-deps
  const counts = data?.counts
  const items = data?.items ?? []
  const base = `/api/${kind}/bulk`

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <h1 className="h1">Решения и задачи</h1>
          <div className="subtitle">{stuckOnly ? 'Задачи в работе без движения больше двух недель: закройте, перенесите срок или отклоните.' : 'Извлечённое автоматически появляется как черновик и ждёт подтверждения.'}</div>
        </div>
        <div className="head-tools">
          {kind === 'tasks' && (counts?.stuck ?? 0) + (stuckOnly ? 1 : 0) > 0 && (
            <button className={'btn s32' + (stuckOnly ? ' accent' : '')} aria-pressed={stuckOnly}
              onClick={() => navigate(stuckOnly ? '/tasks' : '/tasks?stuck=1', true)}>Зависшие{counts?.stuck != null ? ` · ${counts.stuck}` : ''}</button>
          )}
          <button className={'btn s32' + (bulk.on ? ' accent' : '')} onClick={() => (bulk.on ? bulk.stop() : bulk.setOn(true))}>{bulk.on ? 'Закончить выбор' : 'Выбрать'}</button>
          <Segmented small value={kind} onChange={k => navigate(k === 'tasks' ? '/tasks' : '/tasks?kind=decisions', true)}
            items={[{ key: 'tasks', label: 'Задачи', count: counts?.tasks ?? null }, { key: 'decisions', label: 'Решения', count: counts?.decisions ?? null }]} />
        </div>
      </div>
      {kind === 'decisions' && <ConflictsPanel />}
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && (
        <div className="card tasks-card">
          {items.length === 0 && <div className="muted-text" style={{ padding: '20px 0' }}>{stuckOnly ? 'Зависших задач нет' : kind === 'tasks' ? 'Задач пока нет' : 'Решений пока нет'}</div>}
          {kind === 'tasks'
            ? items.map((k, i) => (
              <div className={'sel-row' + (bulk.on ? ' on' : '')} key={k.id}>
                {bulk.on && <Checkbox on={bulk.ids.has(k.id)} onClick={() => bulk.toggle(k.id)} label="Выбрать задачу" />}
                <TaskRow task={k} first={i === 0} source="meeting"
                  onToggle={() => actions.toggleTask(k)} onConfirm={() => actions.confirmTask(k)} onReject={() => actions.rejectTask(k)}
                  onOpen={() => navigate(recordLink(k.meetingId, { t: k.tSec }))}
                  onSave={patch => actions.editTask(k.id, patch)} />
              </div>))
            : items.map((d, i) => (
              <div className={'sel-row' + (bulk.on ? ' on' : '')} key={d.id}>
                {bulk.on && <Checkbox on={bulk.ids.has(d.id)} onClick={() => bulk.toggle(d.id)} label="Выбрать решение" />}
                <DecisionRow decision={d} first={i === 0}
                  source={[d.meetingTitle, dayLabel(d.meetingDate), tc(d.tSec)].filter(Boolean).join(' · ')}
                  onConfirm={() => actions.confirmDecision(d)} onReject={() => actions.rejectDecision(d)}
                  onOpen={() => navigate(recordLink(d.meetingId, { t: d.tSec }))}
                  onSave={patch => actions.editDecision(d.id, patch)} />
              </div>))}
          <MoreButton shown={items.length} total={data.total} onMore={() => setLimit(l => l + PAGE)} />
        </div>
      )}
      {bulk.on && bulk.ids.size > 0 && (
        <BulkBar count={bulk.ids.size} total={items.length} onAll={() => bulk.setAll(items.map(i => i.id))} onClear={bulk.clear} onStop={bulk.stop}>
          {kind === 'tasks' ? (
            <>
              <button className="btn s28" disabled={bulk.busy} onClick={() => void bulk.run(base, { status: 'open' }, 'Подтверждено')}>Подтвердить</button>
              <button className="btn s28" disabled={bulk.busy} onClick={() => void bulk.run(base, { status: 'done' }, 'Выполнено')}>{stuckOnly ? 'Закрыть' : 'Выполнено'}</button>
              <BulkDue label={stuckOnly ? 'Перенести…' : undefined} disabled={bulk.busy} onPick={iso => void bulk.run(base, { deadline: iso }, iso ? 'Срок назначен' : 'Срок снят')} />
              <BulkOwner disabled={bulk.busy} onPick={owner => void bulk.run(base, { owner }, 'Ответственный назначен')} />
              <button className="btn s28 danger" disabled={bulk.busy} onClick={() => void bulk.run(base, { status: 'rejected' }, 'Отклонено')}>Отклонить</button>
            </>
          ) : (
            <>
              <button className="btn s28" disabled={bulk.busy} onClick={() => void bulk.run(base, { status: 'confirmed' }, 'Подтверждено')}>Подтвердить</button>
              <button className="btn s28 danger" disabled={bulk.busy} onClick={() => void bulk.run(base, { status: 'rejected' }, 'Отклонено')}>Отклонить</button>
            </>
          )}
        </BulkBar>
      )}
    </>
  )
}

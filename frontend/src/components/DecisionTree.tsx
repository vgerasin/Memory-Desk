import { useMemo, useState } from 'react'
import { api } from '../api'
import { useApp } from '../store'
import { useActions } from '../actions'
import { navigate, recordLink } from '../router'
import { ownerLabel, plural } from '../format'
import type { DecisionGroup, DecisionNode, DecisionTreeData } from '../types'
import { shortDate, ChartCard } from './viz'
import { tr } from '../i18n'

function flatten(nodes: DecisionNode[], depth = 0, parent: DecisionNode | null = null): { node: DecisionNode; depth: number; parent: DecisionNode | null }[] {
  return nodes.flatMap(n => [{ node: n, depth, parent }, ...flatten(n.replaces, depth + 1, n)])
}
const descendants = (node: DecisionNode): Set<string> => new Set(flatten(node.replaces).map(x => x.node.id))

function Row({ node, depth, group, onChange }: { node: DecisionNode; depth: number; group: DecisionGroup; onChange: () => void }) {
  const { showToast } = useApp()
  const actions = useActions()
  const [choosing, setChoosing] = useState(false)
  const candidates = useMemo(() => {
    const blocked = descendants(node)
    return flatten(group.current).map(x => x.node).filter(x => x.id !== node.id && !blocked.has(x.id) && x.date >= node.date)
      .sort((a, b) => a.date.localeCompare(b.date))
  }, [group, node])

  const replace = (target: string) => actions.safe(async () => {
    await api.patch(`/api/decisions/${node.id}`, { replaced_by: target })
    showToast(target ? tr('Отмечено: решение заменено') : tr('Отметка «заменено» снята'))
    onChange()
  })

  return (
    <li className={'dtree-row' + (depth ? ' old' : '')} style={{ marginLeft: depth * 22 }}>
      <span className="dtree-date">{shortDate(node.date)}</span>
      <span className="dtree-main">
        <button className="dtree-text" onClick={() => navigate(recordLink(node.meetingId, { t: node.tSec }))} title={node.meetingTitle}>{node.text}</button>
        <span className="dtree-tags">
          {depth > 0 && <span className="draft-badge">{tr('заменено')}</span>}
          {node.draft && <span className="draft-badge">{tr('черновик')}</span>}
          {node.owner && <span className="dtree-owner">{ownerLabel(node.owner)}</span>}
        </span>
      </span>
      <span className="dtree-actions">
        {node.replacedBy
          ? <button className="link-btn" onClick={() => void replace('')}>{tr('Снять «заменено»')}</button>
          : choosing
            ? <select autoFocus aria-label={tr('Чем заменено')} defaultValue="" onBlur={() => setChoosing(false)}
                onChange={e => { setChoosing(false); if (e.target.value) void replace(e.target.value) }}>
                <option value="">{tr('Чем заменено…')}</option>
                {candidates.map(c => <option key={c.id} value={c.id}>{shortDate(c.date)} · {c.text.slice(0, 70)}</option>)}
              </select>
            : candidates.length > 0 && <button className="link-btn" onClick={() => setChoosing(true)}>{tr('Заменено…')}</button>}
      </span>
    </li>
  )
}

/** Дерево решений: корень — действующее решение, ниже то, что оно заменило. Заменять можно только решением из того же проекта. */
export function DecisionTree({ data, onChange }: { data: DecisionTreeData; onChange: () => void }) {
  const [only, setOnly] = useState('')
  const groups = data.projects.filter(g => !only || g.id === only)
  const total = data.projects.reduce((n, g) => n + g.total, 0)
  const rows = groups.flatMap(g => flatten(g.current).map(x => [shortDate(x.node.date), g.title, x.node.text,
    x.node.draft ? tr('черновик') : tr('подтверждено'), x.parent ? tr('заменено → ') + x.parent.text.slice(0, 60) : tr('действует')]))
  return (
    <ChartCard title={tr('Дерево решений')} wide
      sub={tr('{0}; под действующим решением — то, что оно заменило. Если решение пересмотрели, отметьте «Заменено…»', [plural(total, 'решение', 'решения', 'решений')])}
      table={{ head: [tr('Дата'), tr('Проект'), tr('Решение'), tr('Статус'), tr('Состояние')], rows }}>
      {data.projects.length > 1 && (
        <label className="viz-select" style={{ maxWidth: 280, marginBottom: 12 }}><span>{tr('Проект')}</span>
          <select value={only} onChange={e => setOnly(e.target.value)}>
            <option value="">{tr('Все проекты')}</option>
            {data.projects.map(g => <option key={g.id || 'none'} value={g.id}>{g.title}</option>)}
          </select>
        </label>
      )}
      {groups.length === 0 && <div className="muted-text">{tr('За выбранный период решений нет.')}</div>}
      {groups.map(g => (
        <div className="dtree-group" key={g.id || 'none'}>
          <div className="dtree-head">
            <i style={{ background: g.color }} /><b>{g.title}</b>
            <span className="muted-text">{tr('действует {0}{1}', [g.active, g.replaced ? tr(', заменено {0}', [g.replaced]) : ''])}</span>
          </div>
          <ul className="dtree">
            {flatten(g.current).map(x => <Row key={x.node.id} node={x.node} depth={x.depth} group={g} onChange={onChange} />)}
          </ul>
        </div>
      ))}
    </ChartCard>
  )
}

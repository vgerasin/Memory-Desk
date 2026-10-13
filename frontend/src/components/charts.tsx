import { useMemo, useState } from 'react'
import { navigate, recordLink } from '../router'
import { ownerLabel, plural } from '../format'
import type { AnalyticsData } from '../types'
import { addDays, barPath, dayNumber, hoursText, niceMax, shortDate, useTip, useWidth } from './viz'
import { lc, tr } from '../i18n'

type Weeks = AnalyticsData['weeks']

/** Записи по неделям: одна серия, колонки не толще 24 px, подписаны текущая и максимальная недели. */
export function WeeklyChart({ weeks }: { weeks: Weeks }) {
  const tip = useTip()
  const [box, W] = useWidth()
  const H = 220, L = 30, R = 8, T = 18, B = 28
  const { max, step } = niceMax(Math.max(...weeks.map(w => w.meetings), 0))
  const band = (W - L - R) / weeks.length
  const bw = Math.min(24, band * 0.6)
  const y = (v: number) => T + (H - T - B) * (1 - v / max)
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step)
  const peak = weeks.reduce((best, w, i) => (w.meetings > weeks[best].meetings ? i : best), 0)
  return (
    <div className="viz-svg-wrap" ref={box}>
      <svg width={W} height={H} className="viz-svg" role="img" aria-label={tr('Записи по неделям')} onMouseLeave={tip.hide}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? 'v-axis' : 'v-grid'} />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="v-tick">{t}</text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const cx = L + band * i + band / 2
          const h = (H - T - B) * (w.meetings / max)
          const labelled = w.meetings > 0 && (i === weeks.length - 1 || i === peak)
          const text = [plural(w.meetings, 'запись', 'записи', 'записей'), hoursText(w.minutes)]
          return (
            <g key={w.start}>
              {w.meetings > 0 && <path d={barPath(cx - bw / 2, y(w.meetings), bw, h, 'top')} className="v-blue" />}
              {labelled && <text x={cx} y={y(w.meetings) - 6} textAnchor="middle" className="v-value">{w.meetings}</text>}
              {(W > 560 ? i % 2 === 1 : i % 3 === 2) || i === weeks.length - 1 ? <text x={cx} y={H - 8} textAnchor="middle" className="v-tick">{shortDate(w.start)}</text> : null}
              <rect x={L + band * i} y={T} width={band} height={H - T - B} fill="transparent" tabIndex={0}
                aria-label={tr('Неделя с {0}: {1}', [shortDate(w.start), text.join(', ')])}
                onMouseMove={e => tip.at(e, tr('Неделя с {0}', [shortDate(w.start)]), text)}
                onFocus={e => tip.onElement(e.currentTarget, tr('Неделя с {0}', [shortDate(w.start)]), text)} onBlur={tip.hide} />
            </g>
          )
        })}
      </svg>
      {tip.node}
    </div>
  )
}

/** Задачи по ответственным: горизонтальные составные полосы (открыты / просрочены / выполнены), зазор 2 px. */
export function OwnerBars({ rows }: { rows: AnalyticsData['byOwner'] }) {
  const tip = useTip()
  const max = Math.max(...rows.map(r => r.open + r.overdue + r.done), 1)
  const parts = [
    { key: 'open', label: tr('Открыты'), cls: 'v-blue' }, { key: 'overdue', label: tr('Просрочены'), cls: 'v-crit' }, { key: 'done', label: tr('Выполнены'), cls: 'v-mute' },
  ] as const
  if (rows.length === 0) return <div className="muted-text">{tr('Задач пока нет')}</div>
  return (
    <div className="viz-rows" onMouseLeave={tip.hide}>
      {rows.map(r => {
        const total = r.open + r.overdue + r.done
        const shown = parts.filter(p => r[p.key] > 0)
        return (
          <div className="viz-row" key={r.owner}>
            <span className="viz-row-label ellipsis" title={ownerLabel(r.owner)}>{ownerLabel(r.owner)}</span>
            <div className="viz-hbar" style={{ width: `${(total / max) * 100}%` }}>
              {shown.map((p, i) => (
                <i key={p.key} tabIndex={0} className={`${p.cls}${i === shown.length - 1 ? ' last' : ''}`} style={{ flex: r[p.key] }}
                  aria-label={`${ownerLabel(r.owner)}: ${lc(p.label)} — ${r[p.key]}`}
                  onMouseMove={e => tip.at(e, ownerLabel(r.owner), [`${p.label}: ${r[p.key]}`, tr('Всего: {0}', [total])])}
                  onFocus={e => tip.onElement(e.currentTarget, ownerLabel(r.owner), [`${p.label}: ${r[p.key]}`, tr('Всего: {0}', [total])])} onBlur={tip.hide} />
              ))}
            </div>
            <span className="viz-row-value num">{total}</span>
          </div>
        )
      })}
      {tip.node}
    </div>
  )
}

/** Сроки открытых задач: от даты встречи до срока, линия «сегодня». */
export function Gantt({ data }: { data: AnalyticsData }) {
  const tip = useTip()
  const rows = data.gantt
  const range = useMemo(() => {
    const from = rows.reduce((m, g) => (g.start < m ? g.start : m), data.today)
    const to = rows.reduce((m, g) => (g.due > m ? g.due : m), data.today)
    const t0 = dayNumber(from) - 1, t1 = dayNumber(to) + 2
    const span = t1 - t0
    const ticks = Array.from({ length: 5 }, (_, i) => addDays(from, Math.round(((span - 2) * i) / 4) - 1 + 1))
    return { t0, span, ticks }
  }, [rows, data.today])
  if (rows.length === 0) return <div className="muted-text">{tr('Открытых задач со сроком нет')}</div>
  const pct = (iso: string) => ((dayNumber(iso) - range.t0) / range.span) * 100
  const todayPct = pct(data.today)
  return (
    <div className="viz-gantt" onMouseLeave={tip.hide}>
      <div className="viz-gantt-axis">
        <span />
        <div className="viz-gantt-track">
          {range.ticks.map(t => <span key={t} className="v-tick-html" style={{ left: `${pct(t)}%` }}>{shortDate(t)}</span>)}
        </div>
      </div>
      {rows.map(g => {
        const left = pct(g.start), width = Math.max(pct(g.due) - left, 1.2)
        const meta = [ownerLabel(g.owner), g.project].filter(Boolean).join(' · ')
        const lines = [meta, `${shortDate(g.start)} → ${shortDate(g.due)}${g.overdue ? tr(' · просрочено') : ''}`]
        return (
          <button key={g.id} className="viz-gantt-row" onClick={() => navigate(recordLink(g.meetingId, { t: g.tSec }))}
            onMouseMove={e => tip.at(e, g.text, lines)} onFocus={e => tip.onElement(e.currentTarget, g.text, lines)} onBlur={tip.hide}>
            <span className="viz-gantt-label">
              <span className="ellipsis">{g.text}</span>
              <span className="viz-gantt-meta ellipsis">{meta}{g.overdue ? tr(' · просрочено') : ''}</span>
            </span>
            <span className="viz-gantt-track">
              <i className="viz-today" style={{ left: `${todayPct}%` }} />
              <i className={'viz-gantt-bar ' + (g.overdue ? 'v-crit' : 'v-blue')} style={{ left: `${left}%`, width: `${width}%` }} />
            </span>
          </button>
        )
      })}
      <div className="viz-gantt-foot"><span /><span className="viz-gantt-note"><i className="viz-today inline" />{' '}{tr(' сегодня')}{data.ganttTotal > rows.length ? tr(' · показано {0} из {1} по ближайшим срокам', [rows.length, data.ganttTotal]) : ''}</span></div>
      {tip.node}
    </div>
  )
}

/** Решения во времени: строка на проект, точка — решение (размер фиксированный, кольцо цвета поверхности). */
export function DecisionTimeline({ data }: { data: AnalyticsData }) {
  const tip = useTip()
  const items = data.decisions
  const groups = useMemo(() => {
    const map = new Map<string, typeof items>()
    for (const d of items) map.set(d.project || tr('Без проекта'), [...(map.get(d.project || tr('Без проекта')) ?? []), d])
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 6)
  }, [items])
  const color = (name: string) => data.graph.projects.find(p => p.name === name)?.color ?? '#8E8E93'
  if (items.length === 0) return <div className="muted-text">{tr('Решений за выбранный период нет')}</div>
  const dates = items.map(d => dayNumber(d.date))
  const t0 = Math.min(...dates) - 1, t1 = Math.max(...dates) + 1, span = Math.max(t1 - t0, 2)
  const pct = (iso: string) => ((dayNumber(iso) - t0) / span) * 100
  const first = new Date((t0 + 1) * 86400000).toISOString().slice(0, 10), last = new Date((t1 - 1) * 86400000).toISOString().slice(0, 10)
  return (
    <div className="viz-timeline" onMouseLeave={tip.hide}>
      {groups.map(([name, list]) => {
        const seen = new Map<string, number>()
        return (
          <div className="viz-tl-row" key={name}>
            <span className="viz-row-label ellipsis" title={name}>{name}</span>
            <span className="viz-tl-track">
              {list.map(d => {
                const k = seen.get(d.date) ?? 0
                seen.set(d.date, k + 1)
                const lines = [shortDate(d.date) + (d.draft ? tr(' · черновик') : '')]
                return (
                  <button key={d.id} className="viz-dot" style={{ left: `calc(${pct(d.date)}% - ${k * 12}px)`, background: color(name) }}
                    aria-label={d.text} onClick={() => navigate(recordLink(d.meetingId, { t: d.tSec }))}
                    onMouseMove={e => tip.at(e, d.text, lines)} onFocus={e => tip.onElement(e.currentTarget, d.text, lines)} onBlur={tip.hide} />
                )
              })}
            </span>
            <span className="viz-row-value num">{list.length}</span>
          </div>
        )
      })}
      <div className="viz-tl-row axis"><span /><span className="viz-tl-axis"><span>{shortDate(first)}</span><span>{shortDate(last)}</span></span><span /></div>
      {tip.node}
    </div>
  )
}

/** Связи людей и проектов: толщина линии — число общих встреч; наведение подсвечивает связи узла. */
export function LinkGraph({ graph }: { graph: AnalyticsData['graph'] }) {
  const tip = useTip()
  const [hot, setHot] = useState<{ kind: 'p' | 'j'; name: string } | null>(null)
  const { people, projects, links } = graph
  if (links.length === 0) return <div className="muted-text">{tr('Связей пока нет: добавьте участников и проекты к записям')}</div>
  const [box, W] = useWidth()
  const rowH = 30, top = 14
  const rows = Math.max(people.length, projects.length)
  const H = top * 2 + rows * rowH
  const side = Math.min(200, Math.max(110, W * 0.26))
  const x1 = side, x2 = W - side
  const yp = (i: number, n: number) => top + ((rows - n) * rowH) / 2 + i * rowH + rowH / 2
  const maxC = Math.max(...links.map(l => l.meetings), 1)
  const active = (l: typeof links[number]) => !hot || (hot.kind === 'p' ? hot.name === l.person : hot.name === l.project)
  return (
    <div className="viz-svg-wrap" ref={box}>
      <svg width={W} height={H} className="viz-svg" role="img" aria-label={tr('Связи людей и проектов')} onMouseLeave={() => { setHot(null); tip.hide() }}>
        {links.map(l => {
          const i = people.findIndex(p => p.name === l.person), j = projects.findIndex(p => p.name === l.project)
          const ya = yp(i, people.length), yb = yp(j, projects.length), mid = (x1 + x2) / 2
          const on = active(l)
          return (
            <path key={l.person + l.project} d={`M${x1},${ya}C${mid},${ya} ${mid},${yb} ${x2},${yb}`} fill="none"
              className={on && hot ? 'v-link hot' : on ? 'v-link' : 'v-link dim'} strokeWidth={1.5 + 4.5 * (l.meetings / maxC)} strokeLinecap="round"
              onMouseMove={e => tip.at(e, `${l.person} · ${l.project}`, [plural(l.meetings, 'общая встреча', 'общие встречи', 'общих встреч')])} />
          )
        })}
        {people.map((p, i) => (
          <g key={p.name} tabIndex={0} onMouseEnter={() => setHot({ kind: 'p', name: p.name })} onFocus={() => setHot({ kind: 'p', name: p.name })} onBlur={() => setHot(null)}>
            <text x={x1 - 18} y={yp(i, people.length) + 4} textAnchor="end" className="v-label">{p.name}</text>
            <circle cx={x1 - 6} cy={yp(i, people.length)} r={5} fill={p.color} className="v-ring" />
            <rect x={0} y={yp(i, people.length) - rowH / 2} width={x1} height={rowH} fill="transparent" />
          </g>
        ))}
        {projects.map((p, j) => (
          <g key={p.name} tabIndex={0} onMouseEnter={() => setHot({ kind: 'j', name: p.name })} onFocus={() => setHot({ kind: 'j', name: p.name })} onBlur={() => setHot(null)}>
            <circle cx={x2 + 6} cy={yp(j, projects.length)} r={5} fill={p.color} className="v-ring" />
            <text x={x2 + 18} y={yp(j, projects.length) + 4} className="v-label">{p.name}</text>
            <rect x={x2} y={yp(j, projects.length) - rowH / 2} width={W - x2} height={rowH} fill="transparent" />
          </g>
        ))}
      </svg>
      {tip.node}
    </div>
  )
}


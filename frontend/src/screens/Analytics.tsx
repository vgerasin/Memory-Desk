import { useEffect, useMemo, useState } from 'react'
import { api, errorText } from '../api'
import { useApi } from '../useApi'
import { navigate, recordLink } from '../router'
import { dayLabel, ownerLabel, plural } from '../format'
import type { AnalyticsData, DecisionTreeData, MeetingDetail, MeetingsResponse } from '../types'
import { ErrorBox, Loading, Segmented } from '../components/ui'
import { DecisionTimeline, Gantt, LinkGraph, OwnerBars, WeeklyChart } from '../components/charts'
import { ChartCard, hoursText, shortDate } from '../components/viz'
import { DecisionTree } from '../components/DecisionTree'
import { tr } from '../i18n'

type Period = '30' | '90' | '365' | '0'

function Tile({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: 'bad' }) {
  return (
    <div className="card viz viz-tile">
      <span className="viz-tile-label">{label}</span>
      <span className={'viz-tile-value' + (tone === 'bad' && Number(value) > 0 ? ' bad' : '')}>{value}</span>
      {sub && <span className="viz-tile-sub">{sub}</span>}
    </div>
  )
}

const words = (text: string) => new Set(text.toLowerCase().match(/[а-яёa-z]{5,}/g) ?? [])

/** Две записи рядом: что общего и что различается. Данные берутся из карточек записей. */
function Compare() {
  const { data: list } = useApi<MeetingsResponse>('/api/meetings')
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [pair, setPair] = useState<[MeetingDetail, MeetingDetail] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!a || !b || a === b) { setPair(null); return }
    let alive = true
    setError('')
    Promise.all([api.get<MeetingDetail>(`/api/meetings/${a}`), api.get<MeetingDetail>(`/api/meetings/${b}`)])
      .then(r => { if (alive) setPair(r) }).catch(e => { if (alive) setError(errorText(e)) })
    return () => { alive = false }
  }, [a, b])

  const rows = useMemo(() => {
    if (!pair) return []
    const [x, y] = pair
    const names = (m: MeetingDetail) => m.people.map(p => p.name)
    const common = names(x).filter(n => names(y).includes(n))
    const shared = [...words([...x.topics, ...x.summary].join(' '))].filter(w => words([...y.topics, ...y.summary].join(' ')).has(w)).slice(0, 8)
    const open = (m: MeetingDetail) => m.tasks.filter(t => !t.done).length
    return [
      [tr('Дата'), `${dayLabel(x.date)}${x.time ? ', ' + x.time : ''}`, `${dayLabel(y.date)}${y.time ? ', ' + y.time : ''}`],
      [tr('Длительность'), x.duration || '—', y.duration || '—'],
      [tr('Проект'), x.project?.title ?? tr('Без проекта'), y.project?.title ?? tr('Без проекта')],
      [tr('Участники'), names(x).join(', ') || '—', names(y).join(', ') || '—'],
      [tr('Решения'), x.decisions.length, y.decisions.length],
      [tr('Задачи (открытые)'), `${x.tasks.length} (${open(x)})`, `${y.tasks.length} (${open(y)})`],
      [tr('Открытые вопросы'), x.questions.length, y.questions.length],
      [tr('Риски'), x.risks.length, y.risks.length],
      [tr('Общие участники'), common.join(', ') || tr('нет'), ''],
      [tr('Общие слова в темах'), shared.join(', ') || tr('нет'), ''],
    ] as (string | number)[][]
  }, [pair])

  const options = list?.items ?? []
  const select = (value: string, set: (v: string) => void, label: string) => (
    <label className="viz-select"><span>{label}</span>
      <select value={value} onChange={e => set(e.target.value)}>
        <option value="">{tr('Выберите запись')}</option>
        {options.map(m => <option key={m.id} value={m.id}>{dayLabel(m.date)} · {m.title}</option>)}
      </select>
    </label>
  )
  return (
    <ChartCard title={tr('Сравнение записей')} sub={tr('Две записи рядом: участники, решения, задачи и общие темы')} wide>
      <div className="viz-compare-pick">{select(a, setA, tr('Запись A'))}{select(b, setB, tr('Запись B'))}</div>
      {error && <ErrorBox text={error} />}
      {!pair && !error && <div className="muted-text">{a && a === b ? tr('Выберите две разные записи') : tr('Выберите две записи, чтобы сравнить их')}</div>}
      {pair && (
        <div className="viz-table-wrap">
          <table className="viz-table compare">
            <thead><tr><th />
              {pair.map(m => <th key={m.id}><button className="link-btn" onClick={() => navigate(recordLink(m.id))}>{m.title}</button></th>)}</tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={String(r[0])}>
                  <th>{r[0]}</th>
                  {r[2] === '' ? <td colSpan={2}>{r[1]}</td> : <><td>{r[1]}</td><td>{r[2]}</td></>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ChartCard>
  )
}

export function Analytics() {
  const [period, setPeriod] = useState<Period>('90')
  const { data, error } = useApi<AnalyticsData>(`/api/analytics?days=${period}`)
  const tree = useApi<DecisionTreeData>(`/api/decisions/tree?days=${period}`)
  return (
    <>
      <div className="page-head">
        <h1 className="h1">{tr('Аналитика')}</h1>
        <Segmented small value={period} onChange={setPeriod}
          items={[{ key: '30', label: tr('30 дней') }, { key: '90', label: tr('90 дней') }, { key: '365', label: tr('Год') }, { key: '0', label: tr('Всё время') }]} />
      </div>
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && (
        <div className="viz-grid">
          <div className="viz-tiles">
            <Tile label={tr('Записей')} value={data.totals.records} sub={tr('{0} звука', [hoursText(data.totals.minutes)])} />
            <Tile label={tr('Открытых задач')} value={data.totals.openTasks} sub={tr('выполнено {0}', [data.totals.doneTasks])} />
            <Tile label={tr('Просрочено')} value={data.totals.overdue} tone="bad" sub={data.totals.overdue ? tr('срок уже прошёл') : tr('нет просроченных')} />
            <Tile label={tr('Решений')} value={data.totals.decisions} sub={tr('подтверждённых и черновиков')} />
          </div>
          <ChartCard title={tr('Записи по неделям')} sub={tr('Последние 12 недель, не зависит от периода')}
            table={{ head: [tr('Неделя с'), tr('Записей'), tr('Минут')], rows: data.weeks.map(w => [shortDate(w.start), w.meetings, w.minutes]) }} wide>
            <WeeklyChart weeks={data.weeks} />
          </ChartCard>
          <ChartCard title={tr('Задачи по ответственным')} sub={tr('Кто сколько ведёт')}
            legend={[{ label: tr('Открыты'), color: 'var(--v-blue)' }, { label: tr('Просрочены'), color: 'var(--v-crit)' }, { label: tr('Выполнены'), color: 'var(--v-mute)' }]}
            table={{ head: [tr('Ответственный'), tr('Открыты'), tr('Просрочены'), tr('Выполнены')], rows: data.byOwner.map(r => [ownerLabel(r.owner), r.open, r.overdue, r.done]) }}>
            <OwnerBars rows={data.byOwner} />
          </ChartCard>
          <ChartCard title={tr('Решения во времени')} sub={plural(data.decisions.length, 'решение', 'решения', 'решений') + tr(' за период; точка открывает запись')}
            table={{ head: [tr('Дата'), tr('Проект'), tr('Решение')], rows: data.decisions.map(d => [shortDate(d.date), d.project || tr('Без проекта'), d.text]) }}>
            <DecisionTimeline data={data} />
          </ChartCard>
          <ChartCard title={tr('Сроки открытых задач')} sub={tr('От даты встречи до срока; строка открывает запись')} wide
            legend={[{ label: tr('В срок'), color: 'var(--v-blue)' }, { label: tr('Просрочена'), color: 'var(--v-crit)' }]}
            table={{ head: [tr('Задача'), tr('Ответственный'), tr('С'), tr('Срок'), tr('Статус')], rows: data.gantt.map(g => [g.text, ownerLabel(g.owner), shortDate(g.start), shortDate(g.due), g.overdue ? tr('просрочена') : tr('в срок')]) }}>
            <Gantt data={data} />
          </ChartCard>
          <ChartCard title={tr('Связи людей и проектов')} sub={tr('Толщина линии — число общих встреч; наведите на имя')} wide
            table={{ head: [tr('Человек'), tr('Проект'), tr('Встреч')], rows: data.graph.links.map(l => [l.person, l.project, l.meetings]) }}>
            <LinkGraph graph={data.graph} />
          </ChartCard>
          {tree.data && <DecisionTree data={tree.data} onChange={tree.reload} />}
          <Compare />
        </div>
      )}
    </>
  )
}

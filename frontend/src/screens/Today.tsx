import { navigate, recordLink } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { useActions } from '../actions'
import { dayLabel, fmt, longDate, meetingWhen, ownerLabel } from '../format'
import type { Hero, TaskDto, TodayPayload } from '../types'

interface HeroItem { key: string; text: string; meta: string; go: () => void; task?: TaskDto }
import { AvatarStack, ErrorBox, Loading } from '../components/ui'
import { DueButton, TaskRow } from '../components/Items'
import { MorningCard } from '../components/MorningCard'

const stageLine = (label: string, index: number) => `${label} · этап ${index} из 12`

function HeroCard({ hero }: { hero: Hero }) {
  const { chat, setPrep } = useApp()
  const isNext = hero.kind === 'next_meeting'
  const kicker = isNext
    ? `${hero.ongoing ? 'Идёт сейчас' : 'Следующая встреча'} · ${meetingWhen(hero.start || '')}`
    : `Последняя запись · ${dayLabel(hero.date || '').toLowerCase()}${hero.time ? ', ' + hero.time : ''}`
  const people = hero.people as { name: string; initials: string; color: string }[]
  const ask = () => {
    if (hero.project?.id) {
      chat.openChat({ kind: 'project', id: hero.project.id, title: hero.project.title, label: 'Проект: ' + hero.project.title },
        `Что решили и какие задачи открыты по проекту «${hero.project.title}»?`)
    } else if (hero.meetingId) {
      chat.openChat({ kind: 'record', id: hero.meetingId, label: 'Текущая запись' }, 'Кратко: что обсуждали, что решили и какие задачи появились?')
    } else {
      chat.openChat({ kind: 'all' }, 'Что решили за последнюю неделю и какие задачи открыты?')
    }
  }
  const cols = hero.columns
  const columns: { title: string; empty: string; items: HeroItem[] }[] = [
    { title: 'Что решили', empty: 'Решений пока нет', items: cols.decisions.map(d => ({
      key: d.id, text: d.text, meta: dayLabel(d.meetingDate) + (d.draft ? ' · черновик' : ''), go: () => navigate(recordLink(d.meetingId, { t: d.tSec })) })) },
    { title: 'Открытые задачи', empty: 'Открытых задач нет', items: cols.tasks.map(k => ({
      key: k.id, text: k.text, meta: ownerLabel(k.owner) + ' · ', task: k, go: () => navigate(recordLink(k.meetingId, { t: k.tSec })) })) },
    { title: 'Открытые вопросы', empty: 'Открытых вопросов нет', items: cols.questions.map((q, i) => ({
      key: q.meetingId + i, text: q.text, meta: q.meetingTitle, go: () => navigate(recordLink(q.meetingId, { view: 'questions' })) })) },
  ]
  return (
    <section className="card hero">
      <div className="hero-top">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="hero-kicker">{kicker}</div>
          <div className="hero-title">{hero.title}</div>
          {people.length > 0 && (
            <div className="hero-people">
              <AvatarStack people={people} size={24} />
              <span className="names">{people.map(p => p.name).join(', ')}</span>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn accent s36" style={{ fontWeight: 600 }} onClick={() => (hero.project?.id ? setPrep({ projectId: hero.project.id, title: hero.project.title }) : hero.meetingId ? setPrep({ meetingId: hero.meetingId, title: hero.title }) : ask())}>Подготовить сводку</button>
          {hero.project
            ? <button className="btn s36" onClick={() => navigate('/projects')}>Открыть проект</button>
            : hero.meetingId && <button className="btn s36" onClick={() => navigate(recordLink(hero.meetingId!))}>Открыть запись</button>}
        </div>
      </div>
      <div className="hero-cols">
        {columns.map(c => (
          <div className="hero-col" key={c.title}>
            <div className="label-caps">{c.title}</div>
            {c.items.length === 0 && <div className="muted-text" style={{ fontSize: 14 }}>{c.empty}</div>}
            {c.items.map(i => i.task ? (
              <div className="hero-item" key={i.key}>
                <button type="button" className="t hero-link" onClick={i.go}>{i.text}</button>
                <div className="m">{i.meta}<DueButton task={i.task} /></div>
              </div>
            ) : (
              <button className="hero-item" key={i.key} onClick={i.go}>
                <div className="t">{i.text}</div>
                <div className="m">{i.meta}</div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </section>
  )
}

export function Today() {
  const { state } = useApp()
  const { data, error } = useApi<TodayPayload>('/api/today')
  const actions = useActions()
  const run = state.running

  if (!data) return <>{error ? <ErrorBox text={error} /> : <Loading />}</>
  const draftLink = data.drafts ? `Черновики: ${data.drafts}` : 'Все задачи'

  return (
    <>
      <div className="page-head" style={{ alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div className="eyebrow">{longDate()}</div>
          <h1 className="h1">Сегодня</h1>
        </div>
        <button className="btn s34" onClick={() => navigate('/calendar')}>Календарь</button>
      </div>

      {run && (
        <button className="procbar" onClick={() => navigate('/records')}>
          <div className="info">
            <div className="ellipsis" style={{ fontSize: 14, fontWeight: 600 }}>{run.file}</div>
            <div style={{ fontSize: 13, color: 'var(--text2)' }}>{stageLine(run.stage_label, run.stage_index)}</div>
          </div>
          <div className="track"><i style={{ width: run.percent + '%' }} /></div>
          <div className="num" style={{ fontSize: 14, fontWeight: 600, color: 'var(--accent)' }}>{Math.floor(run.percent)}%</div>
        </button>
      )}

      <MorningCard />

      {data.hero
        ? <HeroCard hero={data.hero} />
        : <div className="empty" style={{ marginTop: 28 }}>Пока нет ни одной записи. Положите файл в папку записей и нажмите «Обработать новые».</div>}

      <div className="grid-today">
        <section className="card panel">
          <div className="panel-head">
            <h3 className="h3">Новые записи</h3>
            <button className="link-btn" onClick={() => navigate('/records')}>Все записи</button>
          </div>
          <div className="rows">
            {data.newRecords.length === 0 && <div className="muted-text">Новых записей нет</div>}
            {data.newRecords.map(r => (
              <button className="row-hover" key={r.id} onClick={() => navigate(recordLink(r.id))}>
                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <div className="row-title ellipsis">{r.title}</div>
                  <div className="row-meta">{[dayLabel(r.date) + ', ' + r.time, r.durationSec != null ? fmt(r.durationSec) : '', r.project?.title ?? 'Без проекта'].join(' · ')}</div>
                </div>
                {r.status === 'needs_review' && <span className="review-badge">Проверить</span>}
              </button>
            ))}
          </div>
        </section>

        <section className="card panel">
          <div className="panel-head">
            <h3 className="h3">Задачи</h3>
            <button className="link-btn" onClick={() => navigate('/tasks')}>{draftLink}</button>
          </div>
          <div className="rows">
            {data.tasks.length === 0 && <div className="muted-text">{data.drafts ? 'Подтверждённых задач пока нет — проверьте черновики' : 'Открытых задач нет'}</div>}
            {data.tasks.map((k, i) => (
              <TaskRow key={k.id} task={k} first={i === 0} source="time"
                onToggle={() => actions.toggleTask(k)} onConfirm={() => actions.confirmTask(k)} onReject={() => actions.rejectTask(k)}
                onOpen={() => navigate(recordLink(k.meetingId, { t: k.tSec }))} />
            ))}
          </div>
        </section>

        <section className="card panel">
          <h3 className="h3">Решения за неделю</h3>
          <div className="rows">
            {data.decisions.length === 0 && <div className="muted-text">За последние 7 дней решений нет</div>}
            {data.decisions.map(d => (
              <button className="row-hover" key={d.id} style={{ flexDirection: 'column', alignItems: 'stretch', gap: 2 }}
                onClick={() => navigate(recordLink(d.meetingId, { t: d.tSec }))}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span style={{ fontSize: 15, lineHeight: 1.4, flex: 1 }}>{d.text}</span>
                  {d.draft && <span className="draft-tag">черновик</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{d.meetingTitle} · {dayLabel(d.meetingDate)}</div>
              </button>
            ))}
          </div>
        </section>

        <section className="card noproj">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h3 className="h3">Без проекта</h3>
            <div style={{ fontSize: 15, color: 'var(--text2)', lineHeight: 1.5, maxWidth: 380 }}>
              {data.noProject ? 'Записи без проекта не попадают в хронологию и подготовку к встречам.' : 'Все записи разложены по проектам.'}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
            <span className="big-num">{data.noProject}</span>
            {data.noProject > 0 && <button className="btn s36" onClick={() => navigate('/records?project=' + encodeURIComponent('Без проекта'))}>Разобрать</button>}
          </div>
        </section>
      </div>
    </>
  )
}

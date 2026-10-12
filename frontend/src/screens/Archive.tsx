import { api } from '../api'
import { useActions } from '../actions'
import { dayLabel, fmt, plural } from '../format'
import { navigate } from '../router'
import { useApi } from '../useApi'
import type { MeetingsResponse } from '../types'
import { ErrorBox, Loading } from '../components/ui'

export function Archive() {
  const { data, error } = useApi<MeetingsResponse>('/api/meetings?archived=true')
  const actions = useActions()
  const items = data?.items ?? []

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <h1 className="h1">Архив</h1>
          <div className="subtitle">
            {data ? (items.length ? `${plural(items.length, 'запись', 'записи', 'записей')} · скрыты из списков, поиска и ответов чата` : 'Архив пуст') : ' '}
          </div>
        </div>
        <button className="btn s32" onClick={() => navigate('/records')}>К записям</button>
      </div>
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && items.length > 0 && (
        <div className="card archive-card">
          {items.map((r, i) => (
            <div className={'archive-row' + (i === 0 ? ' first' : '')} key={r.id}>
              <button className="archive-main" onClick={() => navigate(`/records/${r.id}`)}>
                <span className="archive-title">{r.title}</span>
                <span className="archive-meta">
                  {dayLabel(r.date)}, {r.time}{r.durationSec != null ? ' · ' + fmt(r.durationSec) : ''}
                  {' · '}{r.project?.title ?? 'Без проекта'}
                </span>
              </button>
              <button className="btn accent s28" onClick={() => actions.safe(() => api.post(`/api/meetings/${r.id}/restore`), 'Запись возвращена из архива')}>
                Вернуть
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

import { api } from '../api'
import { useApp } from '../store'
import { useActions } from '../actions'
import type { MeetingItem } from '../types'
import { Dropdown, IconChevron } from './ui'
import { tr } from '../i18n'

/** Меню «Проект» и «Тип» записи. `keyed` — серые подписи-префиксы (как на экране «Входящие»). */
export function RecordMenus({ meeting, keyed, idPrefix }: { meeting: MeetingItem; keyed?: boolean; idPrefix: string }) {
  const { meta } = useApp()
  const actions = useActions()
  const patch = (body: object) => actions.safe(async () => { await api.patch(`/api/meetings/${meeting.id}`, body) })

  const projectOptions = [{ label: tr('Без проекта'), value: '', dot: '#8E8E93' },
    ...(meta?.projects ?? []).map(p => ({ label: p.title, value: p.id, dot: p.color }))]
  const typeOptions = (meta?.types ?? []).map(t => ({ label: t.title, value: t.id }))
  const projectValue = meeting.project?.id ?? ''
  const project = projectOptions.find(o => o.value === projectValue)

  return (
    <>
      <Dropdown id={`${idPrefix}-p`} options={projectOptions} value={projectValue} onPick={v => patch({ project_id: v })}
        trigger={(_o, toggle) => (
          <button className="mbtn" onClick={toggle}>
            {keyed && <span className="key">{tr('Проект')}</span>}
            <span className="dot" style={{ background: project?.dot ?? '#8E8E93' }} />
            <span className="ellipsis">{meeting.project?.title ?? tr('Без проекта')}</span>
            <IconChevron />
          </button>
        )} />
      <Dropdown id={`${idPrefix}-t`} options={typeOptions} value={meeting.type.id} onPick={v => patch({ record_type_id: v })}
        trigger={(_o, toggle) => (
          <button className="mbtn" onClick={toggle}>
            {keyed && <span className="key">{tr('Тип')}</span>}
            <span className="ellipsis">{meeting.type.title}</span>
            <IconChevron />
          </button>
        )} />
    </>
  )
}

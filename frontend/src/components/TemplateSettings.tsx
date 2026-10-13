import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import type { TemplateDto, TemplatesResponse } from '../types'

interface Editor { id: string; name: string; text: string; sections: TemplateDto['sections']; isNew: boolean; builtin: boolean }

const EXAMPLE = 'Например: разбор инцидента на продакшене — что случилось, причины, что исправили, какие выводы и кто что делает.'

function Sections({ items }: { items: TemplateDto['sections'] }) {
  return (
    <span className="tpl-sections">
      {items.map(s => <span key={s.key} className={'tpl-chip' + (s.free ? ' free' : '')} title={s.instruction || undefined}>{s.title}</span>)}
    </span>
  )
}

/** Блок «Шаблоны заметок» в настройках: выбор шаблона по умолчанию, правка файла шаблона, создание нового по описанию. */
export function TemplateSettings({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { showToast } = useApp()
  const [data, setData] = useState<TemplatesResponse | null>(null)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { api.get<TemplatesResponse>('/api/templates').then(setData).catch(e => setError(errorText(e))) }, [])

  const edit = async (t: TemplateDto) => {
    setError('')
    try {
      const r = await api.get<TemplateDto & { text: string }>(`/api/templates/${t.id}`)
      setEditor({ id: t.id, name: t.name, text: r.text, sections: r.sections, isNew: false, builtin: t.builtin })
    } catch (e) { setError(errorText(e)) }
  }
  const draft = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.post<TemplateDto & { text: string }>('/api/templates/draft', { description })
      setEditor({ id: r.id, name: r.name, text: r.text, sections: r.sections, isNew: true, builtin: false })
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const save = async () => {
    if (!editor) return
    setBusy(true); setError('')
    try {
      const r = await api.put<TemplatesResponse>(`/api/templates/${editor.id}`, { text: editor.text })
      setData(r); setEditor(null); setDescription('')
      showToast(r.message ?? 'Шаблон сохранён')
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const remove = async (t: TemplateDto) => {
    setBusy(true); setError('')
    try {
      const r = await api.del<TemplatesResponse>(`/api/templates/${t.id}`)
      setData(r); setEditor(null)
      if (value === t.id && !r.items.some(i => i.id === t.id)) onChange(r.default)
      showToast(r.message ?? 'Шаблон удалён')
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="field template-settings">
      <label htmlFor="default-template">Шаблоны заметок</label>
      <span className="hint">
        Шаблон задаёт, какие разделы будут в заметке. «Задачи», «Решения» и «Вопросы» всегда пишутся так, как их читает кабинет.
        Для конкретной записи шаблон выбирается во «Входящих» до обработки или потом кнопкой «Пересобрать» в самой записи.
        Если модель не справилась с шаблоном, заметка составляется стандартным.
      </span>
      {data && (
        <>
          <div className="backup-plan">
            <label htmlFor="default-template">По умолчанию</label>
            <select id="default-template" value={data.items.some(t => t.id === value) ? value : data.default}
              onChange={e => onChange(e.target.value)}>
              {data.items.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <span className="hint">Применяется после «Сохранить» внизу</span>
          </div>
          <div className="tpl-list">
            {data.items.map(t => (
              <div className="tpl-row" key={t.id}>
                <div className="tpl-main">
                  <div className="tpl-name">{t.name}{!t.builtin && <span className="tpl-own">свой</span>}</div>
                  {t.description && <div className="hint">{t.description}</div>}
                  <Sections items={t.sections.filter(s => !['map', 'flow', 'plan'].includes(s.key))} />
                </div>
                <div className="tpl-actions">
                  <button className="btn s28" disabled={busy} onClick={() => void edit(t)}>Изменить</button>
                  {!t.builtin && <button className="btn s28 muted" disabled={busy} onClick={() => void remove(t)}>
                    {t.overridesBundled ? 'Вернуть стандартный' : 'Удалить'}
                  </button>}
                </div>
              </div>
            ))}
          </div>
          {data.problems.length > 0 && <span className="hint bad">Не прочитаны: {data.problems.join('; ')}</span>}

          {!editor && (
            <div className="tpl-new">
              <label htmlFor="tpl-description">Новый шаблон по описанию</label>
              <textarea id="tpl-description" rows={3} value={description} onChange={e => setDescription(e.target.value)} placeholder={EXAMPLE} />
              <div className="dictionary-actions">
                <button className="btn s28 accent" disabled={busy || description.trim().length < 10} onClick={() => void draft()}>
                  {busy ? 'Модель думает…' : 'Предложить структуру'}
                </button>
                <span className="hint">Локальная модель предложит разделы, вы посмотрите и поправите до сохранения</span>
              </div>
            </div>
          )}

          {editor && (
            <div className="tpl-editor">
              <div className="tpl-name">{editor.isNew ? 'Предпросмотр: ' : 'Файл шаблона: '}{editor.name}</div>
              <Sections items={editor.sections} />
              {editor.builtin && <span className="hint">Это поставляемый шаблон. Сохранение создаст вашу копию, обновления программы её не затронут.</span>}
              <textarea value={editor.text} rows={Math.min(22, Math.max(10, editor.text.split('\n').length + 1))} spellCheck={false}
                onChange={e => setEditor({ ...editor, text: e.target.value })} aria-label="Текст шаблона" />
              <span className="hint">
                Разделы со стандартными ключами (summary, topics, decisions, tasks, open_questions, risks, next_steps) менять по заголовку нельзя.
                Свой раздел — любой другой ключ латиницей с заголовком title.
              </span>
              <div className="dictionary-actions">
                <button className="btn s28 accent" disabled={busy} onClick={() => void save()}>{editor.isNew ? 'Сохранить шаблон' : 'Сохранить'}</button>
                <button className="btn s28" disabled={busy} onClick={() => { setEditor(null); setError('') }}>Отмена</button>
              </div>
            </div>
          )}
        </>
      )}
      {error && <span className="hint bad">{error}</span>}
    </div>
  )
}

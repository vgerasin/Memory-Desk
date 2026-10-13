import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { tr } from '../i18n'

interface DictionaryState { text: string; count: number; warnings: string[]; path: string; message?: string }
interface ApplyResult { meetings: number; replacements: number; message: string }

const EXAMPLE = tr('КХД = кахэде, ка ха дэ\nKafka = кафка\nФилософт')

/** Блок «Словарь терминов» в настройках: слова, которые распознавание путает. Сохраняется отдельно от остальных настроек. */
export function DictionarySettings() {
  const { showToast, invalidate } = useApp()
  const [saved, setSaved] = useState('')
  const [text, setText] = useState('')
  const [count, setCount] = useState(0)
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dirty = text !== saved

  useEffect(() => {
    api.get<DictionaryState>('/api/dictionary')
      .then(r => { setSaved(r.text); setText(r.text); setCount(r.count); setWarnings(r.warnings) })
      .catch(e => setError(errorText(e)))
  }, [])

  const save = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.put<DictionaryState>('/api/dictionary', { text })
      setSaved(r.text); setText(r.text); setCount(r.count); setWarnings(r.warnings)
      showToast(r.message ?? tr('Словарь сохранён'))
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  const apply = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.post<ApplyResult>('/api/dictionary/apply')
      showToast(r.message)
      if (r.meetings) invalidate()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="field dictionary-settings">
      <label htmlFor="dictionary">{tr('Словарь терминов')}</label>
      <span className="hint">{tr('По строке на слово: «как писать = как ошибочно слышит распознавание». Слова подсказываются модели и исправляются в тексте новых записей. Изменения действуют сразу, перезапуск не нужен. Подсказка помогает в основном в начале записи, поэтому варианты ошибок надёжнее.')}</span>
      <textarea id="dictionary" value={text} onChange={e => setText(e.target.value)} spellCheck={false}
        placeholder={EXAMPLE} rows={6} />
      {warnings.length > 0 && <span className="hint bad">{warnings.join('; ')}</span>}
      <div className="dictionary-actions">
        <button className="btn s28 accent" disabled={busy || !dirty} onClick={() => void save()}>{tr('Сохранить словарь')}</button>
        <button className="btn s28" disabled={busy || dirty || count === 0} onClick={() => void apply()}
          title={dirty ? tr('Сначала сохраните словарь') : undefined}>{tr('Применить к готовым записям')}</button>
        <span className="hint">{count > 0 ? tr('Слов в словаре: {0}', [count]) : tr('Словарь пуст')}</span>
      </div>
      {error && <span className="hint bad">{error}</span>}
    </div>
  )
}

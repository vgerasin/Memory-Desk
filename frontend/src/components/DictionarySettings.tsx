import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { plural, tr } from '../i18n'

interface DictionaryState { text: string; count: number; warnings: string[]; path: string; message?: string }
interface Suggestion { wrong: string; right: string; count: number; meetings: string[] }
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
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const dirty = text !== saved

  const loadSuggestions = () => api.get<{ items: Suggestion[] }>('/api/dictionary/suggestions').then(r => setSuggestions(r.items)).catch(() => setSuggestions([]))

  useEffect(() => {
    api.get<DictionaryState>('/api/dictionary')
      .then(r => { setSaved(r.text); setText(r.text); setCount(r.count); setWarnings(r.warnings) })
      .catch(e => setError(errorText(e)))
    void loadSuggestions()
  }, [])

  const save = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.put<DictionaryState>('/api/dictionary', { text })
      setSaved(r.text); setText(r.text); setCount(r.count); setWarnings(r.warnings)
      showToast(r.message ?? tr('Словарь сохранён'))
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  const accept = async (item: Suggestion) => {
    setBusy(true); setError('')
    try {
      const r = await api.post<DictionaryState>('/api/dictionary/suggestions/accept', { wrong: item.wrong, right: item.right })
      setSaved(r.text); setText(r.text); setCount(r.count); setWarnings(r.warnings)
      showToast(r.message ?? tr('Словарь сохранён'))
      await loadSuggestions()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  const dismiss = async (item: Suggestion) => {
    try { await api.post('/api/dictionary/suggestions/dismiss', { wrong: item.wrong, right: item.right }) } catch (e) { setError(errorText(e)); return }
    setSuggestions(list => list.filter(x => x !== item))
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
      {suggestions.length > 0 && (
        <div className="dict-suggest">
          <span className="hint">{tr('Вы правили эти слова в стенограммах. Добавьте их в словарь, и в новых записях они будут исправляться сами')}</span>
          {suggestions.slice(0, 8).map(item => (
            <div className="dict-suggest-row" key={item.wrong + '=>' + item.right}>
              <span className="mono">{item.wrong}</span><span aria-hidden="true">→</span><b>{item.right}</b>
              <span className="hint">{item.count > 1 ? plural(item.count, 'раз', 'раза', 'раз') : item.meetings[0]}</span>
              <button className="btn s28" disabled={busy || dirty} title={dirty ? tr('Сначала сохраните словарь') : undefined} onClick={() => void accept(item)}>{tr('В словарь')}</button>
              <button className="btn s28" disabled={busy} onClick={() => void dismiss(item)}>{tr('Не нужно')}</button>
            </div>
          ))}
        </div>
      )}
      {error && <span className="hint bad">{error}</span>}
    </div>
  )
}

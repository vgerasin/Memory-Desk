import { useState } from 'react'
import type { Settings } from '../types'
import { tr } from '../i18n'

const PRESETS = ['turbo', 'large-v3', 'medium']

/** Блок «Распознавание речи» в настройках: модель, подсказка из имён и проектов, подготовка звука. */
export function AsrSettings({ model, hint, prep, onChange }: {
  model: string; hint: boolean; prep: NonNullable<Settings['asr_prep']>
  onChange: (key: 'asr_model' | 'asr_hint' | 'asr_prep', value: string | boolean) => void
}) {
  const custom = !PRESETS.includes(model)
  const [own, setOwn] = useState(custom)
  const prepNote = {
    off: tr('Звук не обрабатывается: так он попадает в модель как записан'),
    light: tr('Убирается гул ниже речи и выравнивается громкость'),
    strong: tr('Дополнительно подавляется ровный шум и срезается верх. Может «съесть» тихие согласные: проверьте замером'),
  }[prep]

  return (
    <div className="field asr-settings">
      <label htmlFor="asr-model">{tr('Распознавание речи')}</label>
      <span className="hint">{tr('Изменения действуют на новые записи. Что лучше для ваших записей, показывает замер на вашем эталоне (scripts/asr_benchmark.py), на слух выбирать трудно.')}</span>
      <div className="backup-plan">
        <label htmlFor="asr-model">{tr('Модель')}</label>
        <select id="asr-model" value={own ? 'custom' : model} onChange={e => {
          if (e.target.value === 'custom') { setOwn(true); return }
          setOwn(false); onChange('asr_model', e.target.value)
        }}>
          <option value="turbo">{tr('turbo: быстрая, по умолчанию')}</option>
          <option value="large-v3">{tr('large-v3: точнее, медленнее')}</option>
          <option value="medium">{tr('medium: легче по памяти')}</option>
          <option value="custom">{tr('Другая модель…')}</option>
        </select>
        {own && (
          <input type="text" value={custom ? model : ''} placeholder="mlx-community/whisper-…" spellCheck={false}
            aria-label={tr('Имя модели на Hugging Face')} onChange={e => onChange('asr_model', e.target.value.trim())} />
        )}
      </div>
      {own && <span className="hint">{tr('Полное имя вида автор/модель. Если модели нет на компьютере, она скачается при первой обработке записи.')}</span>}
      <div className="switch-row">
        <div className="field" style={{ gap: 2 }}>
          <label>{tr('Подсказывать модели имена и проекты')}</label>
          <span className="hint">{tr('К словарю терминов добавляются имена людей и названия проектов из кабинета: так реже искажаются фамилии')}</span>
        </div>
        <button className={'switch' + (hint ? ' on' : '')} role="switch" aria-checked={hint} aria-label={tr('Подсказывать модели имена и проекты')}
          onClick={() => onChange('asr_hint', !hint)}><i /></button>
      </div>
      <div className="backup-plan">
        <label htmlFor="asr-prep">{tr('Подготовка звука')}</label>
        <select id="asr-prep" value={prep} onChange={e => onChange('asr_prep', e.target.value)}>
          <option value="off">{tr('Выключена')}</option>
          <option value="light">{tr('Лёгкая')}</option>
          <option value="strong">{tr('Сильная')}</option>
        </select>
      </div>
      <span className="hint">{prepNote}</span>
    </div>
  )
}

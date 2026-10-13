import { useApp } from '../store'
import { PALETTES, type Appearance } from '../themes'

const MODES: { value: Appearance; label: string }[] = [
  { value: 'system', label: 'Как в системе' },
  { value: 'light', label: 'Светлая' },
  { value: 'dark', label: 'Тёмная' },
]

/** Блок «Оформление» в настройках: цветовая тема и режим. Применяется сразу и запоминается в этом браузере. */
export function AppearanceSettings() {
  const { palette, setPalette, appearance, setAppearance, theme } = useApp()
  return (
    <div className="field appearance">
      <label>Оформление</label>
      <div className="theme-grid" role="radiogroup" aria-label="Цветовая тема">
        {PALETTES.map(item => {
          const t = item[theme]
          const on = item.id === palette.id
          return (
            <button key={item.id} role="radio" aria-checked={on} className={'theme-card' + (on ? ' on' : '')}
              onClick={() => setPalette(item.id)} title={item.note}>
              <span className="theme-preview" style={{ backgroundColor: t.bg, backgroundImage: t.bgImage, boxShadow: `inset 0 0 0 1px ${t.sep}` }}>
                <i className="theme-bar" style={{ background: t.surface, boxShadow: t.shadow }}>
                  <b style={{ background: t.text2 }} /><b style={{ background: t.text3, width: '45%' }} />
                </i>
                <i className="theme-dot" style={{ background: t.accent }} />
              </span>
              <span className="theme-name">{item.name}</span>
            </button>
          )
        })}
      </div>
      <div className="seg small" role="tablist" style={{ alignSelf: 'flex-start' }}>
        {MODES.map(m => (
          <button key={m.value} role="tab" aria-selected={appearance === m.value} className={appearance === m.value ? 'on' : ''}
            onClick={() => setAppearance(m.value)}>{m.label}</button>
        ))}
      </div>
      <span className="hint">{palette.note}. Выбор действует сразу, остальные настройки — после «Сохранить».</span>
    </div>
  )
}

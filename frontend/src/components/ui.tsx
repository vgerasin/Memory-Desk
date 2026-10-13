import { navigate } from '../router'
import type { CSSProperties, ReactNode } from 'react'
import { useApp } from '../store'
import { tr } from '../i18n'

type IconProps = { size?: number; style?: CSSProperties }
const base = (size: number, sw: number, style?: CSSProperties) => ({
  width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: sw,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, style,
})

export const IconSearch = ({ size = 14, style }: IconProps) => (
  <svg {...base(size, 2, style)}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></svg>
)
export const IconRefresh = ({ size = 14 }: IconProps) => (
  <svg {...base(size, 2.2)}><path d="M20 12a8 8 0 1 1-2.34-5.66" /><path d="M20 4v4h-4" /></svg>
)
export const IconGear = ({ size = 17 }: IconProps) => (
  <svg {...base(size, 1.6)}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
)
export const IconLock = ({ size = 15 }: IconProps) => (
  <svg {...base(size, 1.8)}><rect x="5" y="11" width="14" height="9" rx="2.2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
)
export const IconPin = ({ size = 14 }: IconProps) => (
  <svg {...base(size, 1.8)}><path d="M9 4h6l-1 6 3 3H7l3-3-1-6z" /><path d="M12 13v7" /></svg>
)
export const IconChevron = ({ size = 10, style }: IconProps) => (
  <svg {...base(size, 2.6, style)}><path d="M6 9l6 6 6-6" /></svg>
)
export const IconCheck = ({ size = 13, sw = 2.6 }: IconProps & { sw?: number }) => (
  <svg {...base(size, sw)}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
)
export const IconPlay = ({ size = 15, style }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style}><path d="M7 4.5v15l12.5-7.5z" /></svg>
)
export const IconPause = ({ size = 14 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
)
export const IconArchive = ({ size = 17 }: IconProps) => (
  <svg {...base(size, 1.6)}><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" /></svg>
)
export const IconOut = ({ size = 11 }: IconProps) => (
  <svg {...base(size, 2.4)}><path d="M7 17L17 7M9 7h8v8" /></svg>
)
export const IconClose = ({ size = 12 }: IconProps) => (
  <svg {...base(size, 2.6)}><path d="M6 6l12 12M18 6L6 18" /></svg>
)
export const IconSend = ({ size = 14 }: IconProps) => (
  <svg {...base(size, 2.6)}><path d="M12 19V5M5 12l7-7 7 7" /></svg>
)
export const IconEdit = ({ size = 15 }: IconProps) => (
  <svg {...base(size, 1.8)}><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" /><path d="M14 7l3 3" /></svg>
)

export function Avatar({ initials, color, size, title, solo }: { initials: string; color: string; size: number; title?: string; solo?: boolean }) {
  return (
    <span className={'avatar' + (solo ? ' solo' : '')} title={title}
      style={{ width: size, height: size, background: color, fontSize: Math.round(size * (size >= 40 ? 0.35 : 0.4)) }}>
      {initials}
    </span>
  )
}

export function AvatarStack({ people, size }: { people: { name: string; initials: string; color: string }[]; size: number }) {
  if (!people.length) return null
  return (
    <div className="avatars">
      {people.map(p => <Avatar key={p.name} initials={p.initials} color={p.color} size={size} title={p.name} />)}
    </div>
  )
}

export function Segmented<T extends string>({ items, value, onChange, small, className }: {
  items: { key: T; label: string; badge?: number | null; count?: number | null }[]
  value: T; onChange: (key: T) => void; small?: boolean; className?: string
}) {
  return (
    <div className={'seg' + (small ? ' small' : '') + (className ? ' ' + className : '')} role="tablist">
      {items.map(item => (
        <button key={item.key} role="tab" aria-selected={value === item.key} className={value === item.key ? 'on' : ''}
          onClick={() => onChange(item.key)}>
          <span>{item.label}</span>
          {item.badge ? <span className="badge">{item.badge}</span> : null}
          {item.count != null && item.count !== 0 ? <span className="count">{item.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export interface MenuOption { label: string; value: string; dot?: string }

/** Выпадающее меню в стиле macOS. Одновременно открыто только одно (ключ хранится в общем состоянии). */
export function Dropdown({ id, options, value, onPick, trigger, className, popClass }: {
  id: string; options: MenuOption[]; value: string; onPick: (value: string) => void
  trigger: (open: boolean, toggle: () => void) => ReactNode; className?: string; popClass?: string
}) {
  const { openMenu, setOpenMenu } = useApp()
  const open = openMenu === id
  return (
    <div className={'dd' + (open ? ' open' : '') + (className ? ' ' + className : '')}>
      {trigger(open, () => setOpenMenu(open ? null : id))}
      {open && (
        <div className={'popover' + (popClass ? ' ' + popClass : '')} role="menu">
          {options.map(o => (
            <div key={o.value} role="menuitem" className="opt" onClick={() => { onPick(o.value); setOpenMenu(null) }}>
              <span className="tick">{o.value === value ? <IconCheck /> : null}</span>
              {o.dot ? <span className="dot" style={{ background: o.dot }} /> : null}
              <span>{o.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Checkbox({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button className={'check' + (on ? ' on' : '')} onClick={onClick} aria-label={label} aria-pressed={on}>
      {on ? <IconCheck size={11} sw={3.2} /> : null}
    </button>
  )
}

export function Loading({ text = tr('Загрузка…') }: { text?: string }) {
  return <div className="empty flat">{text}</div>
}

export function ErrorBox({ text }: { text: string }) {
  return <div className="error-box">{text}</div>
}

/** Переключатель внутри пункта «Проекты и люди»: у обоих экранов один и тот же. */
export function DirectoryTabs({ current }: { current: 'projects' | 'people' }) {
  return (
    <Segmented small className="dir-tabs" value={current} onChange={key => navigate('/' + key)}
      items={[{ key: 'projects', label: tr('Проекты') }, { key: 'people', label: tr('Люди') }]} />
  )
}

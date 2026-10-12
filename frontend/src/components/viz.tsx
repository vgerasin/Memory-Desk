import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Segmented } from './ui'

/* Вспомогательные части графиков: подсказка, карточка с «Таблицей», шкалы и даты. */

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

export function shortDate(iso: string): string {
  const [, m, d] = iso.split('-').map(Number)
  return m ? `${d} ${MONTHS[m - 1]}` : iso
}
export const dayNumber = (iso: string) => Math.floor(Date.parse(iso + 'T00:00:00Z') / 86400000)
export const addDays = (iso: string, n: number) => new Date((dayNumber(iso) + n) * 86400000).toISOString().slice(0, 10)

export function hoursText(minutes: number): string {
  const h = Math.floor(minutes / 60), m = minutes % 60
  if (!h) return `${m} мин`
  return m ? `${h} ч ${m} мин` : `${h} ч`
}

export function niceMax(value: number): { max: number; step: number } {
  if (value <= 0) return { max: 4, step: 1 }
  const raw = value / 4
  const pow = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 5, 10].map(f => f * pow).find(s => s >= raw) ?? raw
  return { max: Math.ceil(value / step) * step, step }
}

/** Прямоугольник со скруглением только на конце: сверху (колонка), справа (полоса) или без. */
export function barPath(x: number, y: number, w: number, h: number, round: 'top' | 'right' | 'none', r = 4): string {
  const k = Math.min(r, w / 2, h / 2)
  if (round === 'top') return `M${x},${y + h}V${y + k}Q${x},${y} ${x + k},${y}H${x + w - k}Q${x + w},${y} ${x + w},${y + k}V${y + h}Z`
  if (round === 'right') return `M${x},${y}H${x + w - k}Q${x + w},${y} ${x + w},${y + k}V${y + h - k}Q${x + w},${y + h} ${x + w - k},${y + h}H${x}Z`
  return `M${x},${y}H${x + w}V${y + h}H${x}Z`
}

/** Ширина контейнера: график рисуется в натуральных пикселях, чтобы шрифты и толщина линий не масштабировались. */
export function useWidth(fallback = 720): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(fallback)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setWidth(Math.max(320, Math.round(el.getBoundingClientRect().width)))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

export interface TipState { x: number; y: number; title: string; lines: string[] }

/** Подсказка у курсора; для клавиатуры — у элемента при фокусе. */
export function useTip() {
  const [tip, setTip] = useState<TipState | null>(null)
  const at = useCallback((e: { clientX: number; clientY: number }, title: string, lines: string[] = []) =>
    setTip({ x: e.clientX, y: e.clientY, title, lines }), [])
  const onElement = useCallback((el: Element, title: string, lines: string[] = []) => {
    const r = el.getBoundingClientRect()
    setTip({ x: r.left + r.width / 2, y: r.top, title, lines })
  }, [])
  const hide = useCallback(() => setTip(null), [])
  const shown = tip !== null
  useEffect(() => {
    if (!shown) return
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => { window.removeEventListener('scroll', hide, true); window.removeEventListener('resize', hide) }
  }, [shown, hide])
  const node = tip && (
    <div className="viz-tip" role="tooltip"
      style={{ left: Math.min(tip.x + 14, window.innerWidth - 260), top: Math.max(8, tip.y - 12), transform: 'translateY(-100%)' }}>
      <div className="viz-tip-title">{tip.title}</div>
      {tip.lines.map((l, i) => <div key={i} className="viz-tip-line">{l}</div>)}
    </div>
  )
  return { at, onElement, hide, node }
}

export interface TableView { head: string[]; rows: (string | number)[][] }

/** Карточка графика: заголовок, подпись, легенда, переключатель «График / Таблица». */
export function ChartCard({ title, sub, legend, table, wide, children }: {
  title: string; sub?: string; legend?: { label: string; color: string }[]; table?: TableView; wide?: boolean; children: ReactNode
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <section className={'card viz viz-card' + (wide ? ' wide' : '')}>
      <div className="viz-head">
        <div>
          <h2 className="viz-title">{title}</h2>
          {sub && <div className="viz-sub">{sub}</div>}
        </div>
        {table && (
          <Segmented small value={view} onChange={setView}
            items={[{ key: 'chart', label: 'График' }, { key: 'table', label: 'Таблица' }]} />
        )}
      </div>
      {legend && view === 'chart' && (
        <div className="viz-legend">
          {legend.map(l => <span key={l.label}><i style={{ background: l.color }} />{l.label}</span>)}
        </div>
      )}
      {view === 'chart' || !table ? children : (
        <div className="viz-table-wrap">
          <table className="viz-table">
            <thead><tr>{table.head.map(h => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j ? 'num' : ''}>{c}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </section>
  )
}

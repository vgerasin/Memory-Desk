import { useApp } from '../store'

export function Toast() {
  const { toast, hideToast } = useApp()
  if (!toast) return null
  return (
    <div className={'toast' + (toast.undo ? ' has-undo' : '')} role="status" key={toast.id}>
      <span>{toast.text}</span>
      {toast.undo && <button onClick={() => { toast.undo?.(); hideToast() }}>Отменить</button>}
    </div>
  )
}

import { useApp } from '../store'
import { tr } from '../i18n'

export function Toast() {
  const { toast, hideToast } = useApp()
  if (!toast) return null
  return (
    <div className={'toast' + (toast.undo ? ' has-undo' : '')} role="status" key={toast.id}>
      <span>{toast.text}</span>
      {toast.undo && <button onClick={() => { toast.undo?.(); hideToast() }}>{tr('Отменить')}</button>}
    </div>
  )
}

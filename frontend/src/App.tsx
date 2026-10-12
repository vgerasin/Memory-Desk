import { Component, type ReactNode } from 'react'
import { Chat } from './components/Chat'
import { Header } from './components/Header'
import { SettingsModal } from './components/Settings'
import { StorageModal } from './components/Storage'
import { PrepModal } from './components/PrepBrief'
import { MaterialModal } from './components/Material'
import { Toast } from './components/Toast'
import { useRoute } from './router'
import { useApp } from './store'
import { Analytics } from './screens/Analytics'
import { Archive } from './screens/Archive'
import { Calendar } from './screens/Calendar'
import { Inbox } from './screens/Inbox'
import { People } from './screens/People'
import { Projects } from './screens/Projects'
import { Records } from './screens/Records'
import { Tasks } from './screens/Tasks'
import { Today } from './screens/Today'

function Screen() {
  const { segments } = useRoute()
  switch (segments[0]) {
    case 'archive': return <Archive />
    case 'inbox': return <Inbox />
    case 'records': return <Records />
    case 'projects': return <Projects />
    case 'people': return <People />
    case 'tasks': return <Tasks />
    case 'calendar': return <Calendar />
    case 'analytics': return <Analytics />
    default: return <Today />
  }
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="empty" style={{ marginTop: 48 }}>
        Экран не отобразился. Чаще всего так бывает, когда страница обновилась, а сервер ещё старой версии: закройте Memory Desk и запустите start.command заново.
        <div style={{ marginTop: 12 }}><button className="btn s34" onClick={() => location.reload()}>Перезагрузить страницу</button></div>
      </div>
    )
  }
}

export default function App() {
  const { live } = useApp()
  return (
    <div className="app">
      <Header />
      {!live && <div className="offline">Нет связи с Memory Desk — данные могут быть устаревшими. Переподключаюсь…</div>}
      <main className="main"><Boundary><Screen /></Boundary></main>
      <Chat />
      <SettingsModal />
      <StorageModal />
      <PrepModal />
      <MaterialModal />
      <Toast />
    </div>
  )
}

import { Component, useEffect, type ReactNode } from 'react'
import { Chat } from './components/Chat'
import { Header } from './components/Header'
import { SettingsModal } from './components/Settings'
import { StorageModal } from './components/Storage'
import { AuditModal } from './components/Security'
import { SetupWizard, useSetupAutoOpen } from './components/Setup'
import { EraseDialog } from './components/EraseDialog'
import { PrepModal } from './components/PrepBrief'
import { MaterialModal } from './components/Material'
import { Toast } from './components/Toast'
import { navigate, useRoute } from './router'
import { useApp } from './store'
import { Analytics } from './screens/Analytics'
import { Archive } from './screens/Archive'
import { People } from './screens/People'
import { Person } from './screens/Person'
import { Project } from './screens/Project'
import { Projects } from './screens/Projects'
import { Records } from './screens/Records'
import { Tasks } from './screens/Tasks'
import { Today } from './screens/Today'
import { tr } from './i18n'

function Redirect({ to }: { to: string }) {
  useEffect(() => { navigate(to, true) }, [to])
  return null
}

function Screen() {
  const { segments } = useRoute()
  switch (segments[0]) {
    case 'archive': return <Archive />
    case 'inbox': return <Redirect to="/records" />   // «Входящие» теперь часть «Записей»; старые ссылки и закладки ведут туда
    case 'records': return <Records />
    case 'projects': return segments[1] ? <Project /> : <Projects />
    case 'people': return segments[1] ? <Person /> : <People />
    case 'tasks': return <Tasks />
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
      <div className="empty" style={{ marginTop: 48 }}>{tr('Экран не отобразился. Чаще всего так бывает, когда страница обновилась, а сервер ещё старой версии: закройте Memory Desk и запустите start.command заново.')}{' '}<div style={{ marginTop: 12 }}><button className="btn s34" onClick={() => location.reload()}>{tr('Перезагрузить страницу')}</button></div>
      </div>
    )
  }
}

export default function App() {
  const { live } = useApp()
  useSetupAutoOpen()
  return (
    <div className="app">
      <Header />
      {!live && <div className="offline">{tr('Нет связи с Memory Desk — данные могут быть устаревшими. Переподключаюсь…')}</div>}
      <main className="main"><Boundary><Screen /></Boundary></main>
      <Chat />
      <SettingsModal />
      <StorageModal />
      <AuditModal />
      <SetupWizard />
      <EraseDialog />
      <PrepModal />
      <MaterialModal />
      <Toast />
    </div>
  )
}

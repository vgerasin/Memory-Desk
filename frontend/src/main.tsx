import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { initLang } from './i18n'
import './styles.css'

async function boot() {
  // Язык выбирается до загрузки остального кода: надписи, собранные при загрузке модулей, уже на нужном языке.
  await initLang()
  const [{ default: App }, { LockGate }, { AppProvider }, { applyPalette, findPalette }] = await Promise.all([
    import('./App'), import('./lock'), import('./store'), import('./themes'),
  ])
  // Тема ставится до первой отрисовки, чтобы страница не мигала цветами по умолчанию.
  try {
    const saved = localStorage.getItem('md-appearance') ?? localStorage.getItem('md-theme')
    const dark = saved === 'dark' || (saved !== 'light' && window.matchMedia?.('(prefers-color-scheme: dark)').matches)
    applyPalette(findPalette(localStorage.getItem('md-palette')), dark ? 'dark' : 'light')
  } catch { /* хранилище недоступно: остаются значения из styles.css */ }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <LockGate>
        <AppProvider>
          <App />
        </AppProvider>
      </LockGate>
    </StrictMode>,
  )
}

void boot()

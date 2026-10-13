import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { LockGate } from './lock'
import { AppProvider } from './store'
import './styles.css'
import { applyPalette, findPalette } from './themes'

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

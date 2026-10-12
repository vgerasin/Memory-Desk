import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: '../memory_desk/web', emptyOutDir: true, sourcemap: false },
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8502', changeOrigin: false } } },
})

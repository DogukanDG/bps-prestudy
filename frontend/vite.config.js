import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    open: 'http://localhost:5173',
    browser: {
      name: 'chrome',
      arguments: ['--new-window']
    }
  }
})

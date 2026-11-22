import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to the FastAPI backend (uvicorn on port 8000) during development.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})

/// <reference types="vitest/config" />
// Vite: dev server + production build for the single React app (docs/02 §3).
import { fileURLToPath } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // "@/lib/utils" -> src/lib/utils. fileURLToPath gives a correct path on Windows and Linux.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // Windows bind mounts don't deliver file-change events into Linux
    // containers, so the dev server polls when told to (compose.yaml, Q7).
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    // Same origin in development: the browser calls /api on Vite, which forwards
    // to FastAPI. No CORS configuration needed (docs/02 §3).
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET ?? 'http://localhost:8000', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'], // e2e/*.spec.ts are Playwright's, not Vitest's
    css: { include: [/index\.css/] }, // theme-contrast.test.ts reads the real design tokens
  },
})

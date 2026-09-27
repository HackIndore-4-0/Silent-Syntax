import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds INTO ../dashboard (the directory server/api.py already serves:
// FileResponse(dashboard/index.html) at "/" and StaticFiles(dashboard/)
// mounted at "/dashboard"). base: "/dashboard/" makes Vite emit asset
// URLs (e.g. /dashboard/assets/index-xxxx.js) that resolve correctly
// under that existing mount, with zero server-side changes needed.
export default defineConfig({
  plugins: [react()],
  base: '/dashboard/',
  build: {
    outDir: '../dashboard',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8010',
    },
  },
})

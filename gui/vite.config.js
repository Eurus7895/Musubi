import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Musubi console — standalone recreation of the Claude Design prototype.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    watch: {
      ignored: ['**/src-tauri/target/**', '**/target/**'],
    },
  },
})

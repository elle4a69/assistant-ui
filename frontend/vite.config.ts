import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 5190,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8025',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8025',
        ws: true,
        changeOrigin: false,
      },
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        'booking-inline': 'src/booking-inline.tsx',
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === 'booking-inline' ? 'booking-inline.js' : 'assets/[name]-[hash].js',
      },
    },
  },
})

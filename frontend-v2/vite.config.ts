import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Prefer TS sources when duplicate transpiled JS files exist in src.
    extensions: ['.tsx', '.ts', '.jsx', '.js', '.mjs', '.json']
  },
  server: {
    host: '127.0.0.1',
    port: 8510,
    strictPort: true
  },
  preview: {
    host: '127.0.0.1',
    port: 8510,
    strictPort: true
  }
});

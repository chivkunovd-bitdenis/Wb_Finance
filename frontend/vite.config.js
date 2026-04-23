import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Прод-HTML под Cloudflare/Chrome:
 * - data-cfasync="false" — не давать Rocket Loader ломать ES modules;
 * - убрать crossorigin у script и stylesheet: при прокси иногда ломается применение CSS (пустой/«серый» экран).
 */
function productionIndexHtmlFixes() {
  return {
    name: 'production-index-html-fixes',
    transformIndexHtml(html) {
      let h = html.replace(
        /<script type="module" crossorigin src="(\/assets\/[^"]+)"/g,
        '<script type="module" data-cfasync="false" src="$1"',
      )
      h = h.replace(
        /<link rel="stylesheet" crossorigin href="(\/assets\/[^"]+)"/g,
        '<link rel="stylesheet" href="$1"',
      )
      return h
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), productionIndexHtmlFixes()],
  server: {
    proxy: {
      '/auth': 'http://localhost:8000',
      '/sync': 'http://localhost:8000',
      '/stores': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/openapi.json': 'http://localhost:8000',
      '/dashboard/state': 'http://localhost:8000',
      '/dashboard/pnl': 'http://localhost:8000',
      '/dashboard/articles': 'http://localhost:8000',
      '/dashboard/articles/cost': 'http://localhost:8000',
      '/dashboard/funnel': 'http://localhost:8000',
      '/dashboard/sku': 'http://localhost:8000',
      '/dashboard/operational-expenses': 'http://localhost:8000',
    },
  },
})

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const prototypeRoot = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(prototypeRoot, '../..');
const sourceRoot = path.join(frontendRoot, 'src');
const fixtureApi = path.join(prototypeRoot, 'fixtures.js');

function localApiFixtureAlias() {
  const sourceApi = path.join(sourceRoot, 'api');
  return {
    name: 'table-expand-local-api-fixtures',
    enforce: 'pre',
    transform(code, id) {
      const cleanId = id.split('?')[0];
      if (cleanId === path.join(sourceRoot, 'Layout.jsx')) {
        const originalDashboardRoute = `element={marketplace === 'wb'
                    ? <Dashboard range={range} refreshTrigger={refreshTrigger} cache={cache} updateCache={updateCache} />
                    : <MarketplaceFinance key={\`${'${activeStoreOwnerId}:${marketplace}:${range.dateFrom}:${range.dateTo}'}\`} view="dashboard" marketplace={marketplace} range={range} refreshTrigger={0} />}`;
        const prototypeDashboardRoute = `<Dashboard key={marketplace} marketplace={marketplace} range={range} refreshTrigger={refreshTrigger} cache={marketplace === 'wb' ? cache : undefined} updateCache={marketplace === 'wb' ? updateCache : undefined} />`;
        return code
          .replace(originalDashboardRoute, `element={${prototypeDashboardRoute}}`)
          .replace(
            "const [marketplace, setMarketplace] = useState('wb');",
            "const [marketplace, setMarketplace] = useState(() => new URLSearchParams(window.location.search).get('marketplace') === 'ozon' ? 'ozon' : 'wb');",
          )
          .replace(
            "className={`layout ${marketplace === 'wb' ? '' : 'marketplace-mode'}`.trim()}",
            "className={`layout ${marketplace === 'wb' || location.pathname === '/dashboard' ? '' : 'marketplace-mode'}`.trim()}",
          );
      }

      if (cleanId === path.join(sourceRoot, 'screens/Dashboard.jsx')) {
        return code
          .replace(
            'export default function Dashboard({ range, refreshTrigger, cache, updateCache }) {',
            "export default function Dashboard({ range, refreshTrigger, cache, updateCache, marketplace = 'wb' }) {",
          )
          .replace('.getPnl(dateFrom, dateTo)', '.getPnl(dateFrom, dateTo, marketplace)')
          .replace('.getFunnel(dateFrom, dateTo)', '.getFunnel(dateFrom, dateTo, marketplace)')
          .replace("{ label: 'Доля расходов ВБ', key: 'wb_expenses_share'", "{ label: marketplace === 'ozon' ? 'Доля расходов Ozon' : marketplace === 'all' ? 'Доля расходов МП' : 'Доля расходов ВБ', key: 'wb_expenses_share'")
          .replace('    [],\n  );\n\n  const optionalColumns', '    [marketplace],\n  );\n\n  const optionalColumns');
      }
      return null;
    },
    resolveId(source, importer) {
      if (!importer) return null;
      const cleanImporter = importer.split('?')[0];
      if (!cleanImporter.startsWith(`${sourceRoot}${path.sep}`)) return null;
      const candidate = path.resolve(path.dirname(cleanImporter), source);
      return candidate === sourceApi ? fixtureApi : null;
    },
  };
}

export default defineConfig({
  root: prototypeRoot,
  base: '/',
  publicDir: false,
  plugins: [localApiFixtureAlias(), react()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    fs: { allow: [frontendRoot] },
  },
  build: {
    outDir: path.join(prototypeRoot, '.build'),
    emptyOutDir: true,
    rollupOptions: { input: path.join(prototypeRoot, 'index.html') },
  },
});

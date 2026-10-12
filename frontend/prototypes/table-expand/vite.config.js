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
  plugins: [react(), localApiFixtureAlias()],
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

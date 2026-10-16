import { defineConfig, build, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, 'VITE_')

  return {
    define: {
      __REVIEW_AGENT_BUILD_MODE__: JSON.stringify(mode),
      __REVIEW_AGENT_BASE_URL__: JSON.stringify(
        process.env.VITE_AGENT_BASE_URL ?? env.VITE_AGENT_BASE_URL ?? '',
      ),
    },
    plugins: [vue(), {
      name: 'extension-entries',
      async closeBundle() {
        const localBuild = mode !== 'public'
        const entries = ['content', 'background']
        for (const entry of entries) {
          await build({ configFile: false, build: {
            emptyOutDir: false,
            lib: { entry: resolve(import.meta.dirname, `src/entrypoints/${entry}.ts`), formats: ['iife'], name: entry.replace(/-/g, '_'), fileName: () => `${entry}.js` },
          } })
        }

        const manifestPath = resolve(import.meta.dirname, 'dist/manifest.json')
        const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
        const extensionName = localBuild ? '赋界资料审核工作台(本地)' : '赋界资料审核工作台'
        manifest.name = extensionName
        manifest.action.default_title = `打开${extensionName}`
        await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
      },
    }],
  }
})

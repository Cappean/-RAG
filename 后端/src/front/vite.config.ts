import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs'

export default defineConfig({
  plugins: [vue(), {
    name: 'pdfjs-font-resources',
    buildStart() {
      // CJK PDFs may refer to predefined CMaps rather than embedding every glyph.
      // Serve the matching local pdfjs resources in dev and copy them into dist.
      // These packaged directories are flat. Avoid recursive fs.cp on Windows,
      // which can terminate Node before Vite is able to report an exception.
      for (const directory of ['cmaps', 'standard_fonts']) {
        mkdirSync(`public/pdfjs/${directory}`, { recursive: true })
        for (const name of readdirSync(`node_modules/pdfjs-dist/${directory}`)) {
          const destination = `public/pdfjs/${directory}/${name}`
          if (!existsSync(destination)) {
            copyFileSync(`node_modules/pdfjs-dist/${directory}/${name}`, destination)
          }
        }
      }
    },
  }],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})

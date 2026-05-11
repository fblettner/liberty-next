// Bundle Monaco with the app — no runtime CDN fetch. Imported for its side
// effects from the Settings page only, so monaco-editor lands in that page's
// lazy chunk (not the entry bundle). We pull in just the editor API + the `ini`
// basic language (the connector-config editor's only mode), so all the other
// basic languages / JSON-CSS-HTML-TS modes are left out. Vite turns `?worker`
// into a separate worker bundle; only the core editor worker is needed.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/basic-languages/ini/ini.contribution'
import { loader } from '@monaco-editor/react'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
}

loader.config({ monaco })

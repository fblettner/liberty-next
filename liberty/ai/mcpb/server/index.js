// Liberty Next for Claude Desktop — a bridge, nothing more.
//
// Claude Desktop talks to an extension over standard input / output, one
// JSON-RPC message per line; Liberty answers the same messages over HTTP at
// /mcp. Each line read is posted to the server with the user's personal
// access token and the answer is written back, so the server only has to be
// reachable from this machine (an internal address works). No dependency:
// Node's own modules.
//
// Two tools are answered here and not by the server, because only this
// program sees the user's disk: local_files lists the spreadsheets and
// delimited files of the folder chosen when the extension was installed, and
// send_file posts one of them to Liberty for a job that loads a file (the
// job's own upload area — nothing runs until job_run). No other folder is
// ever read.
'use strict'

const fs = require('node:fs')
const path = require('node:path')

const http = require('node:http')
const https = require('node:https')
const readline = require('node:readline')

const address = (process.env.LIBERTY_URL || '').trim()
const token = (process.env.LIBERTY_TOKEN || '').trim().replace(/^Bearer\s+/i, '')
const insecure = /^(true|1|yes)$/i.test((process.env.LIBERTY_INSECURE || '').trim())

const log = text => process.stderr.write(`[liberty] ${text}\n`)

// The files folder; an unset setting can arrive as its own placeholder.
const folderSetting = (process.env.LIBERTY_FOLDER || '').trim()
let folder = ''
if (folderSetting && !folderSetting.startsWith('${')) {
  try { folder = fs.realpathSync(folderSetting) } catch (e) { log(`the files folder cannot be read: "${folderSetting}"`) }
}
const MAX_BYTES = 100 * 1024 * 1024   // the server has its own, lower limit ([jobs] max_upload_mb)
// what a job's upload takes — the server checks again
const SAMPLE = /\.(xlsx|xlsm|csv|tsv|txt)$/i

const LOCAL_TOOLS = [
  {
    name: 'local_files',
    description: 'The spreadsheets and delimited files (.xlsx, .xlsm, .csv, .tsv, .txt) of the files folder on this computer (the one chosen in the Liberty extension settings), newest first. Call it when the user names a file of theirs for a job that loads one, then send_file.',
    inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true },
  },
  {
    name: 'send_file',
    description: 'Send one file of the files folder on this computer to Liberty, for a job that loads a file (job_form says which step needs one). Nothing runs yet: then call job_run with files = {<step>: <the name returned>}.',
    inputSchema: {
      type: 'object', required: ['job', 'file'],
      properties: {
        job: { type: 'string', description: 'The job the file is for (its id)' },
        file: { type: 'string', description: 'File name as local_files lists it' },
      },
    },
    annotations: { readOnlyHint: false },
  },
]

let target = null
try {
  target = new URL(address)
  if (!/\/mcp\/?$/.test(target.pathname)) target.pathname = target.pathname.replace(/\/?$/, '/mcp')
} catch (e) {
  log(`the Liberty address is not valid: "${address}"`)
}

/** Posts one message, or a file to `to`; resolves {status, body}. */
function post(body, to, type) {
  return new Promise((resolve, reject) => {
    const lib = target.protocol === 'https:' ? https : http
    const req = lib.request(to || target, {
      method: 'POST',
      headers: {
        'Content-Type': type || 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': `Bearer ${token}`,
        'Content-Length': Buffer.byteLength(body),
      },
      rejectUnauthorized: !insecure,
      timeout: 120000,
    }, res => {
      const chunks = []
      res.on('data', c => chunks.push(c))
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }))
    })
    req.on('timeout', () => req.destroy(new Error('no answer from the server after 120 seconds')))
    req.on('error', reject)
    req.end(body)
  })
}

/** Why a call failed, in words the user can act on. */
function reason(status, body) {
  let said = ''
  try { const parsed = JSON.parse(body); said = parsed.detail || parsed.error || '' } catch (e) { /* not JSON */ }
  if (typeof said === 'object' && said) said = said.message || ''
  if (status === 401) return `Liberty refused the personal access token${said ? ' (' + said + ')' : ''}: create a new one in your Liberty profile (tab Access tokens) and update the extension settings.`
  if (status === 403) return `Liberty refused the access: ${said || 'not allowed'}.`
  if (status === 404) return said || 'Liberty has no MCP endpoint at this address: check the address, and that MCP is enabled (Administer > App > AI Assistant).'
  return `Liberty answered HTTP ${status}${said ? ': ' + said : ''}.`
}

function fail(message, text) {
  log(text)
  if (message && message.id !== undefined && message.id !== null) {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: text } }) + '\n')
  }
}

const answer = (message, result) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }) + '\n')
const said = (message, text, isError) => answer(message, { content: [{ type: 'text', text }], isError: !!isError })

const NO_FOLDER = 'No files folder is set: open the Liberty extension settings in Claude Desktop and choose one.'

/** The files of the folder that can be sent, newest first. */
function localFiles() {
  return fs.readdirSync(folder, { withFileTypes: true })
    .filter(e => e.isFile() && SAMPLE.test(e.name))
    .map(e => { const st = fs.statSync(path.join(folder, e.name)); return { name: e.name, size: st.size, modified: st.mtime.toISOString() } })
    .sort((a, b) => b.modified.localeCompare(a.modified))
}

/** A tool answered on this computer; false when the call is the server's. */
async function local(message) {
  if (message.method !== 'tools/call' || !message.params) return false
  const name = message.params.name
  if (name !== 'local_files' && name !== 'send_file') return false
  if (!folder) { said(message, NO_FOLDER, true); return true }
  try {
    if (name === 'local_files') {
      said(message, JSON.stringify({ folder, files: localFiles().slice(0, 200) }))
      return true
    }
    const asked = String((message.params.arguments || {}).file || '').trim()
    const job = String((message.params.arguments || {}).job || '').trim()
    if (!job) { said(message, 'Say which job the file is for (job).', true); return true }
    // the name only: a path would be a way out of the folder
    const file = path.join(folder, path.basename(asked))
    if (!asked || !SAMPLE.test(file)) { said(message, 'Only a spreadsheet or a delimited file (.xlsx, .xlsm, .csv, .tsv, .txt) of the files folder can be sent.', true); return true }
    let real = ''
    try { real = fs.realpathSync(file) } catch (e) { said(message, `No file named ${path.basename(asked)} in ${folder}.`, true); return true }
    if (path.dirname(real) !== folder) { said(message, 'That file is not in the files folder.', true); return true }
    const bytes = fs.readFileSync(real)
    if (bytes.length > MAX_BYTES) { said(message, 'The file is larger than 100 MB.', true); return true }
    const to = new URL(target)
    to.pathname = to.pathname.replace(/\/?$/, '/files')
    to.search = '?job=' + encodeURIComponent(job) + '&filename=' + encodeURIComponent(path.basename(real))
    const { status, body } = await post(bytes, to, 'application/octet-stream')
    if (status >= 200 && status < 300) said(message, body)
    else said(message, reason(status, body), true)
  } catch (e) {
    said(message, `The file could not be sent (${e.code || e.message}).`, true)
  }
  return true
}

/** The server's tool list with the two local tools, offered when a folder is set. */
function withLocalTools(message, body) {
  if (message.method !== 'tools/list' || !folder) return body
  try {
    const parsed = JSON.parse(body)
    if (parsed && parsed.result && Array.isArray(parsed.result.tools)) {
      parsed.result.tools.push(...LOCAL_TOOLS)
      return JSON.stringify(parsed)
    }
  } catch (e) { /* left as it is */ }
  return body
}

let pending = 0
let closed = false
const done = () => { if (closed && pending === 0) process.exit(0) }

async function relay(line) {
  let message = null
  try { message = JSON.parse(line) } catch (e) { log('ignored a line that is not JSON'); return }
  if (!target) { fail(message, 'The Liberty address is missing or not valid: open the extension settings.'); return }
  if (!token) { fail(message, 'The personal access token is missing: open the extension settings.'); return }
  try {
    if (await local(message)) return
    const { status, body } = await post(line)
    if (status === 202 || (status >= 200 && status < 300 && !body.trim())) return
    if (status >= 200 && status < 300) { process.stdout.write(withLocalTools(message, body.trim().replace(/\r?\n/g, ' ')) + '\n'); return }
    try {
      const parsed = JSON.parse(body)
      if (parsed && parsed.jsonrpc) { process.stdout.write(JSON.stringify(parsed) + '\n'); return }
    } catch (e) { /* a plain refusal */ }
    fail(message, reason(status, body))
  } catch (e) {
    const code = e && e.code ? e.code : ''
    const certificate = /CERT|SELF_SIGNED|UNABLE_TO_VERIFY/i.test(code)
    fail(message, certificate
      ? `The certificate of ${target.host} is not trusted (${code}): turn on "Accept an internal certificate" in the extension settings.`
      : `Liberty cannot be reached at ${target.origin} (${code || e.message}).`)
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
input.on('line', line => {
  if (!line.trim()) return
  pending++
  relay(line).finally(() => { pending--; done() })
})
input.on('close', () => { closed = true; done() })

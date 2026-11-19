// Liberty Next for Claude Desktop — a bridge, nothing more.
//
// Claude Desktop talks to an extension over standard input / output, one
// JSON-RPC message per line; Liberty answers the same messages over HTTP at
// /mcp. Each line read is posted to the server with the user's personal
// access token and the answer is written back, so the server only has to be
// reachable from this machine (an internal address works). No dependency:
// Node's own modules.
'use strict'

const http = require('node:http')
const https = require('node:https')
const readline = require('node:readline')

const address = (process.env.LIBERTY_URL || '').trim()
const token = (process.env.LIBERTY_TOKEN || '').trim().replace(/^Bearer\s+/i, '')
const insecure = /^(true|1|yes)$/i.test((process.env.LIBERTY_INSECURE || '').trim())

const log = text => process.stderr.write(`[liberty] ${text}\n`)

let target = null
try {
  target = new URL(address)
  if (!/\/mcp\/?$/.test(target.pathname)) target.pathname = target.pathname.replace(/\/?$/, '/mcp')
} catch (e) {
  log(`the Liberty address is not valid: "${address}"`)
}

/** Posts one message; resolves {status, body}. */
function post(body) {
  return new Promise((resolve, reject) => {
    const lib = target.protocol === 'https:' ? https : http
    const req = lib.request(target, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
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
  if (status === 404) return 'Liberty has no MCP endpoint at this address: check the address, and that MCP is enabled (Administer > App > AI Assistant).'
  return `Liberty answered HTTP ${status}${said ? ': ' + said : ''}.`
}

function fail(message, text) {
  log(text)
  if (message && message.id !== undefined && message.id !== null) {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: text } }) + '\n')
  }
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
    const { status, body } = await post(line)
    if (status === 202 || (status >= 200 && status < 300 && !body.trim())) return
    if (status >= 200 && status < 300) { process.stdout.write(body.trim().replace(/\r?\n/g, ' ') + '\n'); return }
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

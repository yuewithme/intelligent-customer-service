const cookieName = 'admin_gate'

function parseCookies(cookieHeader = '') {
  return Object.fromEntries(
    cookieHeader
      .split(';')
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => {
        const index = item.indexOf('=')
        return index === -1
          ? [item, '']
          : [decodeURIComponent(item.slice(0, index)), decodeURIComponent(item.slice(index + 1))]
      })
  )
}

function gateSecret() {
  return process.env.ADMIN_GATE_SECRET || process.env.ADMIN_GATE_PASSWORD || 'local-gate'
}

function gateEnabled() {
  return process.env.ADMIN_GATE_ENABLED !== 'false'
}

function isUnlocked(req) {
  if (!gateEnabled()) return true
  return parseCookies(req.headers.cookie)[cookieName] === gateSecret()
}

async function readJson(req) {
  const chunks = []
  for await (const chunk of req) {
    chunks.push(Buffer.from(chunk))
  }
  const raw = Buffer.concat(chunks).toString('utf8')
  return raw ? JSON.parse(raw) : {}
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    res.status(200).json({ code: 0, data: { unlocked: isUnlocked(req) } })
    return
  }

  if (req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST')
    res.status(405).json({ code: 405, message: 'Method Not Allowed' })
    return
  }

  const body = await readJson(req).catch(() => ({}))
  const password = String(body.password || '')
  const expected = process.env.ADMIN_GATE_PASSWORD || ''
  if (gateEnabled() && expected && password !== expected) {
    res.status(401).json({ code: 401, message: '访问密码不正确' })
    return
  }

  res.setHeader(
    'Set-Cookie',
    `${cookieName}=${encodeURIComponent(gateSecret())}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=2592000`
  )
  res.status(200).json({ code: 0, data: { unlocked: true } })
}

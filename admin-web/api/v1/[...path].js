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

function isGateUnlocked(req) {
  if (process.env.ADMIN_GATE_ENABLED === 'false') return true
  return parseCookies(req.headers.cookie)[cookieName] === gateSecret()
}

async function readBody(req) {
  const chunks = []
  for await (const chunk of req) {
    chunks.push(Buffer.from(chunk))
  }
  return chunks.length ? Buffer.concat(chunks) : undefined
}

export default async function handler(req, res) {
  if (!isGateUnlocked(req)) {
    res.status(401).json({ code: 401, message: 'Gate required' })
    return
  }

  const backendBaseUrl = process.env.BACKEND_BASE_URL
  const backendApiKey = process.env.BACKEND_API_KEY
  if (!backendBaseUrl || !backendApiKey) {
    res.status(500).json({ code: 500, message: 'Backend proxy env vars are not configured' })
    return
  }

  const path = Array.isArray(req.query.path) ? req.query.path.join('/') : req.query.path
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(req.query)) {
    if (key === 'path') continue
    if (Array.isArray(value)) {
      value.forEach((item) => query.append(key, item))
    } else if (value !== undefined) {
      query.append(key, value)
    }
  }

  const targetUrl = `${backendBaseUrl.replace(/\/$/, '')}/api/v1/${path || ''}${
    query.toString() ? `?${query.toString()}` : ''
  }`
  const headers = { ...req.headers }
  delete headers.host
  delete headers.connection
  delete headers['content-length']
  headers.authorization = `Bearer ${backendApiKey}`

  const response = await fetch(targetUrl, {
    method: req.method,
    headers,
    body: req.method === 'GET' || req.method === 'HEAD' ? undefined : await readBody(req)
  })

  res.status(response.status)
  response.headers.forEach((value, key) => {
    if (!['content-encoding', 'transfer-encoding', 'connection'].includes(key.toLowerCase())) {
      res.setHeader(key, value)
    }
  })
  res.send(Buffer.from(await response.arrayBuffer()))
}

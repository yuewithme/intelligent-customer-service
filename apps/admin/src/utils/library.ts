export const libraryApp = window.location.pathname.match(/^\/(product-knowledge|phrase-library)\/lanhua(?:\/|$)/)?.[1] || ''
export const libraryBase = libraryApp ? `/${libraryApp}/lanhua` : ''
export const libraryTitle = libraryApp === 'phrase-library' ? '话术库' : '产品知识库'
export const libraryPages: Record<string, string[]> = {
  'product-knowledge': ['/operations/products', '/operations/care-manuals', '/knowledge-ops/current-activities'],
  'phrase-library': ['/operations/conversation-cases']
}
export const migratedPages = Object.fromEntries(Object.entries(libraryPages).flatMap(([app, paths]) => paths.map(path => [path, app])))
export const libraryLogin = () => `/${libraryApp}/api/auth/login?redirect=${encodeURIComponent(location.pathname + location.search + location.hash)}`
export const apiUrl = (path: string) => libraryApp ? `/${libraryApp}/api/orchid${path.replace(/^\/api/, '')}` : path
export const portalOrigin = 'https://sales-gateway.hzwohu.com'

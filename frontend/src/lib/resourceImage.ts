const resourceIdPattern = /^[a-f0-9]{24}$/i

export function getResourceImageId(src?: string, knownId?: string): string | undefined {
  if (knownId && resourceIdPattern.test(knownId)) return knownId
  if (!src) return undefined
  const path = src.replace(/^(?:https?:)?\/\/[^/]+/i, '').split(/[?#]/, 1)[0]
  return /^\/?api\/v1\/storage\/resources\/([a-f0-9]{24})\/view\/?$/i.exec(path)?.[1]
}

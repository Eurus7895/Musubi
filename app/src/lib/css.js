export function cssToObj(css) {
  if (!css || typeof css !== 'string') return {}
  return css
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .reduce((style, part) => {
      const i = part.indexOf(':')
      if (i < 0) return style
      const key = part.slice(0, i).trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase())
      const value = part.slice(i + 1).trim()
      if (key) style[key] = value
      return style
    }, {})
}

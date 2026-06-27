// Style-string builders — ported from the prototype. They return CSS strings
// (consumed by <Box css> / cssToObj), keeping the design's exact values.

export function roleChip(role, hue) {
  return `display:inline-flex;align-items:center;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:500;padding:2px 8px;border-radius:5px;color:${hue};background:${hue}1f;border:1px solid ${hue}40`
}

export function navStyle(active) {
  return 'display:flex;align-items:center;justify-content:center;width:44px;height:44px;border-radius:10px;border:none;cursor:pointer;position:relative;transition:all .14s;'
    + 'background:' + (active ? 'rgba(255,155,61,0.12)' : 'transparent') + ';'
    + 'color:' + (active ? '#ff9b3d' : '#6a6a72') + ';'
    + 'box-shadow:' + (active ? 'inset 2px 0 0 #ff9b3d' : 'none')
}

export function auditBtn(active) {
  return "font-family:'IBM Plex Mono',monospace;font-size:11px;padding:5px 12px;border-radius:7px;cursor:pointer;transition:all .14s;"
    + 'background:' + (active ? 'rgba(255,155,61,0.12)' : '#19212f') + ';border:1px solid ' + (active ? 'rgba(255,155,61,0.4)' : 'rgba(255,255,255,0.08)') + ';color:' + (active ? '#ff9b3d' : '#9b9ba2')
}

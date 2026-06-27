import { useState } from 'react'
import { cssToObj } from './css.js'

export default function Box({
  as: Tag = 'div',
  css,
  hover,
  style,
  onMouseEnter,
  onMouseLeave,
  ...props
}) {
  const [active, setActive] = useState(false)
  const baseStyle = { ...cssToObj(css), ...(style || {}) }
  const hoverStyle = active ? cssToObj(hover) : {}

  return (
    <Tag
      {...props}
      style={{ ...baseStyle, ...hoverStyle }}
      onMouseEnter={(event) => {
        setActive(true)
        if (onMouseEnter) onMouseEnter(event)
      }}
      onMouseLeave={(event) => {
        setActive(false)
        if (onMouseLeave) onMouseLeave(event)
      }}
    />
  )
}

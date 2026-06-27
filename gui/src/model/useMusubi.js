import { useEffect, useRef, useState } from 'react'
import { createSource } from '../data/createSource.js'
import { buildViewModel } from './viewModel.js'

// Owns the Tauri DataSource, re-renders on changes, and builds the view-model
// the React views consume.
export function useMusubi(props) {
  const [, force] = useState(0)
  const sourceRef = useRef(null)
  if (!sourceRef.current) sourceRef.current = createSource(props)
  const source = sourceRef.current

  useEffect(() => {
    const off = source.subscribe(() => force((n) => n + 1))
    source.start()
    return () => { off(); source.stop && source.stop() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { source, vals: buildViewModel(source.state, source.actions) }
}

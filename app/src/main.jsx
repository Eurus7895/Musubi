import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Prototype props (live / simSpeed / startView) are overridable via URL query,
// e.g. ?startView=pipeline&simSpeed=Brisk&live=false
const q = new URLSearchParams(location.search)
const props = {
  live: q.get('live') === 'false' ? false : true,
  simSpeed: q.get('simSpeed') || 'Normal',
  startView: q.get('startView') || 'orchestrator',
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App {...props} />
  </React.StrictMode>
)

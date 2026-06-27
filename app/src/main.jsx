import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './index.css'

const q = new URLSearchParams(location.search)
const props = {
  startView: q.get('startView') || 'orchestrator',
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App {...props} />
  </React.StrictMode>
)

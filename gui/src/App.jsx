import { useMusubi } from './model/useMusubi.js'
import ActivityBar from './components/ActivityBar.jsx'
import TrustStrip from './components/TrustStrip.jsx'
import Orchestrator from './views/Orchestrator.jsx'
import Pipeline from './views/Pipeline.jsx'
import Policy from './views/Policy.jsx'
import Audit from './views/Audit.jsx'
import Models from './views/Models.jsx'
import Skills from './views/Skills.jsx'

const VIEWS = {
  orchestrator: Orchestrator,
  pipeline: Pipeline,
  policy: Policy,
  audit: Audit,
  models: Models,
  skills: Skills,
}

export default function App(props) {
  const { vals } = useMusubi(props)
  const View = VIEWS[vals.view] || Orchestrator

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', background: '#0d1117', color: '#e9e9ea', fontFamily: "'IBM Plex Sans',system-ui,sans-serif", fontSize: 13, overflow: 'hidden', letterSpacing: '-0.01em' }}>
      <ActivityBar vals={vals} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <TrustStrip activeModel={vals.activeModel} />
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <View vals={vals} />
        </div>
      </div>
    </div>
  )
}

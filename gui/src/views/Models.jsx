import { cssToObj } from '../lib/css.js'

export default function Models({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ padding: '22px 26px 6px' }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Model router · LMRouter</div>
        <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3, fontFamily: "'IBM Plex Mono',monospace" }}>one inject point — substrate stays zero-LLM · precedence: --vendor → --profile → default → env</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 12, padding: '16px 26px 8px' }}>
        {vals.profiles.map((p) => (
          <div key={p.name} style={cssToObj(p.cardStyle)}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                <span style={cssToObj(p.familyStyle)}>{p.family}</span>
                <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, color: '#cfcfd4' }}>{p.name}</span>
              </div>
              <span style={cssToObj(p.statusStyle)}>{p.statusLabel}</span>
            </div>
            <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 14, color: '#fff', marginBottom: 10 }}>{p.model}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#7a7a82', marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#5a5a62' }}>transport</span><span style={{ color: '#b8b8be' }}>{p.transport}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#5a5a62' }}>endpoint</span><span style={{ color: '#b8b8be' }}>{p.endpoint}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#5a5a62' }}>api_key_env</span><span style={{ color: '#b8b8be' }}>{p.keyEnv}</span></div>
            </div>
            <button onClick={p.onSelect} style={cssToObj(p.btnStyle)}>{p.btnLabel}</button>
          </div>
        ))}
      </div>
      <div style={{ padding: '8px 26px 26px' }}>
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 9 }}>.musubi/llm.toml</div>
        <div style={{ background: '#0f1620', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 10, padding: '16px 18px', fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, lineHeight: 1.7, color: '#b8b8be', overflow: 'auto' }}>
          <span style={{ color: '#5a5a62' }}># grouped by LLM family; [&lt;family&gt;.&lt;name&gt;] profiles inherit family defaults</span><br />
          default = <span style={{ color: '#54c79a' }}>"{vals.activeProfileName}"</span><br /><br />
          [azure]<br />
          &nbsp;&nbsp;base_url = <span style={{ color: '#54c79a' }}>"https://gw.corp.internal/openai"</span>&nbsp;&nbsp;<span style={{ color: '#5a5a62' }}># curl transport · proxy / custom CA / mTLS honoured</span><br />
          &nbsp;&nbsp;api_key_env = <span style={{ color: '#54c79a' }}>"AZURE_API_KEY"</span><br />
          [azure.work]<br />
          &nbsp;&nbsp;model = <span style={{ color: '#54c79a' }}>"gpt-4o"</span>
        </div>
      </div>
    </div>
  )
}

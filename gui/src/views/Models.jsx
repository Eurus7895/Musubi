import { cssToObj } from '../lib/css.js'

export default function Models({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ padding: '22px 26px 6px' }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Model router · LMRouter</div>
        {/* The old subtitle documented a --vendor → --profile chain the README
            retired: --profile is the only endpoint switch, and vendor, model,
            endpoint and key all live in the chosen profile. */}
        <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3, fontFamily: "'IBM Plex Mono',monospace" }}>one inject point — substrate stays zero-LLM · --profile is the only endpoint switch, else `default`</div>
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
      {/* This block used to be labelled `.musubi/llm.toml` and typeset as TOML
          while the product reads `.musubi/llm.json`, and its contents
          (gw.corp.internal, gpt-4o) were invented with only `default` live.
          Showing an operator config they will compare against their real file
          is the fastest way to lose trust in a governance tool. It is now the
          documented schema, labelled as a schema, next to their actual path —
          the file itself is not rendered because a profile may carry an inline
          `api_key`, and the console must not put a secret on screen. */}
      <div style={{ padding: '8px 26px 26px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, marginBottom: 9 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72' }}>.musubi/llm.json · schema</div>
          <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#7d8b9e', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {vals.setupPathHint || 'your file is not rendered — it may hold an inline api_key'}
          </div>
        </div>
        <div style={{ background: '#0f1620', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 10, padding: '16px 18px', fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, lineHeight: 1.7, color: '#b8b8be', overflow: 'auto' }}>
          <span style={{ color: '#5a5a62' }}>// keyed by family; scalars are family defaults, nested objects are profiles</span><br />
          &#123;<br />
          &nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"default"</span>: <span style={{ color: '#54c79a' }}>"{vals.activeProfileName}"</span>,<br />
          &nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"azure"</span>: &#123;<br />
          &nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"transport"</span>: <span style={{ color: '#54c79a' }}>"curl"</span>,&nbsp;&nbsp;<span style={{ color: '#5a5a62' }}>// proxy / custom CA / mTLS honoured</span><br />
          &nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"azure_endpoint"</span>: <span style={{ color: '#54c79a' }}>"https://my-resource.openai.azure.com"</span>,<br />
          &nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"api_key_env"</span>: <span style={{ color: '#54c79a' }}>"AZURE_OPENAI_API_KEY"</span>,<br />
          &nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#8ab4d8' }}>"work"</span>: &#123; <span style={{ color: '#8ab4d8' }}>"deployment"</span>: <span style={{ color: '#54c79a' }}>"gpt-4o"</span> &#125;<br />
          &nbsp;&nbsp;&#125;<br />
          &#125;
        </div>
      </div>
    </div>
  )
}

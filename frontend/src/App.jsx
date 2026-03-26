import { useCallback, useEffect, useState } from 'react'
import GraphView from './components/GraphView.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import { fetchGraph, toForceGraphData } from './services/api.js'

export default function App() {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] })
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphError, setGraphError] = useState(null)
  const [graphMeta, setGraphMeta] = useState(null)
  const [highlightedIds, setHighlightedIds] = useState(() => new Set())

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setGraphLoading(true)
      setGraphError(null)
      try {
        const raw = await fetchGraph({ max_nodes: 8000, max_edges: 100000 })
        if (cancelled) return
        setGraphMeta({
          truncated: raw.truncated,
          stats: raw.stats,
          warnings: raw.warnings,
        })
        setGraphData(toForceGraphData(raw))
      } catch (e) {
        if (!cancelled) setGraphError(e?.message || 'Failed to load graph')
      } finally {
        if (!cancelled) setGraphLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const onQueryComplete = useCallback((idSet) => {
    setHighlightedIds(idSet instanceof Set ? idSet : new Set())
  }, [])

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-white">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 shadow-sm">
        <div>
          <p className="text-xs text-slate-500">
            <span className="text-slate-400">Mapping</span>
            <span className="mx-1.5 text-slate-300">/</span>
            <span className="font-medium text-slate-800">Order to Cash</span>
          </p>
          {graphMeta?.truncated && (
            <p className="text-[11px] text-amber-600">Graph truncated for display — see API limits.</p>
          )}
        </div>
        <span className="text-xs text-slate-500">Backend: 127.0.0.1:8000</span>
      </header>
      <div className="flex min-h-0 flex-1 flex-row">
        <section className="relative z-0 min-h-0 min-w-0 flex-[7] overflow-hidden">
          <GraphView
            graphData={graphData}
            highlightedIds={highlightedIds}
            loading={graphLoading}
            error={graphError}
          />
        </section>
        <aside className="relative z-10 flex min-h-0 min-w-[280px] flex-[3] flex-col bg-white shadow-[inset_1px_0_0_0_rgb(226_232_240)]">
          <ChatPanel onQueryComplete={onQueryComplete} />
        </aside>
      </div>
    </div>
  )
}

import { useCallback, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

const TYPE_COLOR = {
  Order: '#2563eb',
  Delivery: '#ea580c',
  Invoice: '#16a34a',
  Payment: '#dc2626',
  Customer: '#9333ea',
  Product: '#0d9488',
  Address: '#64748b',
}

const HIGHLIGHT_RING = '#fbbf24'
const HIGHLIGHT_FILL = '#fde68a'

export default function GraphView({ graphData, highlightedIds, loading, error }) {
  const fgRef = useRef()
  const [hover, setHover] = useState(null)

  const data = useMemo(() => {
    if (!graphData?.nodes?.length) return { nodes: [], links: [] }
    return graphData
  }, [graphData])

  const highlightSet = useMemo(() => {
    if (!highlightedIds || typeof highlightedIds.forEach !== 'function') return new Set()
    return highlightedIds
  }, [highlightedIds])

  const nodeColor = useCallback(
    (node) => {
      if (highlightSet.has(node.id)) return HIGHLIGHT_FILL
      return TYPE_COLOR[node.type] || '#94a3b8'
    },
    [highlightSet],
  )

  const paintRing = useCallback(
    (node, ctx, globalScale) => {
      if (!highlightSet.has(node.id)) return
      const scale = globalScale || 1
      const r = 10 / scale
      ctx.save()
      ctx.beginPath()
      ctx.arc(node.x, node.y, r + 6 / scale, 0, 2 * Math.PI, false)
      ctx.strokeStyle = HIGHLIGHT_RING
      ctx.lineWidth = 2.5 / scale
      ctx.stroke()
      ctx.restore()
    },
    [highlightSet],
  )

  /** Per-node relative area multiplier (library uses radius = sqrt(val) * nodeRelSize). */
  const nodeVal = useCallback(
    (node) => (highlightSet.has(node.id) ? 2.25 : 1),
    [highlightSet],
  )

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-white text-slate-500">
        <div className="flex items-center gap-3 text-sm">
          <span className="h-5 w-5 animate-spin rounded-full border-2 border-teal-600 border-t-transparent" />
          Loading graph…
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center bg-white p-6 text-center text-sm text-red-600">
        {error}
      </div>
    )
  }

  return (
    <div className="relative h-full min-h-0 w-full bg-white [&_canvas]:max-w-full">
      <ForceGraph2D
        ref={fgRef}
        graphData={data}
        backgroundColor="#ffffff"
        nodeId="id"
        nodeLabel="label"
        linkDirectionalParticles={0}
        linkColor={() => 'rgba(203, 213, 225, 0.9)'}
        linkWidth={1}
        cooldownTicks={120}
        onNodeHover={(node) => {
          setHover(
            node
              ? {
                  id: node.id,
                  type: node.type,
                  meta: node.metadata,
                }
              : null,
          )
        }}
        nodeCanvasObjectMode={() => 'after'}
        nodeCanvasObject={(node, ctx, globalScale) => paintRing(node, ctx, globalScale)}
        nodeVal={nodeVal}
        nodeRelSize={5}
        nodeColor={nodeColor}
      />
      {hover && (
        <div className="pointer-events-none absolute bottom-4 left-4 max-h-[40%] max-w-md overflow-auto rounded-lg border border-slate-200 bg-white/95 p-3 text-xs shadow-lg backdrop-blur-sm">
          <div className="mb-1 font-semibold text-slate-800">{hover.id}</div>
          <div className="mb-2 text-slate-500">{hover.type}</div>
          <pre className="whitespace-pre-wrap break-all text-[11px] text-slate-600">
            {Object.keys(hover.meta || {}).length
              ? JSON.stringify(hover.meta, null, 2)
              : 'No metadata'}
          </pre>
        </div>
      )}
      <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-white/90 px-3 py-2 text-[11px] text-slate-500 shadow-sm backdrop-blur">
        Drag to pan · Scroll to zoom · Hover node for details
      </div>
    </div>
  )
}

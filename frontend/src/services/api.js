/** Default matches your dev API: POST http://127.0.0.1:8000/query */
const BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

/**
 * @returns {Promise<{ nodes: Array, edges: Array, stats?: object, truncated?: boolean, warnings?: string[] }>}
 */
export async function fetchGraph(params = {}) {
  const q = new URLSearchParams()
  if (params.max_nodes != null) q.set('max_nodes', String(params.max_nodes))
  if (params.max_edges != null) q.set('max_edges', String(params.max_edges))
  const url = `${BASE}/graph${q.toString() ? `?${q}` : ''}`
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `Graph request failed (${res.status})`)
  }
  return res.json()
}

/**
 * @returns {Promise<{ answer: string, data: object, trace: object }>}
 */
export async function runQuery(query) {
  const res = await fetch(`${BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body?.detail ?? body?.message ?? res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return body
}

/** Map API payload → force-graph `graphData` */
export function toForceGraphData(api) {
  const nodes = (api.nodes || []).map((n) => ({
    id: n.id,
    type: n.type,
    label: n.label || n.id,
    metadata: n.metadata || {},
  }))
  const links = (api.edges || []).map((e) => ({
    source: e.source,
    target: e.target,
    edgeType: e.type,
  }))
  return { nodes, links }
}

/**
 * Derive node ids to highlight from POST /query response (by intent).
 * @param {object} response - full { answer, data, trace }
 * @returns {Set<string>}
 */
export function extractHighlightedNodeIds(response) {
  const ids = new Set()
  const data = response?.data
  const result = data?.result
  const intent = data?.parse?.intent || response?.trace?.intent
  if (!result || !intent) return ids

  if (intent === 'trace_order_flow') {
    for (const path of result.paths || []) {
      for (const id of path.node_ids || []) {
        if (id) ids.add(id)
      }
    }
    const L = result.lifecycle
    if (L) {
      if (L.order_id) ids.add(L.order_id)
      ;(L.delivery_ids || []).forEach((x) => x && ids.add(x))
      ;(L.invoice_ids || []).forEach((x) => x && ids.add(x))
      ;(L.payment_ids || []).forEach((x) => x && ids.add(x))
      ;(L.product_ids || []).forEach((x) => x && ids.add(x))
      if (L.customer_id) ids.add(L.customer_id)
      ;(L.address_ids || []).forEach((x) => x && ids.add(x))
    }
  } else if (intent === 'find_top_products_by_billing') {
    for (const row of result.rows || []) {
      if (row.material) ids.add(`product:${row.material}`)
    }
  } else if (intent === 'find_incomplete_orders') {
    for (const row of result.rows || []) {
      if (row.order_node_id) ids.add(row.order_node_id)
      const lc = row.lifecycle || {}
      ;(lc.delivery_ids || []).forEach((x) => x && ids.add(x))
      ;(lc.invoice_ids || []).forEach((x) => x && ids.add(x))
      ;(lc.product_ids || []).forEach((x) => x && ids.add(x))
    }
  }

  return ids
}

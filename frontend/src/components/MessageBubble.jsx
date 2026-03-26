import { useMemo, useState } from 'react'

function renderInlineBold(text) {
  if (!text) return null
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, i) => {
    const m = part.match(/^\*\*([^*]+)\*\*$/)
    if (m) {
      return (
        <strong key={i} className="font-semibold text-slate-900">
          {m[1]}
        </strong>
      )
    }
    return <span key={i}>{part}</span>
  })
}

export default function MessageBubble({ role, content, payload, trace, error }) {
  const [open, setOpen] = useState(false)

  const rawPayload = useMemo(() => {
    if (!payload && !trace) return null
    const o = {}
    if (payload != null) o.data = payload
    if (trace != null) o.trace = trace
    return o
  }, [payload, trace])

  if (role === 'user') {
    return (
      <div className="flex justify-end gap-2">
        <div className="max-w-[min(92%,28rem)] rounded-2xl rounded-br-md bg-slate-800 px-4 py-2.5 text-sm text-white shadow-sm">
          <p className="whitespace-pre-wrap leading-relaxed">{content}</p>
        </div>
        <div
          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-300 text-xs font-medium text-slate-700"
          aria-hidden
        >
          You
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start gap-2">
      <div
        className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white"
        aria-hidden
      >
        D
      </div>
      <div className="max-w-[min(95%,32rem)] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800 shadow-sm">
        <div className="mb-1.5 leading-none">
          <span className="text-sm font-semibold text-slate-900">Dodge AI</span>
          <span className="mt-0.5 block text-[11px] text-slate-500">Graph Agent</span>
        </div>
        {error ? (
          <p className="font-medium text-red-600">{error}</p>
        ) : (
          <>
            <p className="whitespace-pre-wrap leading-relaxed text-slate-800">{renderInlineBold(content)}</p>
            {rawPayload && (
              <div className="mt-3 border-t border-slate-100 pt-2">
                <button
                  type="button"
                  onClick={() => setOpen(!open)}
                  className="text-xs font-medium text-slate-700 underline-offset-2 hover:text-slate-900 hover:underline"
                >
                  {open ? 'Hide raw data' : 'Show raw data'}
                </button>
                {open && (
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-50 p-2 text-[11px] leading-snug text-slate-600">
                    {JSON.stringify(rawPayload, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

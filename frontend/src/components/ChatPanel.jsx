import { useCallback, useRef, useState } from 'react'
import MessageBubble from './MessageBubble.jsx'
import { extractHighlightedNodeIds, runQuery } from '../services/api.js'

export default function ChatPanel({ onQueryComplete }) {
  const [messages, setMessages] = useState([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        'Hi! I can help you analyze the **Order to Cash** process.',
      payload: null,
      trace: null,
      error: null,
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const endRef = useRef(null)

  const scrollToBottom = () => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMessages((m) => [
      ...m,
      { id: `u-${Date.now()}`, role: 'user', content: text, payload: null, trace: null, error: null },
    ])
    setLoading(true)
    try {
      const res = await runQuery(text)
      const highlight = extractHighlightedNodeIds(res)
      onQueryComplete?.(highlight, res)
      setMessages((m) => [
        ...m,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: res.answer ?? '(no answer)',
          payload: res.data ?? null,
          trace: res.trace ?? null,
          error: null,
        },
      ])
    } catch (e) {
      onQueryComplete?.(new Set(), null)
      setMessages((m) => [
        ...m,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: '',
          payload: null,
          trace: null,
          error: e?.message || 'Request failed',
        },
      ])
    } finally {
      setLoading(false)
      setTimeout(scrollToBottom, 50)
    }
  }, [input, loading, onQueryComplete])

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50/80">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-900">Chat with Graph</h2>
        <p className="text-xs text-slate-500">Order to Cash</p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <div className="flex flex-col gap-3">
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              payload={msg.payload}
              trace={msg.trace}
              error={msg.error}
            />
          ))}
          {loading && (
            <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 shadow-sm">
              <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-slate-400 border-t-slate-800" />
              <span>Running query…</span>
            </div>
          )}
          <div ref={endRef} className="h-px shrink-0" aria-hidden />
        </div>
      </div>

      <div className="shrink-0 border-t border-slate-200 bg-white p-3">
        <div
          className={`mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${
            loading ? 'bg-slate-100 text-slate-600' : 'bg-slate-50 text-slate-600'
          }`}
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${loading ? 'animate-pulse bg-amber-500' : 'bg-emerald-500'}`}
            aria-hidden
          />
          <span>{loading ? 'Working on your question…' : 'Dodge AI is awaiting instructions.'}</span>
        </div>
        <div className="flex gap-2">
          <textarea
            rows={3}
            value={input}
            disabled={loading}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder="Analyze anything"
            className="min-h-[72px] flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <button
            type="button"
            disabled={loading || !input.trim()}
            onClick={send}
            className="self-end rounded-xl bg-slate-700 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}

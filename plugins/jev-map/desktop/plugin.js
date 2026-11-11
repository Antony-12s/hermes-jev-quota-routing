/**
 * Jev map side pane. Reads the backend's /map route (GET /api/plugins/jev-map/map).
 * Plain ESM, uncompiled: UI is jsx() calls, imports limited to the SDK, react, react/jsx-runtime.
 */

import { usePluginI18n } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'jev-map'
const POLL_MS = 1000

// Mode -> colour token, so the badge reads at a glance.
const MODE_TONE = {
  off: 'text-(--ui-text-quaternary)',
  shadow: 'text-(--ui-cyan)',
  auto: 'text-(--ui-orange)',
  notify: 'text-(--ui-yellow)',
  confirm: 'text-(--ui-yellow)'
}

const LABEL_TONE = {
  fast: 'text-(--ui-green)',
  deep: 'text-(--ui-purple)',
  xpremium: 'text-(--ui-purple)'
}

// Short fade-up as cards appear. Disabled for reduced-motion users.
const STYLE = `
@keyframes jev-map-in { from { opacity: 0; transform: translateY(4px) } to { opacity: 1; transform: none } }
[data-jev-map] .jev-map-card { animation: jev-map-in 220ms ease-out both }
[data-jev-map] .jev-map-card:nth-child(2) { animation-delay: 40ms }
[data-jev-map] .jev-map-card:nth-child(3) { animation-delay: 80ms }
[data-jev-map] .jev-map-card:nth-child(4) { animation-delay: 120ms }
[data-jev-map] .jev-map-row { transition: background-color 150ms ease }
[data-jev-map] .jev-map-row:hover { background-color: var(--chrome-action-hover) }
@media (prefers-reduced-motion: reduce) { [data-jev-map] * { animation: none !important; transition: none !important } }
`

function Card({ title, children }) {
  return jsxs('section', {
    className: 'jev-map-card rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) p-3',
    children: [
      jsx('div', {
        className: 'mb-2 text-[0.6875rem] uppercase tracking-wide text-(--ui-text-tertiary)',
        children: title
      }),
      children
    ]
  })
}

function Pill({ children, tone = '' }) {
  return jsx('span', {
    className: `inline-flex items-center rounded-full border border-(--ui-stroke-tertiary) px-2 py-0.5 text-[0.6875rem] font-medium ${tone}`,
    children
  })
}

function Row({ left, sub, right }) {
  return jsxs('div', {
    className: 'jev-map-row flex items-center justify-between gap-2 rounded-md px-1.5 py-1',
    children: [
      jsxs('div', {
        className: 'min-w-0',
        children: [
          jsx('div', { className: 'truncate text-xs text-(--ui-text-primary)', children: left }),
          sub ? jsx('div', { className: 'truncate text-[0.6875rem] text-(--ui-text-tertiary)', children: sub }) : null
        ]
      }),
      right ? jsx('div', { className: 'shrink-0 text-xs', children: right }) : null
    ]
  })
}

function targetOf(entries) {
  const first = Array.isArray(entries) && entries[0]
  if (!first) return { model: '(empty)', sub: '', extra: 0 }
  return {
    model: first.model || '?',
    sub: `${first.provider || '?'} · ${first.thinkingLevel || first.reasoning_effort || '-'}`,
    extra: Math.max(0, entries.length - 1)
  }
}

function targetRows(mapping) {
  return Object.entries(mapping).map(([name, entries]) => {
    const target = targetOf(entries)
    return jsx(Row, {
      key: name,
      left: name,
      sub: target.sub,
      right: jsxs('span', {
        className: 'text-(--ui-text-secondary)',
        children: [
          target.model,
          target.extra ? jsx('span', { className: 'ml-1 text-(--ui-text-quaternary)', children: `+${target.extra}` }) : null
        ]
      })
    })
  })
}

function clock(iso) {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function StatusDot({ state }) {
  const tone = { ok: 'bg-(--ui-green)', loading: 'bg-(--ui-yellow)', error: 'bg-(--ui-orange)' }[state]
  return jsx('span', { className: `inline-block size-1.5 rounded-full ${tone}`, 'aria-hidden': true })
}

function MapPane({ rest }) {
  const t = usePluginI18n(ID)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    // Self-rescheduling tick: the next request starts only after the previous one settles,
    // so a slow reply never stacks up behind a newer one.
    let alive = true
    let timer = null
    const schedule = () => {
      if (alive) timer = setTimeout(tick, POLL_MS)
    }
    const tick = () => {
      Promise.resolve()
        .then(() => rest('map', { timeoutMs: 15000 }))
        .then(body => alive && (setData(body), setError(null)))
        .catch(err => alive && setError(String(err?.message || err)))
        .finally(schedule)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [rest])

  const failed = Boolean(error) || (data && !data.ok)
  const state = failed ? 'error' : data ? 'ok' : 'loading'
  const header = jsxs('div', {
    className: 'flex items-center justify-between',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [jsx(StatusDot, { state }), jsx('span', { className: 'text-sm font-semibold', children: t('title') })]
      }),
      data?.updatedAt ? jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: clock(data.updatedAt) }) : null
    ]
  })

  let body
  if (failed) {
    body = jsx(Card, {
      title: t('loadError'),
      children: jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: error || data.error })
    })
  } else if (!data) {
    body = jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: t('loading') })
  } else {
    const KIND = {
      main: { label: 'main', tone: 'text-(--ui-accent)' },
      review: { label: 'review', tone: 'text-(--ui-yellow)' },
      sub: { label: 'sub', tone: 'text-(--ui-green)' },
      'sub-start': { label: 'sub ▶', tone: 'text-(--ui-green)' },
      'sub-done': { label: 'sub ✓', tone: 'text-(--ui-green)' }
    }
    const live = (data.live || []).map((row, index) => {
      const kind = row.kind || 'main'
      const isSub = kind.startsWith('sub')
      const known = KIND[kind] || { label: kind, tone: 'text-(--ui-text-quaternary)' }
      const left = kind === 'main' ? row.prompt || '(no message)'
        : kind === 'review' ? 'background review (fork)'
        : isSub ? `↳ ${row.goal || row.parent_turn || ''}`
        : kind
      const duration = row.duration_ms != null ? `${Math.round(row.duration_ms / 100) / 10}s` : row.seconds != null ? `${row.seconds}s` : ''
      const meta = [clock(row.ts), row.session ? `#${row.session}` : '', kind === 'main' && row.step ? `step ${row.step}` : '', row.effort ? `effort ${row.effort}` : '', row.status || '', duration, row.tokens ? `${row.tokens} tok` : '']
      const tail = kind === 'main' ? (row.tiers || []).join(' / ') || 'no matching tier' : row.provider || ''
      return jsx(Row, {
        key: `live-${index}`,
        left,
        sub: `${meta.filter(Boolean).join(' · ')}${tail ? ` · ${tail}` : ''}`,
        right: jsxs('span', {
          className: 'flex items-center gap-1',
          children: [
            jsx(Pill, { tone: known.tone, children: known.label }),
            jsx('span', { className: 'text-(--ui-text-secondary)', children: row.model || '' })
          ]
        })
      })
    })
    const picks = data.audit.map((row, index) =>
      jsx(Row, {
        key: index,
        left: `${clock(row.timestamp)} · ${row.model || '?'}`,
        sub: row.provider || '',
        right: jsx(Pill, { tone: LABEL_TONE[row.label] || '', children: row.label || '-' })
      })
    )
    body = jsxs('div', {
      className: 'flex flex-col gap-2',
      children: [
        jsx(Card, {
          title: t('live'),
          children: live.length
            ? jsx('div', { className: 'flex flex-col', children: live })
            : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: t('noLive') })
        }),
        jsx(Card, {
          title: t('mode'),
          children: jsxs('div', {
            className: 'flex flex-col gap-1',
            children: [
              jsx(Pill, { tone: MODE_TONE[data.mode] || '', children: data.mode }),
              jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('modeHint', data.modeSource) })
            ]
          })
        }),
        jsx(Card, { title: t('tiers'), children: jsx('div', { className: 'flex flex-col', children: targetRows(data.routes) }) }),
        jsx(Card, { title: t('kinds'), children: jsx('div', { className: 'flex flex-col', children: targetRows(data.kindModels) }) }),
        jsx(Card, {
          title: t('recent'),
          children: picks.length
            ? jsx('div', { className: 'flex flex-col', children: picks })
            : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: t('noPicks') })
        })
      ]
    })
  }

  return jsxs('div', {
    'data-jev-map': '',
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-3',
    children: [jsx('style', { children: STYLE }), header, body]
  })
}

export default {
  id: ID,
  name: 'Jev Map',
  register(ctx) {
    ctx.i18n.register({
      en: {
        title: 'Jev routing',
        loading: 'loading…',
        loadError: 'could not load',
        mode: 'Effective mode',
        modeHint: source => `from ${source} · shadow only logs, auto switches the model`,
        tiers: 'Tiers',
        kinds: 'Kind models',
        recent: 'Recent picks',
        live: 'Live · command → model',
        noLive: 'no model calls yet',
        noPicks: 'no picks yet'
      }
    })

    const rest = ctx.rest
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Jev map',
      data: { placement: 'right', width: '320px' },
      render: () => jsx(MapPane, { rest })
    })
  }
}

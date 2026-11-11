/**
 * The Jev line renders in the composer underside strip.
 *
 * The listing edition uses the sanctioned composer contribution area: one line
 * under the input for the turn that was just routed, plus the approve row when
 * the weight wants another model. Nothing here reads or writes app-owned nodes,
 * so the desktop surface check stays inside the plugin SDK.
 */
import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

const EVENT = 'plugin.hermes-jev-routing.routed'
const TOGGLE_EVENT = 'plugin.hermes-jev-routing.toggle'

let current = null
let pendingAuto = null
const listeners = new Set()

function publish(next) {
  current = next
  listeners.forEach((fn) => fn())
}

function payloadOf(frame) {
  if (!frame || typeof frame !== 'object') return null
  return frame.payload && typeof frame.payload === 'object' ? frame.payload : frame
}

function runtimeSessionId(payload) {
  const focused = host.state && host.state.focusedSessionId && typeof host.state.focusedSessionId.get === 'function'
    ? host.state.focusedSessionId.get()
    : ''
  const stored = host.state?.focusedStoredSessionId?.get?.() || ''
  return payload.session_id && (payload.session_id === focused || payload.session_id === stored)
    ? focused
    : ''
}

function modelSet(payload, pick) {
  const sessionId = runtimeSessionId(payload)
  if (!sessionId || !pick) return null
  return {
    session_id: sessionId,
    key: 'model',
    // --session scopes the switch to this session; without it config.yaml is written globally.
    value: `${pick.model} --provider ${pick.provider} --session`,
  }
}

// The rest of a pick, once the session is on the accepted model.
async function settle(payload, pick) {
  const sessionId = runtimeSessionId(payload)
  if (!sessionId || !pick) return
  if (pick.thinking) {
    await host.request('config.set', {
      session_id: sessionId,
      key: 'reasoning',
      value: String(pick.thinking),
    })
  }
  // A held turn was interrupted before it sent, so the approved prompt still
  // owes an answer. Resubmit it once the session is on the accepted model.
  const owed = payload.owed_prompt
  if (owed && typeof host.composer?.submit === 'function') {
    host.composer.submit(sessionId, owed)
  }
}

function guardMessage(result, pick) {
  const text = result && typeof result.confirm_message === 'string' ? result.confirm_message : ''
  return text || `Confirm ${pick.model}?`
}

// Core guards an expensive, data-policy, or large-context model. Answering that
// guard here would approve it for the user, so leave the session on its current
// model and let the strip ask. `auto` is the payload.apply path, which has no
// click behind it: surface the message and never send the confirmation.
async function applyChoice(payload, pick, auto) {
  const body = modelSet(payload, pick)
  if (!body) return
  if (auto && host.state?.busyBySession?.get?.()[body.session_id]) {
    // Never rebind the provider while an existing request is in flight.
    pendingAuto = { payload, pick }
    return
  }
  const result = await host.request('config.set', body)
  if (result && result.confirm_required) {
    const message = guardMessage(result, pick)
    publish({ ...payload, interrupt: false, confirm: auto ? { message } : { message, pick } })
    return
  }
  await settle(payload, pick)
}

async function confirmPick(payload) {
  const pending = payload.confirm
  if (!pending || !pending.pick) return
  const body = modelSet(payload, pending.pick)
  if (!body) return
  const result = await host.request('config.set', { ...body, confirm_expensive_model: true })
  if (result && result.confirm_required) {
    // A second guard question stays with the user rather than being answered.
    publish({ ...payload, interrupt: false, confirm: { message: guardMessage(result, pending.pick), pick: pending.pick } })
    return
  }
  publish({ ...payload, interrupt: false, confirm: null })
  await settle(payload, pending.pick)
}

// Quota fallback: an offer auto-accepts at its deadline unless the user answers first.
let quotaTimer = null

function clearQuotaTimer() {
  if (quotaTimer) clearTimeout(quotaTimer)
  quotaTimer = null
}

function acceptQuota(payload) {
  clearQuotaTimer()
  const quota = payload.quota
  if (!quota || !quota.pick) return
  publish({ ...payload, quota: null, line: `quota · switching to ${quota.pick.model}` })
  applyChoice(payload, quota.pick, true)
}

function stayOnClaude(payload) {
  clearQuotaTimer()
  publish({ ...payload, quota: null, line: 'quota · staying on Claude' })
  const sid = runtimeSessionId(payload)
  if (sid) runJevCommand(sid, 'stay')
}

// Jev on/off per session. Off leaves the user's picked model alone; quota fallback still runs.
// Local state mirrors the Python slot; the Python side re-publishes it every turn.
const jevToggles = {}
const toggleListeners = new Set()

function jevEnabled(sid) {
  return jevToggles[sid] !== false
}

function setJevToggle(sid, enabled) {
  jevToggles[sid] = enabled
  toggleListeners.forEach((fn) => fn())
}

// Routes through the slash worker, so the command never reaches the model as a chat message.
function runJevCommand(sid, command) {
  return host.request('slash.exec', { session_id: sid, command: `/jev-routing ${command}` })
}

async function toggleJev(sid) {
  const next = !jevEnabled(sid)
  setJevToggle(sid, next)
  try {
    await runJevCommand(sid, next ? 'on' : 'off')
  } catch {
    setJevToggle(sid, !next)
  }
}

function JevToggle() {
  const [, rerender] = useState(0)
  const focused = host.state?.focusedSessionId?.get?.() || host.state?.focusedStoredSessionId?.get?.() || ''

  useEffect(() => {
    const sync = () => rerender((n) => n + 1)
    toggleListeners.add(sync)
    const offFocus = host.state?.focusedSessionId?.listen?.(sync)
    return () => {
      toggleListeners.delete(sync)
      if (typeof offFocus === 'function') offFocus()
    }
  }, [])

  if (!focused) return null
  const on = jevEnabled(focused)
  return jsx(Choice, {
    label: on ? 'Jev on' : 'Jev off',
    bold: on,
    onClick: () => toggleJev(focused),
  })
}

function handleQuota(payload) {
  const quota = payload.quota
  clearQuotaTimer()
  if (quota.kind === 'switch') {
    acceptQuota(payload)
    return
  }
  if (quota.kind === 'offer') {
    const wait = Math.max(0, Number(quota.deadline_ms || 0) - Date.now())
    quotaTimer = setTimeout(() => acceptQuota(payload), wait)
  }
}

function QuotaCountdown({ deadline }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const left = Math.max(0, Math.ceil((Number(deadline || 0) - now) / 1000))
  return jsx('span', { children: ` (auto in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}) ` })
}

function cancelPick(payload) {
  publish({ ...payload, interrupt: false, confirm: null })
}

const WRAP = {
  fontSize: '0.6875rem',
  lineHeight: '1.25rem',
  color: 'color-mix(in oklab, var(--ui-text-tertiary, var(--foreground)) 70%, transparent)',
  padding: '0 0.5rem',
}

const ROW = { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline' }

function choiceStyle(bold) {
  return {
    font: 'inherit',
    lineHeight: 'inherit',
    padding: 0,
    border: 0,
    background: 'transparent',
    cursor: 'pointer',
    color: bold ? 'var(--ui-text-primary, var(--foreground))' : 'inherit',
    fontWeight: bold ? 600 : 400,
  }
}

function Choice({ label, bold, onClick }) {
  return jsx('button', { type: 'button', onClick, style: choiceStyle(bold), children: label })
}

function JevLine() {
  const [payload, setPayload] = useState(current)

  useEffect(() => {
    const sync = () => setPayload(current)
    listeners.add(sync)
    sync()
    return () => {
      listeners.delete(sync)
    }
  }, [])

  if (!payload || typeof payload.line !== 'string' || !payload.line) return null

  const dismiss = () => publish({ ...payload, interrupt: false })
  const pick = (target) => {
    dismiss()
    applyChoice(payload, target)
  }
  const offered = payload.offered_tier || 'premium'
  const heads = payload.heads || {}
  const kinds = payload.kinds || {}
  const kindNames = Object.keys(kinds)
  const otherTiers = Object.keys(heads).filter((tier) => tier !== offered)

  const row = [
    jsx(Choice, { label: 'Approve:', bold: true, onClick: dismiss }, 'approve'),
    jsx('span', { children: ' ' }, 'gap-one'),
    jsx(Choice, {
      label: `Yes, ${offered}`,
      onClick: () => pick({ provider: payload.provider, model: payload.model, thinking: payload.thinking }),
    }, 'yes'),
  ]
  row.push(jsx('span', { children: ' \u00b7 ' }, 'gap-two'))
  row.push(jsx(Choice, { label: 'No', onClick: dismiss }, 'no'))
  if (payload.free) {
    row.push(jsx('span', { children: ' \u00b7 ' }, 'gap-free'))
    row.push(jsx(Choice, { label: 'Free', onClick: () => pick(payload.free) }, 'free'))
  }
  otherTiers.forEach((tier, index) => {
    row.push(jsx('span', { children: ' \u00b7 ' }, `gap-tier-${index}`))
    row.push(jsx(Choice, { label: tier, onClick: () => pick(heads[tier]) }, `tier-${tier}`))
  })

  if (kindNames.length) {
    const kindRow = []
    kindNames.forEach((kind) => {
      kindRow.push(jsx(Choice, { label: kind, onClick: () => pick(kinds[kind]) }, `kind-${kind}`))
      kindRow.push(jsx('span', { children: ' ' }, `kind-gap-${kind}`))
    })
    kindRow.push(jsx(Choice, { label: 'X', onClick: dismiss }, 'x-kind'))
    row.push(jsx('span', { style: { flexBasis: '100%' }, children: kindRow }, 'kinds'))
  } else {
    row.push(jsx('span', { children: ' \u00b7 ' }, 'gap-x'))
    row.push(jsx(Choice, { label: 'X', onClick: dismiss }, 'x'))
  }

  const quota = payload.quota && payload.quota.kind === 'offer' ? payload.quota : null
  const quotaRow = quota
    ? [
        jsx(Choice, { label: 'Switch to Codex now', bold: true, onClick: () => acceptQuota(payload) }, 'q-yes'),
        jsx(QuotaCountdown, { deadline: quota.deadline_ms }, 'q-count'),
        jsx('span', { children: ' \u00b7 ' }, 'q-gap'),
        jsx(Choice, { label: 'Stay on Claude', onClick: () => stayOnClaude(payload) }, 'q-no'),
      ]
    : null

  const confirm = payload.confirm && typeof payload.confirm.message === 'string' ? payload.confirm : null
  const confirmRow = []
  if (confirm) {
    confirmRow.push(jsx('span', { children: `${confirm.message} ` }, 'confirm-message'))
    if (confirm.pick) {
      confirmRow.push(jsx(Choice, {
        label: `Confirm ${confirm.pick.model}`,
        bold: true,
        onClick: () => confirmPick(payload),
      }, 'confirm-yes'))
      confirmRow.push(jsx('span', { children: ' \u00b7 ' }, 'confirm-gap'))
      confirmRow.push(jsx(Choice, { label: 'Cancel', onClick: () => cancelPick(payload) }, 'confirm-no'))
    }
  }

  return jsxs('div', {
    style: { ...WRAP, whiteSpace: 'normal' },
    children: [
      jsx('div', { style: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }, children: payload.line }, 'line'),
      quotaRow ? jsx('div', { style: ROW, children: quotaRow }, 'quota') : null,
      payload.interrupt ? jsx('div', { style: ROW, children: row }, 'row') : null,
      confirm ? jsx('div', { style: ROW, children: confirmRow }, 'confirm') : null,
    ],
  })
}

export default {
  id: 'hermes-jev-routing',
  name: 'Jev Routing',
  description: 'Shows the Jev line for the turn that was just routed, under the composer.',

  register(ctx) {
    publish(null)
    pendingAuto = null
    const offBusy = host.state?.busyBySession?.listen?.(() => {
      if (!pendingAuto) return
      const { payload, pick } = pendingAuto
      const sid = runtimeSessionId(payload)
      if (sid && host.state.busyBySession.get()[sid]) return
      pendingAuto = null
      if (sid) applyChoice(payload, pick, true)
    })

    ctx.register({
      id: 'jev-toggle',
      area: COMPOSER_AREAS.actions,
      order: 5,
      render: () => jsx(JevToggle, {}),
    })

    ctx.register({
      id: 'jev-line',
      area: COMPOSER_AREAS.underside,
      order: 10,
      render: () => jsx(JevLine, {}),
    })

    const off = typeof ctx.onEvent === 'function'
      ? ctx.onEvent(EVENT, (frame) => {
          const payload = payloadOf(frame)
          if (!payload || typeof payload.line !== 'string') return
          if (!runtimeSessionId(payload)) return
          publish(payload)
          if (payload.quota) {
            handleQuota(payload)
            return
          }
          if (payload.apply) {
            // The automatic path has no click behind it, so it may apply a pick
            // but it never answers core's model guard (third argument).
            applyChoice(payload, { provider: payload.provider, model: payload.model, thinking: payload.thinking }, true)
          }
        })
      : null

    const offToggle = typeof ctx.onEvent === 'function'
      ? ctx.onEvent(TOGGLE_EVENT, (frame) => {
          const payload = payloadOf(frame)
          if (payload && payload.session_id) setJevToggle(payload.session_id, payload.enabled !== false)
        })
      : null

    ctx.onDispose(() => {
      if (typeof off === 'function') off()
      if (typeof offToggle === 'function') offToggle()
      toggleListeners.clear()
      if (typeof offBusy === 'function') offBusy()
      pendingAuto = null
      clearQuotaTimer()
      listeners.clear()
      current = null
    })
  },
}

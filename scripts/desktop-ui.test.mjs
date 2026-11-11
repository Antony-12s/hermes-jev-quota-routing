// Drive the actual bundled plugin modules with SDK/React boundary stubs.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const jsx = (type, props) => ({ type, props })
function expand(node) {
  if (Array.isArray(node)) return node.map(expand)
  if (!node || typeof node !== 'object') return node
  if (typeof node.type === 'function') return expand(node.type(node.props || {}))
  return { ...node, props: { ...node.props, children: expand(node.props?.children) } }
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join(' ')
  if (node && typeof node === 'object') return text(node.props?.children)
  return node == null ? '' : String(node)
}
function load(id, globals = {}) {
  const contributions = []
  const code = readFileSync(join(root, 'plugins', id, 'desktop/plugin.js'), 'utf8')
    .split('\n').filter(line => !/^import\s/.test(line)).join('\n')
    .replace('export default', 'globalThis.plugin =')
  const scope = {
    jsx, jsxs: jsx, COMPOSER_AREAS: { actions: 'composer.actions', underside: 'composer.underside' },
    useState: value => [value, () => {}], useEffect: () => {},
    ...globals,
  }
  runInNewContext(code, scope)
  let strings = {}
  scope.plugin.register({
    register: item => contributions.push(item), onDispose: () => {}, onEvent: () => () => {},
    i18n: { register: languages => { strings = languages.en } }, rest: () => Promise.resolve({}),
  })
  return { contributions, strings }
}
function toggle(reject = false) {
  const calls = []
  const host = {
    state: { focusedSessionId: { get: () => 'sample-session' } },
    request: async (method, payload) => {
      calls.push({ method, payload })
      if (reject) throw new Error('Test RPC failure')
      return {}
    },
  }
  const loaded = load('hermes-jev-routing', { host })
  const contribution = loaded.contributions.find(item => item.id === 'jev-toggle')
  assert.equal(contribution.area, 'composer.actions')
  return { calls, render: () => expand(contribution.render()) }
}

test('bundled composer button switches on/off through session-scoped slash RPC', async () => {
  const fixture = toggle()
  assert.equal(text(fixture.render()), 'Jev on')
  fixture.render().props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(text(fixture.render()), 'Jev off')
  assert.equal(fixture.calls[0].method, 'slash.exec')
  assert.equal(fixture.calls[0].payload.command, '/jev-routing off')
  assert.equal(fixture.calls[0].payload.session_id, 'sample-session')
  fixture.render().props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(text(fixture.render()), 'Jev on')
})

test('bundled composer button rolls back when RPC fails', async () => {
  const fixture = toggle(true)
  fixture.render().props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(text(fixture.render()), 'Jev on')
})

test('bundled Jev map registers a right-side pane and renders real module output', () => {
  let strings
  let stateIndex = 0
  const fixture = { ok: true, mode: 'auto', modeSource: 'config.yaml', routes: {}, kindModels: {}, audit: [],
    live: [{ kind: 'main', prompt: 'Sample task', model: 'claude-opus-5-5', step: 1, effort: 'high' }] }
  const loaded = load('jev-map', {
    usePluginI18n: () => key => strings[key],
    useState: initial => [stateIndex++ === 0 ? fixture : initial, () => {}],
  })
  strings = loaded.strings
  const pane = loaded.contributions.find(item => item.id === 'pane')
  assert.equal(pane.area, 'panes')
  assert.equal(pane.data.placement, 'right')
  const output = text(expand(pane.render()))
  assert.match(output, /Jev routing/)
  assert.match(output, /Live/)
  assert.match(output, /Sample task/)
  assert.match(output, /claude-opus-5-5/)
  assert.match(output, /step 1/)
})

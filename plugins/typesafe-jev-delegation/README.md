# typesafe-jev-delegation

Delegation backend `jev_routes`: TypeSafe Jev picks one configured route per `delegate_task` subagent.
Not enabled by installing it. Design notes: [DECISIONS.md](DECISIONS.md).

```yaml
delegation:
  backend: jev_routes          # opt in
  backends:
    jev_routes:
      timeout_s: 3             # optional, (0, 10]
      options:
        - {name: fast, description: "Lookups and small edits", provider: openrouter, model: some/fast-model, reasoning_effort: low}
        - {name: deep, description: "Hard debugging and design", provider: anthropic, model: some-deep-model, reasoning_effort: high}
```

Needs `TYPESAFE_API_KEY` in the environment; without it every task takes the default route.

Tests (offline): `python3 -m unittest discover -s tests -v` (set `HERMES_AGENT_ROOT` if core is not at
`~/.hermes/hermes-agent`). Live check (real network): `scripts/live_check.py`.

# hermes-jev-quota-routing

ให้ [Jev (TypeSafe)](https://typesafe.ai) เลือกโมเดลกับ effort ให้ [Hermes Agent](https://hermes-agent.nousresearch.com) เอง ทั้งแชทหลักและ sub-agent ด้วยกติกาเดียวกัน พร้อมตัวเฝ้า quota ที่เตือนก่อน แล้วสลับ Claude ไป Codex ให้อัตโนมัติ

Lets TypeSafe Jev pick the model and effort for Hermes Agent, in the main chat and in subagents, under one rule set, with a quota guard that warns first and then moves from Claude to Codex.

![routing map](docs/routing-map.png)

## Routes

| Work | Main chat | Subagent |
|---|---|---|
| Quick question, lookup | Opus 5.5 low | Opus 5.5 low |
| Normal coding | Opus 5.5 medium | Opus 5.5 medium |
| Hard debugging, planning | Opus 5.5 high | Opus 5.5 high |
| Beyond Opus 5.5 (long refactor, hard proof) | Fable 5.2 high | Fable 5.2 high |
| Implement, fix a test, security fix | stays on Claude | Codex gpt-6.1-sol |
| Art and visual work (UI, logo, SVG) | stays on Claude | Codex gpt-6.1-sol |
| Research, fact-finding | stays on Claude | Gemini 3.8 Flash (agy) |

- The main chat cannot change provider in the middle of a session, so it stays on Claude. Subagents each choose their own provider.
- Jev unsure (confidence under 0.65): the model you picked is kept.
- The Desktop gets a **Jev on / Jev off** button next to Send. Off = your picked model is used exactly as-is.

## Quota guard (always on, button on or off)

1. Claude at 10% or less: warning plus a 2-minute countdown in the Desktop.
2. You press switch, or the countdown runs out: the session moves to Codex. "Stay on Claude" keeps it.
3. Codex too low as well: Hermes tells you to wait, with the earliest reset time.
4. Claude back at 25% or more: back to Claude.

Without the Desktop app (CLI, messaging) it warns and asks you to wait; it never switches on its own.

## Install

Needs: Hermes Agent, a Claude subscription logged in to Hermes, and a TypeSafe API key.

```bash
git clone https://github.com/Antony-12s/hermes-jev-quota-routing
cd hermes-jev-quota-routing
./install.sh
```

The installer:

- copies both plugins into `~/.hermes/plugins` (`HERMES_HOME` respected; old copies go to `~/.hermes/backups/`)
- writes `~/.hermes/jev-routing/main.json`, or keeps yours and saves the new one as `main.json.example`
- enables both plugins and sets `delegation.backend: jev_routes`
- adds the Codex routes only if you have an `openai-codex` login, and the Gemini route only if the `antigravity-oauth` plugin is enabled

Then:

1. Add `TYPESAFE_API_KEY=...` to `~/.hermes/.env`.
2. Optional, for the quota guard: `hermes plugins install rarf/hermes-quota-plugin --enable`. Without it the guard has no data and never blocks.
3. Restart the Desktop app, open Settings > Plugins and turn on **hermes-jev-routing**. Desktop plugins from outside the catalog start switched off.
4. Open a **new chat**. A running chat keeps the subagent routing it started with.

## What is in here

| Path | What |
|---|---|
| `plugins/hermes-jev-routing/` | Main-chat routing, Desktop button, quota switch. Fork of Apostol Apostolov's plugin (MIT, see its `LICENSE`). |
| `plugins/typesafe-jev-delegation/` | Subagent backend `jev_routes` and the shared quota rule `quota_gate.py`. Design notes in `DECISIONS.md`. |
| `examples/main.json` | Main-chat routes. |
| `scripts/delegation_options.py` | Builds the subagent routes for the installer. |
| `docs/routing-map.html` | The diagram above. |

### Changes to the upstream hermes-jev-routing

- `quota_switch.py`: quota fallback for the main chat (warn, countdown, Codex, wait).
- `jev_toggle.py` and `desktop/plugin.js`: per-session Jev on/off button; quota countdown row.
- `pre_tool_call` hook: asks the model to wrap up when quota hits the floor mid-turn. This is a request to the model, not a guaranteed hard stop.

## Tests

```bash
cd plugins/hermes-jev-routing && python3 -m unittest discover -s tests -q && node --test tests/*.test.mjs
cd ../typesafe-jev-delegation && python3 -m unittest discover -s tests -q
cd ../../scripts && python3 -m unittest -q test_delegation_options
```

Run them with the Python from your Hermes install if imports fail.

## Privacy

Jev receives the current message, a short excerpt, a context-size estimate and a spend snapshot (see `plugins/hermes-jev-routing/README.md`). Goals that look like they contain a secret are not sent for subagent routing.

## License

MIT. `plugins/hermes-jev-routing` keeps its own MIT notice from Apostol Apostolov.

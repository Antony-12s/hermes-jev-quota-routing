# DECISIONS

1. **Standalone, no advanced-delegation import.** The option validation is copied in small form
   (`parse_options`). Only `provider`, `model`, `reasoning_effort` are copied into a route; `api_key`,
   `base_url`, `api_mode`, `service_tier` and anything else in an option are ignored, so credentials
   resolve exactly as core does for `delegation.provider`.
2. **Strict config, all-or-nothing.** One bad option (missing field, bad name, duplicate, unknown
   effort, >16 options) or bad `timeout_s` (not a number in (0, 10]) disables Jev for that call:
   default route, no network. Partial acceptance would silently change which labels Jev can pick.
3. **Fallback = the default backend's route** (`config` keys in core `ROUTE_KEYS` minus
   `service_tier`), so provider/model/effort/base_url/api_key/fallback behaviour is unchanged.
4. **Inert schema.** `task_parameters` `{}` and `description` `''`: the parent model cannot steer the
   route and the cached tool prefix is unchanged. `is_available` is `True` with no I/O.
5. **Goal only.** Body is `{model: jev-latest, state: {task}, questions: {route: choice}}`. Goal is
   control-char stripped, whitespace collapsed, truncated to 4000 chars. Context, history, tools and
   system prompts are never sent. Instructions tell Jev to ignore routing instructions in the task.
6. **Reject, don't redact.** A goal that looks like it carries a secret (private key block, `X_KEY=`
   env assignment, `password:`, `Authorization:`, bearer token, `sk-`/`ghp_`/`AKIA`/`xox`/`AIza`
   prefixes, or the literal `TYPESAFE_API_KEY` value) routes by default with no network. Regex
   redaction is fragile; refusing to send is the privacy-safe failure. False positives only cost the
   Jev choice.
7. **Exact-label answers only.** The `choice` must be an exact configured name (case-sensitive) and
   `confidence` a finite non-bool number ≥ 0.65; otherwise default route.
8. **Bounded transport.** stdlib `urllib`, fixed `https://api.typesafe.ai/v1/systemone`, redirects
   refused, 3 s default timeout, response read capped at 64 KiB, non-200 → failure. Error messages
   carry only the HTTP status, never the body (which could echo the key).
9. **Auth.** `TYPESAFE_API_KEY` from the environment only, read at call time; never logged or audited.
10. **Audit.** `get_hermes_home()/jev-routing/audit.jsonl`, file 0600, dir 0700. Row: timestamp,
    `mode: delegation`, outcome, reason, label, provider/model/reasoning_effort. No task text, no
    session (the provider receives none), no key. Write failure is swallowed (debug log only).
11. **Logs.** One info line per decision with reason or label; exceptions logged by type only.
12. **Approval policy untouched.** The plugin only answers "which route"; core keeps the lifecycle.
13. **Not enabled.** Nothing edits config.yaml; `scripts/live_check.py --delegate` refuses unless the
    user has set `delegation.backend: jev_routes` themselves.

## 2026-11-10 Quota fallback (shared with hermes-jev-routing)

14. **One gate, two callers.** `quota_gate.py` lives here; hermes-jev-routing loads it by path so main
    chat and subagents follow one rule set. It reads `quota_cache.json` + the `auth.json` pool only, no
    network. Stale (>10 min) or unreadable data never blocks (fail open).
15. **Lines.** Warn at <= 10 % left (worst window), hard floor <= 3 %, return to Claude at >= 25 %
    (hysteresis so the prompt cache is not dropped every turn). A second live Claude account in the
    pool counts as spare, so one account running low does not trigger a provider switch.
16. **Order (user's rule).** Claude low -> warn -> Codex (main: desktop offer auto-accepts after 2 min;
    sub: moves at the floor, or at once when main is already on Codex) -> neither has room -> refuse
    with the earliest reset time. Never falls through to the paid API key.
17. **Codex counts per account.** Parked and pool-exhausted accounts are ignored; the best remaining
    account's worst window decides.
18. **Default route stays gated too.** A low-confidence fallback goes through the same gate.
19. **Opus 5.5 floor, Fable 5.2 for work beyond it (2026-11-10).** User decision: default model is Opus 5.5; Jev decides on its own when a task is harder than Opus 5.5 handles and sends it to Fable 5.2. No per-switch confirmation. Haiku 5.5 and Opus 4.8 left both main and sub routes. Main: xpremium = Fable 5.2 high (fallback Opus 5.5 xhigh). Sub: new `fable` option. Backups: `jev-routing/main.json.bak-before-fable`, `backups/config/`.

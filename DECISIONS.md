# Decisions

## Automatic account rotation in the installer

- Use the catalog `account-pool` plugin as the credential-rotation owner. Jev's quota gate only decides cross-provider fallback; it must not invent a second account selector.
- Configure the existing Linux systemd user service and timer: every minute, run `hermes account all balance --threshold 95` (5% remaining). Keep the same unit names so reinstalling cannot create duplicate schedulers.
- Back up replaced units and reject symlinks or units owned by another Hermes home. Never change credentials or another profile. Use native `hermes config set` for the default profile's fill-first strategy.
- Configure account-pool directly in the default profile instead of calling its all-profile `setup` command. Leave existing account-pool versions installed; let the native plugin manager handle a missing dependency.
- Pin all account-pool CLI calls and the service to `--profile default`; pin the main routing installer to its explicitly targeted profile. A sticky `active_profile` must not redirect configuration or the scheduled balance.
- Fail if dependency installation or unit activation fails. Warn if the immediate balance run fails while the timer is active, preserving the installer's remaining instructions. Print a clear limitation when Linux user systemd is unavailable; do not claim rotation was enabled on unsupported platforms.
- Accept both native unquoted and installer-quoted `HERMES_HOME` unit declarations. Write UTF-8 units atomically; do not back up unchanged units.
- Polling is not an exact threshold interrupt. A large request, parallel requests or delayed vendor metrics can still exhaust quota. Keep provider fallback and its warnings separate.

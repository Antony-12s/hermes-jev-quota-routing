# Decisions

## Automatic account rotation in the installer

- Use the catalog `account-pool` plugin as the credential-rotation owner. Jev's quota gate only decides cross-provider fallback; it must not invent a second account selector.
- Configure the existing Linux systemd user service and timer: every minute, run `hermes account all balance --threshold 95` (5% remaining). Keep the same unit names so reinstalling cannot create duplicate schedulers.
- Back up replaced units and reject symlinks or units owned by another Hermes home. Never change credentials or another profile. Use native `hermes config set` for the default profile's fill-first strategy.
- Configure account-pool directly in the default profile instead of calling its all-profile `setup` command. Leave existing account-pool versions installed; let the native plugin manager handle a missing dependency.
- Fail if dependency installation, unit activation or the immediate balance run fails. Print a clear limitation when Linux user systemd is unavailable; do not claim rotation was enabled on unsupported platforms.
- Polling is not an exact threshold interrupt. A large request, parallel requests or delayed vendor metrics can still exhaust quota. Keep provider fallback and its warnings separate.

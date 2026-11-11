#!/usr/bin/env bash
# Install hermes-jev-quota-routing into a Hermes home (default ~/.hermes).
# Safe to re-run: existing plugin folders are backed up, an existing main.json is kept.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS=(hermes-jev-routing typesafe-jev-delegation)

command -v hermes >/dev/null || { echo "hermes CLI not found. Install Hermes Agent first." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found." >&2; exit 1; }

stamp="$(date +%Y%m%d-%H%M%S)"
backup="$HOME_DIR/backups/jev-quota-routing-$stamp"
mkdir -p "$HOME_DIR/plugins" "$HOME_DIR/jev-routing"

for name in "${PLUGINS[@]}"; do
  if [ -e "$HOME_DIR/plugins/$name" ]; then
    mkdir -p "$backup"
    mv "$HOME_DIR/plugins/$name" "$backup/"
    echo "backed up old $name -> $backup/$name"
  fi
  cp -R "$REPO/plugins/$name" "$HOME_DIR/plugins/$name"
  echo "installed plugin $name"
done

main_json="$HOME_DIR/jev-routing/main.json"
if [ -e "$main_json" ]; then
  cp "$REPO/examples/main.json" "$main_json.example"
  echo "kept your $main_json (new defaults saved as main.json.example)"
else
  cp "$REPO/examples/main.json" "$main_json"
  echo "wrote $main_json"
fi

hermes plugins enable hermes-jev-routing >/dev/null
hermes plugins enable typesafe-jev-delegation >/dev/null
hermes config set plugins.entries.hermes-jev-routing.settings.mode auto >/dev/null
hermes config set plugins.entries.hermes-jev-routing.settings.config_path "$main_json" >/dev/null

# Subagent routes: only offer providers this machine can actually run.
auth="$(hermes auth list 2>/dev/null || true)"
enabled="$(hermes config get plugins.enabled 2>/dev/null || true)"
has_codex=0; has_gemini=0
grep -Eq '^openai-codex \([1-9]' <<<"$auth" && has_codex=1
grep -q 'antigravity-oauth' <<<"$enabled" && has_gemini=1
options="$(HAS_CODEX=$has_codex HAS_GEMINI=$has_gemini python3 "$REPO/scripts/delegation_options.py")"
hermes config set delegation.backends.jev_routes.options "$options" >/dev/null
hermes config set delegation.backend jev_routes >/dev/null
hermes config set delegation.provider anthropic >/dev/null
hermes config set delegation.model claude-opus-5-5 >/dev/null
echo "subagent routes set (codex: $has_codex, gemini: $has_gemini)"

# Account rotation is separate from Jev's cross-provider quota guard.
python3 "$REPO/scripts/install_account_rotation.py"

echo
echo "Done. Still to do by hand:"
grep -q '^TYPESAFE_API_KEY=' "$HOME_DIR/.env" 2>/dev/null \
  || echo "  - add TYPESAFE_API_KEY=<your key> to $HOME_DIR/.env (get one at https://typesafe.ai)"
[ -d "$HOME_DIR/plugins/quota" ] \
  || echo "  - optional, for quota warnings: hermes plugins install rarf/hermes-quota-plugin --enable"
echo "  - restart the Hermes Desktop app, then Settings > Plugins > turn on hermes-jev-routing"
echo "  - open a NEW chat: running chats keep their old subagent routing"

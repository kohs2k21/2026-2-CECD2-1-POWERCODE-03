#!/usr/bin/env bash
set -euo pipefail
umask 077

# Install reviewed local deployment files only. Never start deployments implicitly.
if [[ $# != 2 || "$1" != /* || "$2" != /* ]]; then
  echo 'Usage: install-deploy.sh /absolute/server-directory /absolute/deploy.env' >&2
  exit 2
fi
source_server="$1"
config_source="$2"
if [[ ! -f "$config_source" ]]; then
  echo 'Deployment environment file is missing.' >&2
  exit 2
fi
if [[ "$(id -u)" == 0 ]]; then
  echo 'Run as the rootless Docker deployment user, not root.' >&2
  exit 2
fi
if ! docker info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless'; then
  echo 'The current Docker daemon must be rootless.' >&2
  exit 2
fi
for name in infra/compose.yaml infra/scripts/deploy.py infra/scripts/deploy_lib.py \
            infra/systemd/powercode-deploy.service infra/systemd/powercode-deploy.timer; do
  [[ -f "$source_server/$name" ]] || { echo "Missing installation input: $name" >&2; exit 2; }
done
for unit in powercode-deploy.service powercode-deploy.timer; do
  active_state="$(systemctl --user show "$unit" --property=ActiveState --value)"
  case "$active_state" in
    inactive|failed) ;;
    *) echo 'Stop the deployment timer and wait for the current deployment before updating.' >&2; exit 2 ;;
  esac
done
install_root="$HOME/powercode-deploy"
install -d -m 700 "$install_root/server/infra/scripts" \
  "$HOME/.config/powercode-deploy" "$HOME/.local/state/powercode-deploy" \
  "$HOME/.config/systemd/user"
install -m 600 "$config_source" "$HOME/.config/powercode-deploy/deploy.env"
install -m 644 "$source_server/infra/compose.yaml" "$install_root/server/infra/compose.yaml"
install -m 644 "$source_server/infra/scripts/deploy.py" "$source_server/infra/scripts/deploy_lib.py" \
  "$install_root/server/infra/scripts/"
install -m 644 "$source_server/infra/systemd/powercode-deploy.service" \
  "$source_server/infra/systemd/powercode-deploy.timer" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
echo 'Installed. Timer remains stopped; validate configuration and release before enabling.'

#!/usr/bin/env bash
set -euo pipefail

# Run from a workstation (not the seedbox) to deploy the latest committed code:
# ssh in, fast-forward the checkout, then relaunch both screen sessions via
# start.sh.
#
# Expects a `seedbox` entry in your ~/.ssh/config, e.g.:
#   Host seedbox
#       HostName your-host.example.com
#       User you
#       IdentityFile ~/.ssh/id_ed25519
# Set up key auth (ssh-copy-id seedbox) so it runs without a password prompt.
#
# Defaults to the `seedbox` ssh alias; override target/path/branch via env vars:
#   SEEDBOX_SSH=other-host SEEDBOX_DIR=~/apps/seedbox-mcp SEEDBOX_BRANCH=dev just deploy
#
# Uses a login shell on the remote (bash -lc) so uv is on PATH — a plain
# `ssh host 'cmd'` skips the login profile and would not find uv.

SSH_TARGET="${SEEDBOX_SSH:-seedbox}"
REMOTE_DIR="${SEEDBOX_DIR:-~/seedbox-mcp}"
BRANCH="${SEEDBOX_BRANCH:-main}"

echo "Deploying to ${SSH_TARGET}:${REMOTE_DIR} (branch ${BRANCH})"

ssh "$SSH_TARGET" "bash -lc '
  set -euo pipefail
  cd ${REMOTE_DIR}
  git checkout --quiet ${BRANCH}
  git pull --ff-only
  uv sync --quiet
  bash scripts/start.sh
'"

echo "Waiting for server to come up..."
sleep 3
ssh "$SSH_TARGET" "bash -lc 'cd ${REMOTE_DIR} && bash scripts/healthcheck.sh'" || \
  echo "(health check failed — server may still be starting; retry in a few seconds)"

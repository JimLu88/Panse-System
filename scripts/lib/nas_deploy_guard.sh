#!/usr/bin/env bash
# Shared production deployment guard.
#
# It prevents two workstations from changing the NAS release concurrently.
# The lock is held for the whole unified API + Web release. Standalone API or
# Web deployments also acquire the same lock, so the protection cannot be
# bypassed accidentally by using an older entry point.

PANSE_NAS_DEPLOY_LOCK_DIR="${PANSE_NAS_DEPLOY_LOCK_DIR:-$NAS_DIR/.panse-release-lock}"

panse_acquire_nas_deploy_lock() {
  if [[ "${PANSE_NAS_DEPLOY_LOCK_HELD:-0}" == "1" ]]; then
    return 0
  fi

  PANSE_NAS_DEPLOY_LOCK_TOKEN="${PANSE_NAS_DEPLOY_LOCK_TOKEN:-$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}}"
  export PANSE_NAS_DEPLOY_LOCK_TOKEN

  local owner="${PANSE_NAS_DEPLOY_LOCK_TOKEN}|$(hostname)|$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if ! "${SSH[@]}" "set -eu; if mkdir '$PANSE_NAS_DEPLOY_LOCK_DIR' 2>/dev/null; then printf '%s\\n' '$owner' > '$PANSE_NAS_DEPLOY_LOCK_DIR/owner'; else echo 'LOCKED_BY='\$(cat '$PANSE_NAS_DEPLOY_LOCK_DIR/owner' 2>/dev/null || echo unknown); exit 23; fi"; then
    echo "FATAL: another Panse ERP deployment is active on the NAS. Wait for it to finish; do not remove the lock without checking the other workstation." >&2
    return 1
  fi

  PANSE_NAS_DEPLOY_LOCK_HELD=1
  export PANSE_NAS_DEPLOY_LOCK_HELD
  echo "[guard] acquired NAS deployment lock: $PANSE_NAS_DEPLOY_LOCK_TOKEN"
}

panse_release_nas_deploy_lock() {
  if [[ "${PANSE_NAS_DEPLOY_LOCK_HELD:-0}" != "1" || -z "${PANSE_NAS_DEPLOY_LOCK_TOKEN:-}" ]]; then
    return 0
  fi

  "${SSH[@]}" "set -eu; if [ -f '$PANSE_NAS_DEPLOY_LOCK_DIR/owner' ] && grep -q '^${PANSE_NAS_DEPLOY_LOCK_TOKEN}|' '$PANSE_NAS_DEPLOY_LOCK_DIR/owner'; then rm -f '$PANSE_NAS_DEPLOY_LOCK_DIR/owner'; rmdir '$PANSE_NAS_DEPLOY_LOCK_DIR'; fi" || {
    echo "WARN: failed to release NAS deployment lock $PANSE_NAS_DEPLOY_LOCK_TOKEN; inspect it before the next release." >&2
    return 1
  }
  PANSE_NAS_DEPLOY_LOCK_HELD=0
  export PANSE_NAS_DEPLOY_LOCK_HELD
  echo "[guard] released NAS deployment lock: $PANSE_NAS_DEPLOY_LOCK_TOKEN"
}

panse_require_candidate_supports_database_revision() {
  local revisions revision
  revisions=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-db-1 psql -U panse -d panse_erp -Atc 'select version_num from alembic_version order by version_num'")
  if [[ -z "$revisions" ]]; then
    echo "FATAL: could not read the production Alembic revision; refusing to deploy." >&2
    return 1
  fi

  while IFS= read -r revision; do
    [[ -n "$revision" ]] || continue
    if ! grep -RqsE "^revision[[:space:]]*=[[:space:]]*['\"]${revision}['\"]" backend/alembic/versions; then
      echo "FATAL: production database is at revision '$revision', but the candidate code does not contain that revision." >&2
      echo "       Refusing to replace the running API. Merge the newer migration/code first; never downgrade the database." >&2
      return 1
    fi
  done <<< "$revisions"

  echo "[guard] candidate code contains production DB revision(s): $(tr '\n' ' ' <<< "$revisions" | xargs)"
}

#!/usr/bin/env bash
set -euo pipefail
export TZ=Europe/Stockholm
export GIT_TERMINAL_PROMPT=0
: "${PUBLICATIONS_PATH:=/opt/shipbytes-publications}"
: "${SHIPBYTES_DEPLOY_PATH:=$HOME/docker/shipbytes}"
: "${PUBLISHER_LOCK_PATH:=$SHIPBYTES_DEPLOY_PATH/.publisher-sync.lock}"
log() { printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >&2; }
trap 'log "sync/publish failed at line $LINENO"' ERR
exec 9>"$PUBLISHER_LOCK_PATH"
if ! flock -n 9; then log 'another sync/publisher is running'; exit 1; fi
cd "$PUBLICATIONS_PATH"
if [[ -n "$(git status --porcelain)" ]]; then
    log 'checkout has local changes; refusing to discard them'
    exit 1
fi
publish_once() {
    local before after result count
    before=$(git rev-parse HEAD)
    git fetch --quiet origin master
    git reset --hard origin/master >/dev/null
    after=$(git rev-parse HEAD)
    log "publications: local=$before remote=$after"
    changed=0
    if [[ "$before" != "$after" ]]; then changed=1; else log 'no new Git revision'; fi
    result=$(cd "$SHIPBYTES_DEPLOY_PATH" && docker compose --env-file .env --env-file .release.env exec -T --interactive=false app python -m shipbytes.publish_repository /publications --git-sha "$after")
    count=$(python3 -c 'import json,sys; value=json.load(sys.stdin)["published"]; assert type(value) is int and value>=0; print(value)' <<< "$result")
    published=$count
    log "published=$published"
}
publish_once
if [[ "$changed" == 0 && "$published" == 0 ]]; then
    log 'retrying once in 3600 seconds'
    sleep 3600
    publish_once
fi

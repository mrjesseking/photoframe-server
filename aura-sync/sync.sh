#!/bin/sh
# Pulls photos from one or more Aura frames into the photo folder, then repeats on a schedule.
# A failed run is logged and retried next time; it never affects the photo server.
#
# Settings (environment variables):
#   AURA_EMAIL            Aura account email (required)
#   AURA_PASSWORD         Aura account password, or
#   AURA_PASSWORD_FILE    a file containing it (for Docker secrets)
#   AURA_FRAMES           frames to pull, as name=frame-id pairs separated by commas, e.g.
#                         "livingroom=abf53be3-b73d-4de3-98cd-cfd289bd82df,kitchen=1234abcd-..."
#                         The frame id is the last part of https://app.auraframes.com/frame/<id>
#   AURA_SYNC_INTERVAL    seconds between runs (default 21600 = 6 hours)
#   AURA_RUN_TIMEOUT      give up on one frame's run after this many seconds (default 14400)
#   AURA_YEAR_FOLDERS     set to 1 to sort photos into one subfolder per year
#   AURA_DOWNLOAD_VIDEOS  set to 1 to also download the video part of Live Photos and video clips
#                         (the photo server does not show them, so they only use disk space)
#   AURA_PHOTO_ROOT       where photos go (default /photos/aura, one subfolder per frame name)
#   AURA_ONCE             set to 1 to run once and exit
#   AURA_SCRIPT, AURA_CONFIG_DIR   overrides, mainly for testing
set -u
umask 022   # the photo server runs as a different user and must be able to read the files

SCRIPT="${AURA_SCRIPT:-/opt/aura-frame-downloader/download-aura-photos.py}"
CONFIG_DIR="${AURA_CONFIG_DIR:-/tmp/aura}"
ROOT="${AURA_PHOTO_ROOT:-/photos/aura}"
INTERVAL="${AURA_SYNC_INTERVAL:-21600}"
RUN_TIMEOUT="${AURA_RUN_TIMEOUT:-14400}"

log() { printf '%s aura-sync: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

EMAIL="${AURA_EMAIL:-}"
PASSWORD="${AURA_PASSWORD:-}"
if [ -z "$PASSWORD" ] && [ -n "${AURA_PASSWORD_FILE:-}" ]; then
    [ -r "$AURA_PASSWORD_FILE" ] || die "cannot read AURA_PASSWORD_FILE ($AURA_PASSWORD_FILE)"
    PASSWORD="$(cat "$AURA_PASSWORD_FILE")"
fi
[ -n "$EMAIL" ] || die "AURA_EMAIL is not set"
[ -n "$PASSWORD" ] || die "AURA_PASSWORD (or AURA_PASSWORD_FILE) is not set"
[ -n "${AURA_FRAMES:-}" ] || die "AURA_FRAMES is not set (expected name=frame-id,name=frame-id)"
[ -f "$SCRIPT" ] || die "downloader script not found at $SCRIPT"
case "$EMAIL$PASSWORD" in
    *"
"*) die "the email and password must not contain line breaks" ;;
esac

# Validate the frame list and write the downloader's config file (readable only by us).
mkdir -p "$CONFIG_DIR" || die "cannot create $CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
CONFIG="$CONFIG_DIR/credentials.ini"
{
    printf '[login]\nemail = %s\npassword = %s\n' "$EMAIL" "$PASSWORD"
} > "$CONFIG.tmp"

NAMES=""
OLD_IFS="$IFS"; IFS=','
for pair in $AURA_FRAMES; do
    IFS="$OLD_IFS"
    name="${pair%%=*}"; id="${pair#*=}"
    name="$(printf '%s' "$name" | tr -d ' ')"; id="$(printf '%s' "$id" | tr -d ' ')"
    case "$pair" in *=*) ;; *) die "bad AURA_FRAMES entry '$pair' (expected name=frame-id)" ;; esac
    case "$name" in ''|*[!A-Za-z0-9._-]*) die "frame name '$name' may only contain letters, digits, . _ -" ;; esac
    case "$id" in ''|*[!A-Za-z0-9-]*) die "frame id for '$name' may only contain letters, digits and -" ;; esac
    mkdir -p "$ROOT/$name" || die "cannot create $ROOT/$name (is the photo folder writable?)"
    printf '\n[%s]\nfile_path = %s\nframe_id = %s\n' "$name" "$ROOT/$name" "$id" >> "$CONFIG.tmp"
    NAMES="$NAMES $name"
    IFS=','
done
IFS="$OLD_IFS"
chmod 600 "$CONFIG.tmp"; mv "$CONFIG.tmp" "$CONFIG"

EXTRA=""
[ "${AURA_YEAR_FOLDERS:-0}" = "1" ] && EXTRA="--years"
[ "${AURA_DOWNLOAD_VIDEOS:-0}" = "1" ] || EXTRA="$EXTRA --no-videos"

trap 'log "stopping"; exit 0' TERM INT

while :; do
    for name in $NAMES; do
        log "pulling frame '$name' into $ROOT/$name"
        # shellcheck disable=SC2086
        if timeout "$RUN_TIMEOUT" python "$SCRIPT" "$name" --config "$CONFIG" $EXTRA; then
            log "frame '$name' finished"
        else
            log "frame '$name' did not finish cleanly (exit $?); will try again next time"
        fi
    done
    [ "${AURA_ONCE:-0}" = "1" ] && break
    log "next run in ${INTERVAL}s"
    sleep "$INTERVAL" &
    wait $!
done

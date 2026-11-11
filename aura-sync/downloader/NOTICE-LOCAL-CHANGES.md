# Local copy of aura-frame-downloader

This is the command-line part of https://github.com/meub/aura-frame-downloader (version 2.0.0, MIT licence,
see LICENSE), copied from a local download that was read through before use. The graphical app, build
scripts and sample files are not included. Only `requests` is needed to run it.

It talks only to `api.pushd.com` and `imgproxy.pushd.com` over HTTPS (plus the video address Aura returns).
It is an unofficial tool that uses Aura's own app interface, which Aura can change at any time.

## Changes made here (all in `aura/core.py` and `download-aura-photos.py`)

1. **Safe downloads.** A photo is written to `<name>.part` and renamed only after an HTTP 200 reply and,
   when the server states a length, a complete transfer. Before, a failed or interrupted download left a broken
   file with the final name, which was then skipped as "already downloaded" on every later run.
2. **Safe file names.** Text from Aura's reply (`taken_at`, `id`, extension) can no longer contain slashes or
   `..`, so a malformed reply cannot write outside the target folder.
3. **Timeouts** on the login and photo-list requests, which had none.
4. **`--no-videos`** option (new) to skip the video part of Live Photos, which the photo server does not use.

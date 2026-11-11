# Photo Frame server

A small service that shares a folder of photos with the Animated Photo Frame app on Apple TV.
It is written with the Python standard library only, so there is nothing to install, nothing to
compile, and the same Docker image runs on a Raspberry Pi 3/4/5 (32- or 64-bit), a Synology NAS
or any Linux server.

It serves your original files as they are (no re-encoding), so it uses almost no CPU. It reads
each photo's shape from the file header, which is how the Apple TV plans layouts before it
downloads anything. It understands JPEG, PNG and HEIC, including photos stored sideways.
Files are recognised by their contents, not their extension.

## Quick start

1. On the machine that will serve the photos, save `docker-compose.yml` from this repository
   (that one file is all you need; the image is downloaded for you).
2. Edit it: set `TOKEN` to a long random string (for example the output of
   `openssl rand -hex 24`) and set the photos path.
3. Run `docker compose up -d`.
4. Check it: open `http://<machine-address>:8080/api/v1/health` in a browser. You should see
   `"ok":true` and a photo count.
5. In the Apple TV app open the settings panel, choose Photos, Photo server, and enter
   `<machine-address>:8080` and the same token.

## Where the image comes from

Every push to `main` runs the tests and publishes the image to GitHub's container registry as
`ghcr.io/<owner>/photoframe-server:latest` (and `...-aura-sync` for the optional Aura companion),
built for amd64, arm64 and arm/v7, so one command works on a Pi 3, Pi 4/5, Synology or any Linux
server. Tagging a release such as `v1.0.0` also publishes that version number.

* **If the package is public** nothing more is needed. The image contains no secrets: the token
  and your photo folder are supplied when it runs.
* **If the package is private** (the default for a private repository), run
  `docker login ghcr.io -u <github-user>` once on each machine, using a personal access token with
  the `read:packages` permission as the password. To avoid that, make the package public in
  GitHub under the repository's Packages, Package settings, Change visibility.
* **Updating:** `docker compose pull && docker compose up -d`.
* **Building it yourself** instead: use `build: .` in place of the `image:` line (see the comment
  in the compose file).

## Adding photos (bulk import)

Put photos anywhere inside the photo folder, in subfolders if you like, by any means: copy from
your computer over the network share, rsync, a USB drive, a sync tool. The server looks for new
files every 30 seconds and the Apple TV checks every minute, so new photos start appearing within
about a minute and are favoured until they have been shown once. Removing a file removes the
photo the same way.

A file is not picked up until it has been unchanged for 10 seconds, so a large copy that is still
in progress is never shown half-finished. Hidden files and the folders NAS systems create
(`@eaDir`, `#recycle`, `.DS_Store` and so on) are ignored.

## Keeping photos in step with an Aura frame (optional)

The `aura-sync` companion pulls photos from an Aura account on a schedule, so what is on the
Aura frame also shows on the Apple TV. It runs a reviewed local copy of the open-source
[aura-frame-downloader](https://github.com/meub/aura-frame-downloader) (MIT licence, in
`aura-sync/downloader`; nothing is fetched from GitHub when you build) and saves
into an `aura` subfolder of your photo folder, one subfolder per frame. The photo server then
treats those files like any others, so nothing else changes and the downloader can be turned off
at any time. It is off by default.

1. In `docker-compose.yml`, edit the `aura-sync` service: your Aura email and password, and your
   frames as `name=frame-id` pairs. The frame id is the end of the frame's address in the Aura web
   app, `https://app.auraframes.com/frame/<id>`. Use the same photos path as the server, writable.
2. Start it with `docker compose --profile aura up -d`.
3. Watch it with `docker compose logs -f aura-sync`. The first pull of a large frame can take a
   long time; the downloader waits between files to avoid being throttled and picks up where it
   left off on the next run, skipping photos it already has.

Things to know:

* **Unofficial.** The downloader is a community tool that talks to Aura's own app interface, which
  Aura can change without notice. If it stops working, the frame and the Apple TV carry on showing
  what they have. Whether it suits Aura's terms of service is for you to judge.
* **Your Aura password** is in the compose file (or use `AURA_PASSWORD_FILE` with a Docker secret).
  It is written only to a private file inside the container, and sent only to Aura's login address.
* **The downloader copy.** It is the command-line part of upstream version 2.0.0, with four small
  fixes listed in `aura-sync/downloader/NOTICE-LOCAL-CHANGES.md`: photos are written to a temporary
  name and renamed only when complete (so a failed download can never leave a broken file that is
  then skipped forever), file names built from Aura's reply are made safe, the login and list
  requests have timeouts, and videos can be skipped. To update it, replace that folder with a newer
  reviewed copy and re-apply those changes.
* **Videos** (the video part of Live Photos) are skipped by default because the photo server ignores
  them. Set `AURA_DOWNLOAD_VIDEOS=1` to fetch them anyway.
* **The same photo** added to Aura by different people is downloaded once per person, so it can
  appear twice.
* **Bulk import still works alongside it.** Aura photos live in `aura/`; put your own anywhere else.

## Setting it up on common hardware

**Raspberry Pi.** Install Docker (`curl -fsSL https://get.docker.com | sh`), then follow the
quick start. If the photos are on a USB drive, use its mount point as the photos path, and make
sure the drive is mounted before Docker starts. A Pi 3 is fine for this job.

**Synology.** Install Container Manager from Package Center. Put this folder in a shared folder,
create a Project from the `docker-compose.yml`, and set the photos path to your photos share (for
example `/volume1/photos`). If the log says the photo folder cannot be read, add a `user:` line
(see the comment in the compose file) using the user id and group id of an account with read access.
For bulk import, share the photos folder over SMB as usual and copy files into it.

**Linux server.** The quick start applies unchanged.

## Security

Anyone on your network who has the address and token can view the photos, so keep the token
secret and do not forward the port to the internet. The service is read-only: it cannot change or
delete anything, and photos are looked up by opaque id, never by path. The container runs as an
unprivileged user with a read-only filesystem and the photos mounted read-only. Traffic is plain
HTTP, which is normal for a home network; if you ever need access from outside your home, put a
reverse proxy with HTTPS in front rather than exposing this port.

## Settings (environment variables)

| Name | Default | Meaning |
| --- | --- | --- |
| `TOKEN` | none | Shared secret the app must send. Leave empty only on a network you fully trust. |
| `TOKEN_FILE` | none | Read the token from this file instead (for Docker secrets). |
| `PHOTO_DIR` | `/photos` | Where photos are mounted inside the container. |
| `DATA_DIR` | `/data` | Where the photo index is kept so restarts are quick. |
| `PORT` | `8080` | Port to listen on. |
| `SCAN_INTERVAL` | `30` | Seconds between looks for new or removed photos. |
| `SETTLE_SECONDS` | `10` | A file must be unchanged this long before it is included. |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs every request. |

Settings for the optional `aura-sync` container: `AURA_EMAIL`, `AURA_PASSWORD` (or
`AURA_PASSWORD_FILE`), `AURA_FRAMES`, `AURA_SYNC_INTERVAL` (default `21600` seconds),
`AURA_YEAR_FOLDERS` (`1` to sort into year folders), `AURA_DOWNLOAD_VIDEOS`, `AURA_RUN_TIMEOUT` (default `14400` seconds
per frame), `AURA_PHOTO_ROOT` (default `/photos/aura`). They are described at the top of `aura-sync/sync.sh`.

## API (version 1)

All paths begin `/api/v1`. When a token is set, send `Authorization: Bearer <token>` (except for health).

* `GET /health` returns `{"ok":true,"version","photos","revision","authRequired"}`.
* `GET /catalog` returns `{"revision","photos":[{"id","width","height","revision"}],"warnings":[...]}`.
  Sizes are as displayed (rotation applied). The reply has an `ETag`; send it back as
  `If-None-Match` to get a cheap `304` when nothing has changed.
* `GET /photos/<id>?v=<revision>` returns the original file with its content type. Supports
  `Range` and `If-None-Match`. Because the revision is part of the URL, responses are cacheable forever.

## Troubleshooting

* **Photo count is 0 or low.** Look at the container log (`docker compose logs`) and at the
  `warnings` in `/api/v1/catalog`. Unreadable files are listed there by name.
* **"is not a folder" at start.** The photos path in the compose file is wrong or not mounted yet.
* **Permission denied / no photos on Synology.** Add the `user:` line described above.
* **Photos in the folder but not shown.** Files modified in the last 10 seconds are held back;
  wait a moment. Also check the extension is one of `.jpg .jpeg .png .heic .heif`.

## Development and tests

`python3 -m unittest discover -s tests -t .` from this folder (Python 3.9 or newer). To run without
Docker: `PHOTO_DIR=/some/photos DATA_DIR=/tmp/pf PORT=8080 TOKEN=test python3 -m photoserver`.

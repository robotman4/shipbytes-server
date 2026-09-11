# GitHub publication ingestion

`robotman4/shipbytes-publications` carries content on `master`. This server repository carries application code, schema and operations files. No content is written back by the server. A committed issue is eligible once its `published_date` is today or earlier in Europe/Stockholm. The database determines whether it has already been sent.

## Content contract: version 1

```
issues/
  2026/
    2026-09-15.json
    2026-09-18.json
```

One UTF-8 JSON file is one issue. Use the publication date as the filename and slug. Directories named drafts/published have no special meaning. Every `issues/**/*.json` file is validated, including future and previously published files. Non-issue files such as the repository README are ignored.

The machine-readable contract is [publication.schema.json](publication.schema.json), generated from `shipbytes.publication_schema.RepositoryIssue`. Producers must supply:

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer `1`; no coercion from strings or booleans |
| `slug` | Unique lowercase ASCII words/numbers separated by hyphens, 1–120 characters |
| `title`, `subject` | Nonempty strings, at most 300 characters |
| `published_date` | ISO calendar date `YYYY-MM-DD` |
| `intro` | Optional string, at most 5,000 characters |
| `stories` | 1–20 story objects, in newsletter order |

Each story requires `slug`, `title`, `byte`, `summary`, `why_it_matters`, and supports `source_name`, `source_url`, `source_type` and optional `published_at`. Byte, summary and significance limits are respectively 1,500, 30,000 and 2,000 characters. The source type defaults to `external`; external stories require a nonempty source name and an absolute HTTP(S) URL. Original Ship Bytes reporting uses `shipbytes` and may omit source fields. Blank required text, extra properties, duplicate JSON keys, duplicate issue slugs, duplicate story slugs within/across files, malformed JSON, unsupported versions, and conflicting database slugs are rejected. Files larger than 256 KB and symlinks are rejected. Subjects replace em dashes with plain hyphens.

The JSON Schema describes field constraints. Uniqueness across files/database, duplicate JSON object keys, semantic date checks and external-source requirements are also enforced by Python. Changing this contract incompatibly requires a new schema version; version 1 must remain stable.

No fake stories are placed in the content repository. `tests/test_repository.py` holds the labelled test content used by the automated suite.

## Import and publication

The host checkout is `/opt/shipbytes-publications`, mounted read-only at `/publications`. Git is required only on the host. Normal operation uses `scripts/sync-and-publish.sh`; a manual check against an already synchronized checkout is:

```sh
cd ~/docker/shipbytes
docker compose --env-file .env --env-file .release.env exec -T app \
  python -m shipbytes.publish_repository /publications
```

The command reads the checkout HEAD directly when no `--git-sha` is provided. The sync script supplies the exact fetched SHA. An exported tree without `.git` needs `--git-sha COMMIT`. Do not change/reset the checkout while invoking the CLI directly; use the sync script when updating Git.

All files and database conflicts are preflighted before the first email. A validation failure exits nonzero with no sends for that invocation. Correct the committed file and run again. A bad future file cannot mutate an already published issue. After validation, due issues are processed in chronological `(published_date, slug)` order. Future issues are deferred. Published issues are skipped even when their source file has changed. Editing an already published file never republishes it or silently changes the website.

Imported issues save `source_filename`, `source_git_sha`, a content hash, and `publication_date`. `status` progresses through `pending`, `publishing`, `published`, or `failed`. Existing API drafts retain the `draft` status until attempted. `published_at` and `resend_broadcast_id` record the actual completed publication. Public archives expose only `published` issues.

Both the API and CLI use the same publisher and an exclusive OS file lock beside the SQLite database. The CLI holds it through validation/import/publication. This works across processes and survives no stale-lock condition after a process exits. SQLite persists each external-operation intent before making that call. The host sync lock is separate and covers the complete fetch/check/wait/check sequence.

### Safe retries

A rendering/validation failure never calls Resend. A connection failure before sending a request or a definite HTTP rejection (400, 401, 403, 404, 422, 429) leaves a failed issue retryable. If Broadcast creation succeeded and sending was rejected, the retry reuses the saved Broadcast ID. It does not create another Broadcast. Corrected content can replace a pending/failed import only before Broadcast creation was accepted or became uncertain.

A timeout after transmitting a request, a server error with uncertain acceptance, or a crash during create/send leaves `creating`, `sending` or `uncertain`. Normal publication refuses further provider writes for that issue. Inspect Resend using the saved ID or `Ship Bytes: SLUG` name before recovery. Never clear an uncertain state merely to force a retry. If Resend shows the Broadcast already sent, reconcile that ID and the published state locally; do not send again. If creation returned no ID, confirm the provider outcome before adopting an existing draft or retrying. Keep a backup before manual reconciliation.

Resend's documented [idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys) apply to transactional email endpoints, not Broadcast creation. The app therefore favors avoiding duplicate sends over automatic recovery from an unknowable result. There is no atomic transaction spanning SQLite and Resend. The issue becomes publicly visible after Resend acknowledges send; a crash in that narrow interval requires reconciliation.

Errors return exit code 1. Successful CLI runs emit one JSON result to stdout (`published`, `skipped`, `deferred` counts); timestamped operator messages go to stderr. No keys, tokens, provider response bodies or subscriber addresses are logged. Batch provider failure stops processing later issues and returns nonzero; already successful issues remain published and are skipped next time.

## Host setup

The public GitHub repository must have an initial commit on `master`. An empty README is enough. Install Git, Docker Compose, Python 3, Bash and `flock` on the host, then as the deployment user:

```sh
sudo install -d -o "$USER" -g "$(id -gn)" -m 755 /opt/shipbytes-publications
git clone --branch master --single-branch \
  https://github.com/robotman4/shipbytes-publications.git /opt/shipbytes-publications
```

For a private repository, configure a read-only deploy key or credential helper for that user. Never embed a GitHub token in the origin URL or script. The application does not need a GitHub credential.

The Compose configuration defaults `PUBLICATIONS_PATH` to `/opt/shipbytes-publications`. Create it before deployment. `SHIPBYTES_DEPLOY_PATH` selects the host Compose directory, defaulting to `$HOME/docker/shipbytes`. The sync script reads `.env` and `.release.env` there. Its lock is `.publisher-sync.lock` in that directory; `PUBLISHER_LOCK_PATH` can override it.

The script acquires `flock`, refuses local changes, records HEAD, fetches `origin master`, and resets to `origin/master`. It always runs the idempotent publisher after a successful sync, even when SHA is unchanged. Git/network, JSON, database, rendering and Resend failures stop the run with a nonzero exit. They are never treated as “no changes.”

After a successful check with zero published issues, it waits 3,600 seconds and checks again, up to four checks total. This applies even if Git changed (for example a README-only commit). It stops immediately after publishing one or more issues, or after the fourth check. All overdue eligible issues are considered, including earlier failed attempts. Errors still stop the run immediately with a nonzero exit; they do not trigger these no-content retries.

## Scheduling in Stockholm time

ChatGPT content generation is expected Tuesday and Friday at 05:00 Europe/Stockholm. This feature does not configure the external ChatGPT generation task. Publication runs at 06:00, with conditional checks at approximately 07:00, 08:00 and 09:00. The timer starts only at 06:00; the script handles the three hourly retries. Each wait starts after the preceding check completes, so times may drift slightly.

For cron implementations that support `CRON_TZ` (such as Cronie), install this in the deployment user's crontab after ensuring they can append the log:

```cron
CRON_TZ=Europe/Stockholm
0 6 * * 2,5 /home/ubuntu/docker/shipbytes/scripts/sync-and-publish.sh >> /var/log/shipbytes-publisher.log 2>&1
```

Change the script path if the server lives at `/opt/shipbytes-server`. Do not install this unchanged on cron implementations that ignore `CRON_TZ`; setting ordinary `TZ` only changes the process environment, not necessarily cron's schedule.

The configured OCI host uses UTC and has systemd, so the installed schedule uses the supplied `systemd/shipbytes-publisher.service` and `.timer`. The calendar explicitly names Europe/Stockholm and follows daylight saving time:

```ini
OnCalendar=Tue,Fri *-*-* 06:00:00 Europe/Stockholm
```

Adjust the example service's `User`, `ExecStart` and directory environment values for another deployment. Install the two units into `/etc/systemd/system`, then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now shipbytes-publisher.timer
systemctl list-timers shipbytes-publisher.timer
# Manual execution, including up to three conditional one-hour waits:
sudo systemctl start --no-block shipbytes-publisher.service
```

Use either cron or the timer, not both. The timer does not catch up missed runs at boot; the next run considers all overdue content. Logs append to `/var/log/shipbytes-publisher.log`. The oneshot service allows four hours for all four attempts and uses no persistent worker between runs. Rotate the log with the supplied `systemd/shipbytes-publisher.logrotate` configuration.

## Verification

Run `make test`. Tests block external networking and mock Resend. Coverage includes malformed and unsupported input, duplicate slugs, future issues, source validation, chronological order, successful import/publication, idempotency, concurrent publishers, safe retries, uncertain failures, rendering failure and the shell script's bounded retries and early stopping after publication. Fake `sleep` verifies a 3,600-second wait without delaying the suite.

Do not create a fake production issue to test the timer. An empty content checkout is a valid successful scan with zero publications. Use a real reviewed issue when the publication is ready to send.

## Issue and story images

The canonical image contract is **`image.url`**. Issue covers and individual stories use the same optional image object. Upload and verify an image before committing its URL. Git contains metadata, not image binaries.

```json
"image": {
  "url": "https://www.dropbox.com/scl/fo/<folder-token>/<token>/2026/2026-09-11?dl=1&preview=example-story.jpg&rlkey=<shared-link-key>",
  "type": "generated",
  "alt": "Illustration of a connected cargo vessel at sea.",
  "credit": "Ship Bytes",
  "source_url": null,
  "usage": "generated"
}
```

Use the actual tested public download URL, including its required query parameters. Do not invent Dropbox tokens or resolve paths from a shared folder. The canonical real example is `issues/2026/2026-09-11.json` on `robotman4/shipbytes-publications` master, containing one cover and six story images.

* `url` is the transport/download reference. HTTP and HTTPS on standard ports are supported. No credentials, fragments or unsupported schemes are accepted.
* `source_url` is provenance or attribution, never the download address.
* `type` is `generated`, `licensed`, `source`, or `own`. `alt` is required. `source` requires a valid `source_url` and nonempty `usage` describing permission/licensing.
* Generated images default credit to `Ship Bytes` and usage to `generated`. They are illustrations, not documentary photography, and are labelled as such on the site.
* Historical stories without an image remain valid. Their detail pages fall back to the issue cover; listings only display a story image when one exists.

### Download and validation

The reusable remote fetcher performs GET requests and follows up to five redirects, validating every destination before contacting it. This supports Dropbox public URLs that redirect through a file-specific link to `*.dropboxusercontent.com`. No Dropbox API credentials, folder ZIP downloads or provider/path resolution are used. The temporary `provider/shared_folder_url/path` contract is no longer accepted.

Each request resolves the hostname, rejects private, loopback, link-local, reserved and multicast addresses, then pins the connection to a validated public IP. The original HTTP Host and TLS SNI are retained, preventing a second DNS lookup from rebinding the request to a private address. Redirects receive the same checks. Proxy environment variables are ignored. Connection timeout is 10 seconds, read timeout 30 seconds, with a bounded transfer time and 2 MiB (2,097,152 bytes) input limit.

Pillow decodes the actual bytes. A valid JPEG returned as `application/binary` is accepted; an HTML error page returned as `image/jpeg` is rejected. Accepted inputs are nonanimated JPEG, PNG and WebP, at most 4 million decoded pixels. Downloads stay in bounded memory, so failed downloads leave no temporary files. Persistent file writes use cleaned-up temporary files and atomic replacement.

Explicit remote-image failures stop import before sending email. All required images are prepared before any publication sends. Logs identify the publication, story where applicable, failure category, remote hostname and a short URL fingerprint. Query strings and response bodies are omitted to avoid leaking shared-link keys or signed URL tokens. Failed image refreshes preserve existing published database records and visible images.

### Local media and derivatives

Dropbox is transport only. The source is preserved under `/data/media`, alongside high-quality 1200x630 and 600x315 JPEG derivatives. Images are resized with Lanczos and padded white to fit when required. Preferred ratio is 1.91:1. JPEG quality starts at 92 and may reduce to 88 or 85 to approach 250 KB; quality never drops below 85 solely to hit that target. Larger derivatives are allowed. Each derivative is encoded directly from the decoded original, never from another compressed derivative.

Remote-image paths include the SHA-256 of the original bytes:

```text
/data/media/issues/{issue-slug}/{source-sha256}/source.jpg
/data/media/issues/{issue-slug}/{source-sha256}/cover-1200.jpg
/data/media/issues/{issue-slug}/{source-sha256}/cover-600.jpg
/data/media/stories/{story-slug}/{source-sha256}/cover-1200.jpg
/data/media/stories/{story-slug}/{source-sha256}/cover-600.jpg
```

The original extension follows decoded format. Websites use `/media/...` URLs on Ship Bytes. New newsletters use the issue's locally hosted 600px cover; social metadata uses the 1200px version. Story detail pages and web listings display their own image. Rendering never fetches from Dropbox. Existing sent newsletters remain unchanged.

### Idempotency and image updates

The existing `0005` Alembic migration adds nullable Story image fields and a normalized JSON `image_reference` on both issues and stories. It already supports the final URL metadata, so no destructive replacement model or redundant migration is needed. Local paths, alt text, credit, type, source_url and usage are preserved.

An unchanged reference with existing derivatives is reused without downloading or processing. Changing `image.url` triggers a fresh download; different bytes produce new content-addressed paths. Identical bytes reuse the same files. Optional `sha256` (64 lowercase hex characters) pins and verifies an expected source checksum.

Replacing bytes behind the same URL alone does not silently refresh published media. Use a changed URL/hash or explicitly refresh:

```sh
docker compose --env-file .env --env-file .release.env exec -T app \
  python -m shipbytes.publish_repository /publications --refresh-images
```

The command still publishes eligible unpublished issues. Already-published issues receive image-only updates: text, story membership, publication time and Broadcast ID are unchanged, and no email is resent. Old media files remain available for historical links. Omitting an image in historical JSON preserves existing media.

### Legacy local images and backups

Legacy image objects with `path: "assets/..."` instead of `url` remain valid. Paths are repository-relative and never reinterpreted as URLs. Traversal, absolute paths and symlinks are rejected. Do not supply both `url` and `path`. Legacy optional missing/corrupt assets retain their fallback behavior; remote references are required once explicitly supplied.

The persistent media directory defaults to `media` beside SQLite (`/data/media` in production), and `MEDIA_DIRECTORY` can override it for development. Back up and restore media with the database. The default logo stays at `/static/brand/shipbytes-default-transparent.png`.

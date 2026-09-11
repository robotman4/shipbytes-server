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

Images are staged in Dropbox; GitHub contains JSON metadata only for new images. Use the same optional `image` object on an issue and on each story. Stories without an image remain valid and use their issue cover on the full story page; story listings show an image only when that story has its own.

Configured public folder: `/ShipBytes`

```text
https://www.dropbox.com/scl/fo/ry05g9ow61zsroacmfdi1/AJcsXkGtzq3J8ONf0f0rJR8?rlkey=t1aod2y9bdclzg5y0d9y08tk8&dl=0
```

Upload and verify images before committing their references. Paths are relative to `/ShipBytes`, without a leading slash or `ShipBytes/` prefix. For an issue:

```json
"image": {
  "provider": "dropbox",
  "shared_folder_url": "https://www.dropbox.com/scl/fo/ry05g9ow61zsroacmfdi1/AJcsXkGtzq3J8ONf0f0rJR8?rlkey=t1aod2y9bdclzg5y0d9y08tk8&dl=0",
  "path": "2026/2026-09-11/cover.jpg",
  "type": "generated",
  "alt": "Illustration of a connected cargo vessel with satellite, cybersecurity, navigation and data overlays.",
  "credit": "Ship Bytes",
  "source_url": null,
  "usage": "generated"
}
```

Each story can contain the same object with its own path, such as `2026/2026-09-11/nexuswave-bv-e27-approval.jpg`, and a descriptive alt text. Do not reference a story image until that file exists. Do not call generated illustrations documentary photographs. Generated images are labelled as illustrations on the website.

`type` is `generated`, `licensed`, `source`, or `own`. `path`, `type`, and `alt` are required. `source` requires `source_url` and nonempty `usage` describing permission/licensing. Generated images default credit to `Ship Bytes` and usage to `generated`. `source_url` is attribution metadata, never a download address. Dropbox images additionally require `shared_folder_url`. The publisher rejects any folder other than `DROPBOX_SHARED_FOLDER_URL` in server configuration. Paths reject absolute paths, traversal, URL schemes, backslashes and encoded path segments.

### Transport and configuration

The dedicated `AssetResolver` supports two Dropbox transports without changing JSON:

* Without credentials, download the public folder as a ZIP using Dropbox's documented `dl=1` parameter. Read only the exact referenced member; never extract the archive to disk. One import downloads the archive at most once, only if an image needs importing. The compressed/decompressed HTTP response is capped at 32 MiB and each selected member at 2 MiB. Large archives fail explicitly instead of consuming unlimited resources.
* For a growing archive, set `DROPBOX_APP_KEY` and `DROPBOX_APP_SECRET` in the private server `.env`. The provider uses Dropbox's official `sharing/get_shared_link_file` API with app authentication, the shared-folder URL and `path: "/" + relative_path`. It downloads only the requested file. Configure both credentials together and recreate the app container. Never put credentials in issue JSON, Git or logs.

Dropbox does not document an unauthenticated child-file API for shared-folder links. We do not invent child-link tokens or scrape preview pages. Sources: [force downloads](https://help.dropbox.com/share/force-download) and [official shared-link API specification](https://github.com/dropbox/dropbox-api-spec/blob/main/sharing.stone).

Downloads follow up to five redirects, checking each HTTPS destination against Dropbox download hosts before connecting. Authentication is removed on redirects. Network, HTTP, missing member, size, checksum and image decoding failures are publication errors for explicit Dropbox references. The publisher prepares all required images before sending any newsletter. Existing published images remain visible if a refresh fails.

### Processing, caching and updates

The importer validates actual image content with Pillow: nonanimated JPEG, PNG or WebP, at most 2,097,152 bytes and 4 million pixels. It preserves the original and creates 1200x630 and 600x315 JPEGs with Lanczos resizing and white padding when needed. JPEG quality starts at 92, may reduce to 88 or 85 to aim below 250 KB, and never drops below 85 solely to meet a byte target. Large high-quality derivatives are allowed. Preferred cover ratio: 1.91:1.

Dropbox derivatives are immutable, content-addressed local files:

```text
/data/media/issues/{issue-slug}/{source-sha256}/source.jpg
/data/media/issues/{issue-slug}/{source-sha256}/cover-1200.jpg
/data/media/issues/{issue-slug}/{source-sha256}/cover-600.jpg
/data/media/stories/{story-slug}/{source-sha256}/cover-1200.jpg
/data/media/stories/{story-slug}/{source-sha256}/cover-600.jpg
```

The original extension follows decoded format. Public URLs use `/media/...` on Ship Bytes, never Dropbox. Issue covers remain in the archive, issue pages and newsletters (600px version). Stories use their own images on story pages, social metadata and web listings. Previously sent newsletter HTML is not rewritten.

SQLite records the normalized full reference, attribution, local paths and image type on both issues and stories. Unchanged references with existing local derivatives are reused without downloading or processing. For an intentional change, use a new path or add/change optional `sha256` (64 lowercase hex characters of the original file); a supplied hash is verified. Replacing bytes at the same Dropbox path alone does not silently refresh the site. To explicitly recheck the current references:

```sh
docker compose --env-file .env --env-file .release.env exec -T app \
  python -m shipbytes.publish_repository /publications --refresh-images
```

This still publishes eligible unpublished issues; already-published issues receive image-only updates and are never emailed again. Their text, story membership, publication time and Broadcast ID remain unchanged. Changed images receive new URLs; old files are retained for historical links. Repeated identical refreshes reuse the same file paths and do not rewrite matching files. Omitting an image in older JSON preserves existing media.

Legacy `image` objects without `provider` (or with `provider: "repository"`) still use repository-relative `assets/...` paths. They are never interpreted as Dropbox paths. Legacy optional broken/missing files retain their prior fallback behavior. Do not add new image binaries to Git. Remove a legacy file only after its Dropbox replacement is imported and verified.

The media directory defaults to `media` beside SQLite (`/data/media` in production). Back up and restore it with the database. `MEDIA_DIRECTORY` remains available for development. The default logo stays at `/static/brand/shipbytes-default-transparent.png`.

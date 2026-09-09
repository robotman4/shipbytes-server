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

After a successful first check, it waits exactly 3,600 seconds only if SHA did not change and no issue was published. It then fetches, resets and checks once more, and exits. A changed SHA with zero published issues (for example a README-only commit) does not trigger the extra check, matching the requested revision-based rule. All overdue eligible issues are considered, including earlier failed attempts. The retry does not loop indefinitely.

## Scheduling in Stockholm time

ChatGPT content generation is expected Tuesday and Friday at 05:00 Europe/Stockholm. This feature does not configure the external ChatGPT generation task. Publication runs at 06:00, with the conditional second check at approximately 07:00.

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
# Manual execution, including the conditional one-hour wait:
sudo systemctl start --no-block shipbytes-publisher.service
```

Use either cron or the timer, not both. The timer does not catch up missed runs at boot; the next run considers all overdue content. Logs append to `/var/log/shipbytes-publisher.log`. The oneshot service allows 90 minutes for both attempts and uses no persistent worker between runs. Rotate the log with the supplied `systemd/shipbytes-publisher.logrotate` configuration.

## Verification

Run `make test`. Tests block external networking and mock Resend. Coverage includes malformed and unsupported input, duplicate slugs, future issues, source validation, chronological order, successful import/publication, idempotency, concurrent publishers, safe retries, uncertain failures, rendering failure and the shell script's exact one-retry behavior. Fake `sleep` verifies a 3,600-second wait without delaying the suite.

Do not create a fake production issue to test the timer. An empty content checkout is a valid successful scan with zero publications. Use a real reviewed issue when the publication is ready to send.

## Optional issue image

Version 1 now accepts an optional `image` object; existing issue files remain valid. Commit the image and issue JSON together:

```text
issues/2026/2026-09-15.json
assets/2026/2026-09-15/cover.jpg
```

```json
"image": {
  "path": "assets/2026/2026-09-15/cover.jpg",
  "type": "source",
  "alt": "A factual description of the image",
  "credit": "Photographer or image provider",
  "source_url": "https://example.com/media",
  "usage": "Permission or licence reference"
}
```

`path`, `type` and `alt` are required when `image` is present. Paths are relative to the repository root and must stay under `assets/`. `type` is one of `generated`, `licensed`, `source`, or `own`. For `source`, a valid HTTP(S) `source_url` and nonempty `usage` are required. `source_url` may be `null` for the other types. Generated images default credit to `Ship Bytes` and usage to `generated`; extra attribution is optional. Image selection and permission checks belong to content preparation. Publishing does not fetch remote images or generate illustrations.

The importer accepts nonanimated JPEG, PNG and WebP images up to 2 MB (2,097,152 bytes) and 4 million pixels. It fits them within a white 1200 × 630 cover without cropping, creates a 600 × 315 thumbnail, and saves compressed JPEGs up to 250 KB each. Embedded source metadata is omitted from the rendered files. The preferred input ratio is 1.91:1. Other ratios are fitted with white padding rather than cropped. The approved original is copied to `/data/media/issues/{slug}/source.{extension}`. Derivatives are stored atomically at `/data/media/issues/{slug}/cover-1200.jpg` and `cover-600.jpg`, served at the corresponding `/media/issues/{slug}/` URLs. Attribution and type are stored in SQLite. The original and derivatives survive subsequent Git resets. Existing published issues and existing Broadcasts are never changed by editing Git assets.

Missing, unreadable, corrupt, animated, oversized or symlinked optional images produce a logged warning and use the Ship Bytes default. Invalid image metadata (such as path traversal or a malformed source URL) remains a schema validation error. Storage failures stop publication. Images remain optional, including for API-created issues, which currently use the default.

The supplied logo/default cover is preserved unchanged at `shipbytes/static/brand/shipbytes-default-transparent.png`, served at `/static/brand/shipbytes-default-transparent.png`. It also appears in the site header. Custom issue covers appear on the homepage, archive, issue page, social sharing metadata and future HTML newsletters. The plain-text newsletter stays text-only.

The media directory defaults to `media` beside the SQLite database, so production uses the existing `/data` volume. `MEDIA_DIRECTORY` can override this for development. Include `/data/media` in backups and restore it alongside SQLite; Git alone is not a backup of historically published media.

Newsletters use `cover-600.jpg`; `og:image` uses `cover-1200.jpg`. This is one cover per issue. Story entries remain text-only. The 06:00 publisher never fetches or generates artwork: commit the final approved image with the issue JSON. Only local validation, copying, resizing and publication run on the VM.

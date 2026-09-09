# Ship Bytes

*Maritime tech news, one byte at a time.*

A small server-rendered maritime technology publication. Each issue contains useful short Bytes, links to the full Ship Bytes context, and direct primary-source links. Original reporting gets a single “Read full story” link. There is no seeded content.

## Architecture

Python 3.13, FastAPI/Uvicorn (one worker), Jinja2, SQLAlchemy 2, Alembic and SQLite in WAL mode. Caddy terminates HTTPS. Docker Compose persists SQLite at `/data/shipbytes.db` in `shipbytes_app_data`. Only Caddy publishes host ports. Limits are 384 MB for the app and 128 MB for Caddy. Resend handles transactional confirmations and segment-based Broadcast delivery. No frontend build or JavaScript is required.

## Development

With Python 3.13:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
make dev
```

Without `.env`, development defaults to `http://localhost:8000`, a local SQLite file, and private JSON email files in `dev-emails/`. Open the confirmation URL from the generated file and press Confirm. Development publishing writes a Broadcast JSON file instead of sending. Set a local `PUBLISH_API_TOKEN` to exercise the API. No Resend credentials are needed.

```sh
make test   # Python 3.13 Docker image, network disabled during tests
make build  # production image built locally
```

Do not copy production `.env` defaults into a local development session without adjusting the database path, site URL, environment, and email backend.

## Configuration

Copy `.env.example` to `.env` on the production host, with mode `600`. Never commit it. Production validates configuration at startup and refuses a development email backend.

| Variable | Purpose |
| --- | --- |
| `SITE_URL` | Public HTTPS origin, without trailing slash or path |
| `SITE_NAME` | Publication name; default Ship Bytes |
| `DATABASE_URL` | `sqlite:////data/shipbytes.db` in containers |
| `ENVIRONMENT` | `production` or `development` |
| `EMAIL_BACKEND` | `resend` or `development` |
| `SECRET_KEY` | Random signing secret, at least 32 characters |
| `PUBLISH_API_TOKEN` | Independent random token, at least 32 characters in production |
| `RESEND_API_KEY` | Resend key with Contacts, Segments, Broadcast and email permissions |
| `RESEND_FROM_EMAIL` | Verified sender; use a monitored reply address |
| `RESEND_FROM_NAME` | Ship Bytes |
| `RESEND_SEGMENT_ID` | Dedicated Ship Bytes segment |
| `RESEND_WEBHOOK_SECRET` | Webhook signing secret beginning `whsec_` |
| `EMAIL_DIRECTORY` | Development-only email output directory |
| `DEPLOY_HOST` | SSH host or alias, defaults to `node2` |
| `DEPLOY_USER` | Optional SSH username; empty uses SSH config |
| `DEPLOY_PATH` | Defaults to the remote user’s `~/docker/shipbytes` |

Generate each secret independently with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Store output directly in the private configuration; do not paste it into logs.

## DNS and Resend

Point the site's A record to the OCI VM's public IPv4. Only add AAAA if IPv6 actually works. Allow inbound TCP 80 and 443 in both OCI security rules and the host firewall. Caddy automatically obtains and renews certificates and redirects HTTP to HTTPS. The Compose subnet `172.30.80.0/24` must be unused; if changing it, update the address range, Caddy’s fixed address, and Uvicorn’s trusted proxy address together.

Verify your sender domain in Resend using its provided DNS records. Create a dedicated Ship Bytes Segment and configure its ID. Register a webhook at `${SITE_URL}/webhooks/resend` for `contact.updated`, `contact.deleted`, `email.bounced` and `email.complained`, then save its signing secret in `.env`. The endpoint verifies the raw body with Svix, rejects stale signatures and deduplicates event IDs. Resend's unsubscribe placeholder is included in both email alternatives.

Subscriptions normalize addresses and store only a hash of a random confirmation token. The signed link lasts 24 hours and a confirmation POST consumes it once. Existing Resend unsubscribe preferences are preserved. Suppression events cannot be reversed by a stale subscribed event. Previously suppressed addresses require deliberate operator review; the public form never silently reactivates them.

Reference: [Resend Broadcast creation](https://resend.com/docs/api-reference/broadcasts/create-broadcast), [Contact segments](https://resend.com/docs/api-reference/contacts/add-contact-to-segment), and [webhook events](https://resend.com/docs/webhooks/contacts/updated).

## Deploy

Install Docker Engine and the Compose plugin on the host. The SSH user needs Docker access and ownership of the deployment directory. Create `/home/ubuntu/docker/shipbytes` and install the private `.env` there before first deployment. This script never copies local credentials or builds on production.

```sh
export DEPLOY_HOST=node2
export DEPLOY_PATH=/home/ubuntu/docker/shipbytes
export SITE_URL=https://your-public-hostname.example
make deploy
```

The script tests locally, detects remote architecture, builds with Buildx for amd64 or arm64, transfers and loads the image with checked pipelines, copies configuration, backs up an existing database, migrates, starts Compose, waits for container health and checks public HTTPS. Cross-platform builds may require local QEMU/binfmt support. It retains the old tagged image and `.previous-release.env`. Never run `docker compose down -v` or prune application volumes.

For routine host commands, select the current image explicitly:

```sh
cd /home/ubuntu/docker/shipbytes
docker compose --env-file .env --env-file .release.env ps
docker compose --env-file .env --env-file .release.env logs --tail 100 app
```

Application access logging is disabled to keep email confirmation tokens out of logs. Caddy access logging is not enabled. Restrict access to Docker and `.env`; Docker operators can read container secrets.

Rollback a compatible application image:

```sh
docker compose --env-file .env --env-file .previous-release.env up -d --wait
cp .previous-release.env .release.env
```

Review migration compatibility before rolling back. Restore the pre-deployment database only if required; restoring an old database can lose send records and cause duplicate sends. Reconcile all Broadcasts with Resend before resuming publishing after a restore.

## Publishing API

Export `SITE_URL` and `PUBLISH_API_TOKEN` in a private shell. Write reviewed content into `issue.json`:

```json
{
  "slug": "2026-09-15",
  "title": "Ship Bytes - September 15, 2026",
  "subject": "Ship Bytes - Maritime tech news, one byte at a time",
  "intro": "",
  "stories": [{
    "slug": "example-story",
    "title": "Example story - replace before publishing",
    "byte": "Short useful explanation.",
    "summary": "Longer explanation.\n\nA second paragraph of context.",
    "why_it_matters": "Practical significance.",
    "source_name": "Primary source",
    "source_url": "https://example.com",
    "source_type": "external"
  }]
}
```

```sh
curl --fail-with-body "$SITE_URL/api/v1/issues" \
  -H "Authorization: Bearer $PUBLISH_API_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @issue.json

# This makes the issue public and sends a real Broadcast in production.
curl --fail-with-body -X POST "$SITE_URL/api/v1/issues/2026-09-15/publish" \
  -H "Authorization: Bearer $PUBLISH_API_TOKEN"
```

Subject lines use plain hyphens, never em dashes. The API normalizes em dashes on creation, and the email backend also normalizes existing drafts before sending.

Creation returns 201 and never sends email. Publication returns the issue URL, story count and Broadcast ID. Authentication errors return 401; duplicate slugs or publication attempts return 409; validation failures return 422. HTML is escaped; summaries are plain text with blank lines between paragraphs. Original reporting uses `source_type: "shipbytes"` and may omit source fields.

The public paths are `/`, `/subscribe`, `/subscribe/confirm`, `/issues`, `/issues/{slug}`, `/stories/{slug}`, `/about`, `/privacy`, and `/health`.

## Repository ingestion and scheduled publishing

The server ingests versioned JSON from `robotman4/shipbytes-publications` on `master`. See [the publication workflow](docs/publications.md) and [JSON Schema](docs/publication.schema.json) for the content contract, read-only checkout, Stockholm schedule, bounded retry and failure recovery.

Both publishing entry points share durable provider intent and a process lock. Definite rejected requests can retry safely, reusing any known Broadcast ID. Ambiguous failures block further writes pending Resend reconciliation. An issue is marked published only after a successful send response. Never reset an uncertain delivery state without checking provider history.

## Backups and restore

Run on the host:

```sh
cd /home/ubuntu/docker/shipbytes
bash scripts/backup.sh
```

The script uses Python's SQLite online backup API and writes a timestamped private database file and a matching `.db.media.tar.gz` media archive in `backups/`. Restore both together. Copy backups to separate storage; schedule the script with cron and choose a retention policy. Backups contain personal data. No data volume is removed during deployment.

To restore, stop the application, retain the current database as a backup, then restore a chosen SQLite backup into the volume. Example after copying the backup to `backups/restore.db`:

```sh
docker compose --env-file .env --env-file .release.env stop app
docker compose --env-file .env --env-file .release.env run --rm -T --no-deps --user 0 \
  -v "$PWD/backups:/backups:ro" app python -c \
  'import os, sqlite3; src=sqlite3.connect("file:/backups/restore.db?mode=ro", uri=True); dst=sqlite3.connect("/data/shipbytes.db"); src.backup(dst); dst.close(); src.close(); os.chown("/data/shipbytes.db", 10001, 10001)'
docker compose --env-file .env --env-file .release.env run --rm app python -m alembic upgrade head
# Reconcile external Broadcast history and current Resend subscription preferences before publishing.
```

For the matching media archive, copy it to `backups/restore.media.tar.gz` and, while the app is still stopped, run:

```sh
docker compose --env-file .env --env-file .release.env run --rm -T --no-deps --user 0 \
  -v "$PWD/backups:/backups:ro" app python -c \
  'import os, pathlib, tarfile; archive=tarfile.open("/backups/restore.media.tar.gz"); archive.extractall("/data", filter="data"); archive.close(); paths=[pathlib.Path("/data/media"), *pathlib.Path("/data/media").rglob("*")]; [os.chown(p, 10001, 10001) for p in paths if p.exists()]'
```

Start the app after restoring the database and any media:

```sh
docker compose --env-file .env --env-file .release.env up -d --wait
```

Keep the application stopped during restoration. Do not overwrite a live database with a raw file copy.

## Changing domain

Update DNS, `SITE_URL` in the host `.env` and the deployment shell, and the Resend webhook URL, then redeploy. Update sender-domain verification only if changing the email sender. Existing emails retain their old links; keep the previous hostname redirecting to the new origin where possible. No application URLs embed the initial hostname.

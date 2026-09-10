# AGENTS.md - Ship Bytes

## Project

Build and deploy **Ship Bytes**, a small maritime technology news publication and email newsletter.

Brand:

**Ship Bytes**
*Maritime tech news, one byte at a time.*

Production URL:

`https://shipbyt.es`

Keep the domain configurable. Do not hardcode the hostname anywhere except default/example configuration.

The application must run entirely in Docker Compose on a small OCI Linux VM with approximately:

* 2 CPU cores
* 1 GB RAM
* persistent disk
* public IPv4
* ports 80/443 available

Keep the stack deliberately small.

Do not introduce Kubernetes, Redis, PostgreSQL, Node.js frontends, message queues, Celery, or other infrastructure unless there is an actual demonstrated need.

---

# Product concept

Ship Bytes is not a traditional long-form newsletter.

The information hierarchy is:

1. **Byte**
2. **Read more**
3. **Source**

The newsletter itself should be useful without requiring clicks.

Each story starts with a very short description of what happened.

A reader who wants more context can click **Read more** and get the Ship Bytes summary/article.

A reader who wants the original information can click **Source** and go directly to the primary source.

No clickbait.

No deliberately withholding the important information to generate clicks.

Example:

## Inmarsat NexusWave receives BV E27 approval

Bureau Veritas has approved the complete NexusWave onboard connectivity architecture against IACS UR E27, including its integrated cyber controls.

**Why it matters:** Class approval is increasingly covering the complete vessel connectivity stack rather than individual components.

[Read more] [Source]

The Ship Bytes internal page can then contain a longer summary, technical context, related developments and source references.

---

# Editorial model

An Issue contains multiple Stories.

Typical issue:

* 5-10 stories
* approximately 3-5 minutes total newsletter reading time
* published Tuesday and Friday

Relevant subjects include:

* vessel IT
* onboard infrastructure
* ship-shore networking
* Starlink
* LEO/GEO satellite connectivity
* VSAT
* SD-WAN
* maritime cybersecurity
* IACS UR E26/E27
* OT security
* vessel remote access
* identity
* Microsoft 365 / Teams onboard
* maritime satellite providers
* vessel monitoring
* edge computing
* IoT
* practical AI deployments
* automation
* digital twins
* navigation systems
* maritime software
* fleet technology deployments

Avoid:

* generic ESG stories
* executive appointments
* awards
* conference advertisements
* webinar advertisements
* empty vendor PR
* vague "AI will transform shipping" stories
* stories without practical or industry significance

---

# Story structure

Every story should support:

```json
{
  "slug": "inmarsat-nexuswave-bv-e27",
  "title": "Inmarsat NexusWave receives BV E27 approval",
  "byte": "Bureau Veritas has approved the complete NexusWave onboard connectivity architecture against IACS UR E27.",
  "summary": "Longer Ship Bytes summary and context.",
  "why_it_matters": "Class approval is increasingly covering the complete vessel connectivity stack rather than individual components.",
  "source_name": "Bureau Veritas",
  "source_url": "https://example.com/original-source",
  "published_at": "2026-09-09T12:00:00Z"
}
```

`byte` should normally be 1-3 sentences.

`summary` may be several paragraphs.

The application must distinguish between:

### Externally sourced story

Show:

* Read more
* Source

`Read more` points to the Ship Bytes internal story page.

`Source` points directly to the external primary source.

### Original Ship Bytes reporting

If Ship Bytes is itself the original source, do not display two redundant links.

Show:

* Read full story

---

# Site

The public website should contain:

```text
/
 /subscribe
 /subscribe/confirm
 /issues
 /issues/{issue-slug}
 /stories/{story-slug}
 /about
 /privacy
```

Homepage:

* Ship Bytes branding
* tagline
* subscription form
* latest issue
* recent stories
* links to previous issues

Design should be simple, typography-focused and fast.

Do not create a dashboard-looking website.

Do not create generic SaaS cards everywhere.

It should look like a small technical publication.

Server-render HTML.

JavaScript should be optional for normal reading.

---

# Application stack

Use:

* Python 3.13
* FastAPI
* Uvicorn
* Jinja2
* SQLAlchemy 2
* Alembic
* SQLite
* httpx
* Pydantic
* Caddy
* Docker Compose

Use SQLite WAL mode.

Database file:

`/data/shipbytes.db`

Persist `/data` using a Docker volume or bind mount.

The application should start comfortably within a 1 GB RAM VM.

Target normal steady-state memory consumption should remain well below 512 MB.

---

# Resend

Use Resend for outbound email.

Do not implement an SMTP server.

Environment:

```text
RESEND_API_KEY=
RESEND_FROM_EMAIL=
RESEND_FROM_NAME=Ship Bytes
RESEND_SEGMENT_ID=
RESEND_WEBHOOK_SECRET=
```

Use the Resend API directly over HTTPS or their maintained Python SDK.

The email newsletter should be sent using **Resend Broadcasts**, not one API request per subscriber.

Use Resend Contacts/Segments for actual newsletter delivery.

---

# Subscription workflow

The public form asks only for:

```text
email
```

Optional first name can be added later but should not be required.

Implement double opt-in.

Flow:

```text
POST /subscribe
        |
        v
store pending subscription
        |
        v
send confirmation email through Resend
        |
        v
user clicks signed confirmation URL
        |
        v
mark confirmed
        |
        v
create/update Resend Contact
        |
        v
add to Ship Bytes Segment
```

Confirmation tokens must:

* be cryptographically random
* expire
* be single-use

Do not expose sequential database IDs.

---

# Unsubscribe

Newsletter Broadcasts must include Resend's unsubscribe functionality.

Resend should be considered authoritative for whether a confirmed Contact is currently subscribed.

Implement a Resend webhook endpoint:

```text
POST /webhooks/resend
```

Verify the Resend webhook signature.

At minimum process:

```text
contact.updated
contact.deleted
email.bounced
email.complained
```

If a contact becomes unsubscribed, reflect that locally.

Never send manually to an unsubscribed address.

---

# Database

Minimum models:

## Subscriber

```text
id
email
status
resend_contact_id
confirmation_token_hash
confirmation_expires_at
created_at
confirmed_at
updated_at
```

Possible statuses:

```text
pending
subscribed
unsubscribed
bounced
complained
```

Normalize email addresses before uniqueness checks.

Email must have a unique database constraint.

---

## Issue

```text
id
slug
title
subject
intro
status
published_at
resend_broadcast_id
created_at
updated_at
```

Statuses:

```text
draft
published
```

Slug must be unique.

---

## Story

```text
id
issue_id
slug
title
byte
summary
why_it_matters
source_name
source_url
source_type
sort_order
published_at
created_at
updated_at
```

`source_type`:

```text
external
shipbytes
```

Slug must be unique.

---

# Publishing API

The important integration is a machine-facing publishing API.

This is eventually intended to be called by an AI agent that researches and writes Ship Bytes.

Authentication:

```text
Authorization: Bearer <PUBLISH_API_TOKEN>
```

Environment:

```text
PUBLISH_API_TOKEN=
```

Use constant-time comparison.

Never log the token.

---

## Create draft issue

```text
POST /api/v1/issues
```

Payload:

```json
{
  "slug": "2026-09-15",
  "title": "Ship Bytes - September 15, 2026",
  "subject": "Ship Bytes - Maritime tech news, one byte at a time",
  "intro": "",
  "stories": [
    {
      "slug": "example-story",
      "title": "Example story",
      "byte": "Short useful explanation.",
      "summary": "Longer Ship Bytes explanation.",
      "why_it_matters": "Why a maritime IT person should care.",
      "source_name": "Original source",
      "source_url": "https://example.com",
      "source_type": "external"
    }
  ]
}
```

Creating an issue must NOT send email.

This prevents accidental sends while testing integrations.

---

## Publish issue

```text
POST /api/v1/issues/{slug}/publish
```

Publishing must:

1. validate the complete issue
2. reject an empty issue
3. reject duplicate publishing
4. render the web issue
5. make it publicly visible
6. generate newsletter HTML
7. generate newsletter plain text
8. create and send a Resend Broadcast
9. save the Resend Broadcast ID
10. mark the issue as published

The operation must be idempotent.

Calling publish twice must never result in two newsletter sends.

---

# Newsletter email

Email should be deliberately simple.

Header:

```text
Ship Bytes
Maritime tech news, one byte at a time.
```

Then issue date.

For each story:

```text
TITLE

BYTE

Why it matters: ...

Read more | Source
```

Do not place the complete long summary in the newsletter.

The objective is a useful 3-5 minute overview.

Use normal HTML that works in Outlook, Gmail and mobile clients.

Do not depend on external CSS.

Use inline CSS where necessary.

Always generate a plain-text alternative.

---

# Newsletter archive

Each issue must be publicly available at:

```text
/issues/{slug}
```

Each summarized story:

```text
/stories/{slug}
```

Story page should contain:

* title
* byte
* full Ship Bytes summary
* why it matters
* source attribution
* direct source URL
* publication date
* issue backlink

External links should clearly indicate the source.

---

# API output

Publishing endpoints should return useful structured JSON.

Example:

```json
{
  "status": "published",
  "issue": "2026-09-15",
  "url": "https://shipbyt.es/issues/2026-09-15",
  "stories": 7,
  "broadcast_id": "..."
}
```

Errors must have useful HTTP status codes and messages.

---

# Security

This is an Internet-facing service.

Implement:

* secure HTTP headers
* HTTPS only
* CSRF protection where relevant
* validation on every input
* rate limiting on subscription requests
* honeypot field on subscription form
* no secrets in git
* no secrets in Docker image
* no secrets in logs
* signed webhook verification
* signed subscription confirmation URLs
* API authentication for publishing endpoints

Caddy should redirect HTTP to HTTPS.

Do not expose the FastAPI/Uvicorn port publicly.

Only Caddy gets host ports 80 and 443.

---

# Configuration

Use `.env`.

Provide:

```text
.env.example
```

Example:

```text
SITE_URL=https://shipbyt.es
SITE_NAME=Ship Bytes

DATABASE_URL=sqlite:////data/shipbytes.db

SECRET_KEY=
PUBLISH_API_TOKEN=

RESEND_API_KEY=
RESEND_FROM_EMAIL=
RESEND_FROM_NAME=Ship Bytes
RESEND_SEGMENT_ID=
RESEND_WEBHOOK_SECRET=
```

No real credentials in `.env.example`.

---

# Docker

Provide:

```text
Dockerfile
compose.yml
Caddyfile
.dockerignore
```

Use a small Python base image.

Run application as a non-root user.

Include a health endpoint:

```text
GET /health
```

Return:

```json
{
  "status": "ok"
}
```

Compose should include health checks.

Expected containers:

```text
shipbytes-app
shipbytes-caddy
```

No database container.

---

# Resource constraints

The deployment VM has only approximately 1 GB RAM.

Do not build Docker images on the production server.

Docker images must be built on the development/Codex machine and transferred to or pulled by production.

Set sensible container resource limits.

Suggested starting point:

```text
app:
  memory: 384M

caddy:
  memory: 128M
```

Do not add unnecessary workers.

Start Uvicorn with one worker.

This application will be primarily I/O bound.

---

# Deployment

Create:

```text
scripts/deploy.sh
```

Deployment must happen over SSH.

Configuration:

```text
DEPLOY_HOST=
DEPLOY_USER=
DEPLOY_PATH=/opt/shipbytes
```

Do not embed host credentials.

The deployment script should:

1. run tests
2. determine target server architecture over SSH
3. build the Docker image locally for the target platform
4. transfer the image to the remote host
5. transfer updated Compose/Caddy configuration
6. load the Docker image remotely
7. run database migrations
8. run `docker compose up -d`
9. wait for health check
10. request `https://shipbyt.es/health`
11. fail deployment if the health check does not succeed
12. preserve the previous image for rollback

Do not run `docker build` on the OCI host.

Target architecture may be either:

```text
linux/amd64
linux/arm64
```

Detect it rather than assuming it.

A container registry is not required for the first implementation.

A reasonable transfer method is:

```text
docker save IMAGE | gzip | ssh HOST 'gunzip | docker load'
```

Do not blindly copy that command without adding proper error checking.

---

# Backups

Create:

```text
scripts/backup.sh
```

Back up:

```text
/data/shipbytes.db
```

Use SQLite's safe backup mechanism, not a raw copy while writes may be occurring.

Keep timestamped backups.

The deployment process must never delete the data volume.

---

# Testing

Use pytest.

Minimum coverage:

* homepage
* subscription
* duplicate subscription
* confirmation
* expired confirmation
* publishing API authentication
* issue creation
* issue validation
* issue publishing
* publish idempotency
* newsletter rendering
* Resend API mocked
* webhook signature validation
* unsubscribe webhook
* bounced subscriber handling

No tests may send real email.

---

# Development

Provide:

```text
make dev
make test
make build
make deploy
```

or equivalent scripts.

Local development should not require Resend credentials for viewing the website.

Implement a development email backend that logs rendered emails to stdout or writes them into a local directory.

---

# README

Create a useful `README.md`.

It must cover:

* architecture
* local development
* environment variables
* DNS requirements
* Resend configuration
* deployment
* backups
* restore
* publishing API examples
* webhook configuration
* changing the domain later

Include curl examples for creating and publishing an issue.

---

# Initial branding/content

The initial homepage should display:

# Ship Bytes

**Maritime tech news, one byte at a time.**

Intro text:

> Short, useful updates on maritime IT, connectivity, cyber and technology. Read the byte, dig into the context when it matters, or go straight to the source.

Do not fill the site with fake articles.

A single clearly labelled sample/demo issue is acceptable during development.

---

# Important implementation principles

Prefer boring technology.

Prefer server-side rendering.

Prefer explicit code over frameworks hiding behavior.

Prefer simple files over unnecessary services.

Keep dependencies low.

Do not prematurely optimize for millions of users.

Do not add features unrelated to the initial publication workflow.

Do not make the design look AI-generated.

Do not fill pages with generic marketing copy.

Do not use fake testimonials.

Do not invent subscribers, readership numbers or industry claims.

---

# Definition of done

The task is complete when:

1. `https://shipbyt.es` serves the Ship Bytes website over valid HTTPS.
2. A visitor can subscribe.
3. The visitor receives a confirmation email.
4. Confirming adds them to the Resend mailing list.
5. An authenticated API call can create a draft issue.
6. An authenticated API call can publish the issue.
7. The published issue appears on the website.
8. Publishing creates exactly one Resend Broadcast.
9. The newsletter contains Bytes and links to Read More and Source.
10. Unsubscribe works.
11. Resend webhook events are processed safely.
12. SQLite survives container replacement/deployment.
13. Automated tests pass.
14. Deployment can be repeated safely.
15. README explains how to operate the service.

When implementation is complete, deploy it to the configured OCI host and verify the live HTTPS site end-to-end.

---

# Host access
You can access the host via "ssh node2", ssh keys are already set.

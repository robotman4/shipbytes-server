#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
name="shipbytes-$(date -u +%Y%m%dT%H%M%SZ)-$$.db"
docker compose exec -T --interactive=false app python -c 'import sqlite3, sys; import tempfile; tmp=tempfile.NamedTemporaryFile(); src=sqlite3.connect("/data/shipbytes.db"); dst=sqlite3.connect(tmp.name); src.backup(dst); dst.close(); src.close(); sys.stdout.buffer.write(open(tmp.name, "rb").read())' > "backups/$name.partial"
chmod 600 "backups/$name.partial"
mv "backups/$name.partial" "backups/$name"
docker compose exec -T --interactive=false app python -c 'import pathlib, sys, tarfile; media=pathlib.Path("/data/media"); archive=tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz"); media.exists() and archive.add(media, arcname="media"); archive.close()' > "backups/$name.media.tar.gz.partial"
chmod 600 "backups/$name.media.tar.gz.partial"
mv "backups/$name.media.tar.gz.partial" "backups/$name.media.tar.gz"
echo "Backup saved: backups/$name and backups/$name.media.tar.gz"

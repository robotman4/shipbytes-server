"""Bounded asset transport. Dropbox links never become public media URLs."""
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import httpx

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
DEFAULT_DROPBOX_FOLDER = 'https://www.dropbox.com/scl/fo/ry05g9ow61zsroacmfdi1/AJcsXkGtzq3J8ONf0f0rJR8?rlkey=t1aod2y9bdclzg5y0d9y08tk8&dl=0'

class AssetError(ValueError):
    pass

def folder_identity(url):
    value = urlsplit(str(url))
    query = dict(parse_qsl(value.query))
    if (value.scheme != 'https' or value.hostname != 'www.dropbox.com'
            or value.username or value.password or value.port not in (None, 443)
            or not value.path.startswith('/scl/fo/') or value.fragment
            or set(query) - {'rlkey', 'dl'} or not query.get('rlkey')):
        raise AssetError('Invalid Dropbox shared-folder URL')
    return value.path.rstrip('/'), query['rlkey']

def validate_folder(url, configured):
    if folder_identity(url) != folder_identity(configured):
        raise AssetError('Dropbox shared folder is not the configured Ship Bytes folder')

def _allowed_download(url):
    value = urlsplit(str(url))
    host = value.hostname or ''
    if (value.scheme != 'https' or value.username or value.password or value.port not in (None, 443)
            or not (host in ('www.dropbox.com', 'content.dropboxapi.com', 'dropboxusercontent.com')
                    or host.endswith('.dropbox.com') or host.endswith('.dropboxusercontent.com'))):
        raise AssetError('Dropbox redirected outside allowed download hosts')

def download(url, limit, *, headers=None, auth=None, method='GET'):
    """Validate every redirect before contacting it; bound decompressed response bytes."""
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False) as client:
            for _ in range(6):
                _allowed_download(url)
                with client.stream(method, url, headers=headers, auth=auth) as response:
                    if response.is_redirect:
                        location = response.headers.get('location')
                        if not location:
                            raise AssetError('Dropbox returned a redirect without a destination')
                        url = str(response.url.join(location))
                        method, headers, auth = 'GET', None, None
                        continue
                    if response.status_code != 200:
                        raise AssetError(f'Dropbox download failed (HTTP {response.status_code})')
                    if int(response.headers.get('content-length', 0)) > limit:
                        raise AssetError('Dropbox download exceeds size limit')
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise AssetError('Dropbox download exceeds size limit')
                    return bytes(data)
        raise AssetError('Dropbox exceeded redirect limit')
    except httpx.HTTPError:
        raise AssetError('Dropbox download failed due to a network error') from None

class AssetResolver:
    """One resolver per import: at most one public ZIP fetch for all new references."""
    def __init__(self, root, config):
        self.root, self.config, self.archive = Path(root), config, None

    def read(self, spec):
        if spec.provider != 'dropbox':
            path = self.root / spec.path
            if (not path.resolve().is_relative_to(self.root.resolve())
                    or any((self.root / Path(*Path(spec.path).parts[:i])).is_symlink()
                           for i in range(1, len(Path(spec.path).parts)+1))):
                raise AssetError('Repository image path escapes checkout or uses a symlink')
            try:
                with path.open('rb') as source:
                    raw = source.read(MAX_IMAGE_BYTES + 1)
            except OSError:
                raise AssetError('Repository image is unavailable') from None
        else:
            validate_folder(spec.shared_folder_url, self.config.dropbox_shared_folder_url)
            if self.config.dropbox_app_key and self.config.dropbox_app_secret:
                raw = download('https://content.dropboxapi.com/2/sharing/get_shared_link_file', MAX_IMAGE_BYTES,
                    method='POST', auth=(self.config.dropbox_app_key, self.config.dropbox_app_secret),
                    headers={'Dropbox-API-Arg': json.dumps({'url': str(spec.shared_folder_url), 'path': '/' + spec.path})})
            else:
                if self.archive is None:
                    url = urlsplit(self.config.dropbox_shared_folder_url)
                    query = dict(parse_qsl(url.query)); query['dl'] = '1'
                    payload = download(urlunsplit(url._replace(query=urlencode(query))), MAX_ARCHIVE_BYTES)
                    try:
                        self.archive = zipfile.ZipFile(io.BytesIO(payload))
                    except zipfile.BadZipFile:
                        raise AssetError('Dropbox did not return a valid shared-folder archive') from None
                matches = [item for item in self.archive.infolist() if item.filename == spec.path]
                if len(matches) != 1 or matches[0].is_dir():
                    raise AssetError(f'Dropbox image is missing or ambiguous: {spec.path}')
                item = matches[0]
                if item.file_size > MAX_IMAGE_BYTES or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise AssetError('Dropbox image exceeds size limit or is a symlink')
                try:
                    with self.archive.open(item) as source:
                        raw = source.read(MAX_IMAGE_BYTES + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    raise AssetError('Dropbox image could not be read') from None
        if len(raw) > MAX_IMAGE_BYTES:
            raise AssetError('Image exceeds 2 MB input limit')
        return raw

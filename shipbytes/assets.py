"""Bounded remote image GETs and legacy repository file reads."""
import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import httpx

MAX_IMAGE_BYTES = 2 * 1024 * 1024

class AssetError(ValueError):
    pass

def validate_url(url):
    try:
        value = urlsplit(str(url))
        if (value.scheme not in ('http', 'https') or not value.hostname
                or value.username or value.password or value.fragment
                or value.port not in (None, 80, 443)
                or any(ord(c) < 32 for c in str(url)) or '\\' in str(url)):
            raise ValueError()
        return value
    except ValueError:
        raise AssetError('Invalid image URL: expected HTTP(S) without credentials on a standard port') from None

def public_address(host, port):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise AssetError('Image download DNS lookup failed') from None
    if not addresses:
        raise AssetError('Image download DNS lookup returned no addresses')
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise AssetError('Image download blocked a private or non-public IP address')
    return next((entry[4][0] for entry in addresses if entry[0] == socket.AF_INET), addresses[0][4][0])

def download(url, limit=MAX_IMAGE_BYTES):
    """Pin each connection to a validated public IP, preserving HTTP Host and TLS SNI.

    Redirects are followed manually so every destination is checked before connecting.
    No proxies, credentials, cookies or arbitrary response content enter logs.
    """
    deadline = time.monotonic() + 90
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False) as client:
            for _ in range(6):
                parsed = validate_url(url)
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                address = public_address(host, port)
                # Avoid a second DNS resolution (DNS rebinding) in the HTTP transport.
                pinned = httpx.URL(str(url)).copy_with(host=address)
                host_header = host if parsed.port is None else f'{host}:{port}'
                client.cookies.clear()
                with client.stream('GET', pinned,
                        headers={'Host':host_header, 'User-Agent':'ShipBytes-ImageImporter/1.0'},
                        extensions={'sni_hostname':host}) as response:
                    if response.is_redirect:
                        location = response.headers.get('location')
                        if not location:
                            raise AssetError('Image redirect has no destination')
                        url = urljoin(str(url), location)
                        continue
                    if response.status_code != 200:
                        raise AssetError(f'Image download failed (HTTP {response.status_code})')
                    try:
                        length = int(response.headers.get('content-length', 0))
                    except ValueError:
                        raise AssetError('Image response has an invalid size header') from None
                    if length > limit:
                        raise AssetError('Image exceeds 2 MB input limit')
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise AssetError('Image exceeds 2 MB input limit')
                        if time.monotonic() > deadline:
                            raise AssetError('Image download exceeded time limit')
                    return bytes(data)
        raise AssetError('Image download exceeded redirect limit')
    except httpx.HTTPError:
        raise AssetError('Image download network or TLS failure') from None

class AssetResolver:
    def __init__(self, root, config):
        self.root = Path(root)

    def read(self, spec):
        if spec.url:
            return download(str(spec.url))
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
        if len(raw) > MAX_IMAGE_BYTES:
            raise AssetError('Image exceeds 2 MB input limit')
        return raw

"""Validate and persist approved images with high-quality local derivatives."""
import hashlib
import io
import os
import re
import tempfile
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.engine import make_url
from .assets import AssetResolver, AssetError

DEFAULT_IMAGE = '/static/brand/shipbytes-default-transparent.png'

def media_root(config):
    return Path(config.media_directory) if config.media_directory else Path(make_url(config.database_url).database).resolve().parent / 'media'

def store_image(root, spec, config, issue_slug, *, resolver=None, collection="issues"):
    """Return local web paths, or None for a missing/invalid legacy optional image."""
    resolver = resolver or AssetResolver(root, config)
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', issue_slug):
        raise ValueError('Invalid issue slug for media storage')
    try:
        raw = resolver.read(spec)
        checksum = hashlib.sha256(raw).hexdigest()
        if spec.sha256 and spec.sha256 != checksum:
            raise AssetError('Image SHA-256 does not match the publication reference')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=['JPEG', 'PNG', 'WEBP']) as original:
                if original.width * original.height > 4_000_000 or getattr(original, 'n_frames', 1) != 1:
                    raise AssetError("Image dimensions or animation are not supported")
                source_extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[original.format]
                original.load()
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((1200, 630), Image.Resampling.LANCZOS)
                rgba = oriented.convert('RGBA')
                canvas = Image.new('RGB', (1200, 630), 'white')
                canvas.paste(rgba, ((1200-rgba.width)//2, (630-rgba.height)//2), rgba)
        variants = []
        for label, size in [('cover-1200.jpg', (1200, 630)), ('cover-600.jpg', (600, 315))]:
            resized = canvas.resize(size, Image.Resampling.LANCZOS)
            for quality in (92, 88, 85):
                encoded = io.BytesIO()
                resized.save(encoded, format='JPEG', quality=quality, optimize=True, subsampling=0)
                content = encoded.getvalue()
                if len(content) <= 250_000:
                    break
            variants.append((label, content))
    except AssetError:
        if spec.url or spec.sha256:
            raise
        return None
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        if spec.url or spec.sha256:
            raise AssetError('Required image is unavailable, corrupt or unsupported') from None
        return None
    # Storage errors are actual publication failures, not invalid-image fallbacks.
    if collection not in ('issues', 'stories'):
        raise ValueError('Invalid media collection')
    suffix = f'{collection}/{issue_slug}'
    if spec.url:
        suffix += '/' + checksum
    directory = media_root(config) / suffix
    directory.mkdir(parents=True, exist_ok=True)
    urls = []
    for name, content in [*variants, (f'source.{source_extension}', raw)]:
        destination = directory / name
        if not destination.exists() or destination.read_bytes() != content:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
                try:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    os.replace(temporary.name, destination)
                finally:
                    Path(temporary.name).unlink(missing_ok=True)
        if name.startswith('cover-'):
            urls.append(f'/media/{suffix}/{name}')
    return tuple(urls)

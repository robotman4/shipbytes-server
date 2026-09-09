"""Local publication image ingestion. No remote downloads during publishing."""
import io
import os
import re
import tempfile
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.engine import make_url

DEFAULT_IMAGE = '/static/brand/shipbytes-default.jpg'

def media_root(config):
    return Path(config.media_directory) if config.media_directory else Path(make_url(config.database_url).database).resolve().parent / 'media'

def store_image(root, spec, config, issue_slug):
    """Return immutable web paths, or None for a missing/invalid optional image."""
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', issue_slug):
        raise ValueError('Invalid issue slug for media storage')
    path = Path(root) / spec.path
    try:
        if not path.resolve().is_relative_to(Path(root).resolve()):
            return None
        relative = path.relative_to(root)
        if any((Path(root) / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
            return None
        with path.open('rb') as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=['JPEG', 'PNG', 'WEBP']) as original:
                if original.width * original.height > 4_000_000 or getattr(original, 'n_frames', 1) != 1:
                    return None
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
            for quality in (82, 72, 62, 52, 42):
                encoded = io.BytesIO()
                resized.save(encoded, format='JPEG', quality=quality, optimize=True)
                content = encoded.getvalue()
                if len(content) <= 250_000:
                    break
            else:
                return None
            variants.append((label, content))
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    # Storage errors are actual publication failures, not invalid-image fallbacks.
    directory = media_root(config) / 'issues' / issue_slug
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
            urls.append(f'/media/issues/{issue_slug}/{name}')
    return tuple(urls)

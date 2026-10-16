"""Processing of uploaded images with Pillow.

Every uploaded image is decoded and written again, so nothing the client sent
reaches the disk unchanged:

- the content must be an image Pillow can open (the extension alone proves
  nothing), and not a decompression bomb;
- EXIF orientation is applied to the pixels, then all metadata is dropped
  (EXIF with GPS location, XMP, comments, text chunks); only an ICC colour
  profile is kept, since without it colours can be wrong;
- the longest side is reduced to ``max_side`` pixels;
- JPEG is saved at quality 85 (progressive), PNG optimized, WebP at quality
  85; BMP, TIFF and ICO become PNG. Animated GIF and WebP keep all their
  frames (each frame resized), without metadata.

Variants (`store_upload()`, `write_variants()`, `manage.py backfill_image_variants`):
the re-encoded file stays the canonical upload (`/upfiles/<name>`); next to
it, in `UPFILES_FOLDER/_variants/` (a folder no upload can write to), every
still image also gets AVIF (when this Pillow can write it) and WebP copies at
the widths 640 and 1280 (those smaller than the image) and at full width,
named `<name>.<width>w.<avif|webp>`, plus `<name>.json` with the image's
size and the widths written per format. A full-width copy bigger than the
canonical file is left out. Animated images only get the JSON (their size).
`pictures()` (called by render_markdown()) turns post images that point to
an upload with a JSON file into `<picture>` elements with those sources and
width/height attributes (no layout shift); external images are left alone.
Variants are written once and never changed (served as immutable).
"""
import io
import json
import logging
import os
import re
import warnings
from html import escape, unescape
from urllib.parse import quote, unquote

from PIL import Image, ImageOps, ImageSequence, features

# Larger images are refused: decoding them would take too much memory.
# 50 megapixels holds the photos of 48 MP phone cameras.
MAX_PIXELS = 50 * 1000 * 1000
# Animations: all frames together.
MAX_ANIMATION_PIXELS = 200 * 1000 * 1000

INPUT_FORMATS = {'JPEG', 'MPO', 'PNG', 'GIF', 'WEBP', 'BMP', 'TIFF', 'ICO'}
OUTPUT_FORMATS = {'JPEG': 'JPEG', 'MPO': 'JPEG', 'PNG': 'PNG', 'GIF': 'GIF', 'WEBP': 'WEBP',
                  'BMP': 'PNG', 'TIFF': 'PNG', 'ICO': 'PNG'}
EXTENSIONS = {'JPEG': ('.jpg', '.jpeg'), 'PNG': ('.png',), 'GIF': ('.gif',), 'WEBP': ('.webp',), 'ICO': ('.ico',)}


class NotAnImage(Exception):
    """The file is not an image we accept."""


class ImageTooLarge(Exception):
    """The image has too many pixels (possibly a decompression bomb)."""


def _mode_for(img, output):
    has_alpha = 'A' in img.getbands() or 'transparency' in img.info
    if output == 'JPEG':
        if img.mode in ('L', 'RGB', 'CMYK'):
            return img.mode
        return 'RGB'
    if output == 'PNG':
        if img.mode in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I;16'):
            return img.mode
        return 'RGBA' if has_alpha else 'RGB'
    if output == 'WEBP':
        return 'RGBA' if has_alpha else 'RGB'
    # GIF: Pillow quantizes RGB(A) to a palette on save.
    if img.mode in ('P', 'L'):
        return img.mode
    return 'RGBA' if has_alpha else 'RGB'


def _shrink(img, max_side):
    if not max_side or max(img.size) <= max_side:
        return img
    if img.mode in ('1', 'P'):
        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


def _clean(img):
    """Drops everything but the pixels (and the ICC profile)."""
    icc = img.info.get('icc_profile')
    img.info = {}
    return icc


def process_image(f, filename, max_side=None, keep_ico=False):
    """Reads an uploaded image file and returns (bytes, new file name).

    The file name keeps its stem; the extension follows the stored format.
    Raises NotAnImage or ImageTooLarge.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            img = Image.open(f)
            if img.format not in INPUT_FORMATS:
                raise NotAnImage(img.format)
            if img.width * img.height > MAX_PIXELS:
                raise ImageTooLarge()

            if keep_ico and img.format == 'ICO':
                # Icons carry no EXIF; checked to be readable, stored as they are.
                img.load()
                f.seek(0)
                return f.read(), filename

            stem, ext = os.path.splitext(filename)
            output = OUTPUT_FORMATS[img.format]
            frames = getattr(img, 'n_frames', 1) if output in ('GIF', 'WEBP') else 1
            if frames > 1 and img.width * img.height * frames > MAX_ANIMATION_PIXELS:
                raise ImageTooLarge()

            out = io.BytesIO()
            if frames > 1:
                _save_animation(img, out, output, max_side)
            else:
                _save_still(img, out, output, max_side)
    except (NotAnImage, ImageTooLarge):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageTooLarge()
    except Exception as e:  # Pillow raises many kinds for unreadable data
        raise NotAnImage(str(e))

    extensions = EXTENSIONS[output]
    if ext.lower() not in extensions:
        ext = extensions[0]
    return out.getvalue(), stem + ext


def _save_still(img, out, output, max_side):
    if img.format == 'ICO':
        # The largest size the icon holds.
        img.size = max(img.info.get('sizes') or [img.size])
    img.load()
    img = ImageOps.exif_transpose(img)
    img = _shrink(img, max_side)
    mode = _mode_for(img, output)
    if img.mode != mode:
        img = img.convert(mode)
    icc = _clean(img)
    options = {'icc_profile': icc} if icc and output != 'GIF' else {}
    if output == 'JPEG':
        img.save(out, 'JPEG', quality=85, progressive=True, optimize=True, **options)
    elif output == 'PNG':
        img.save(out, 'PNG', optimize=True, **options)
    elif output == 'WEBP':
        img.save(out, 'WEBP', quality=85, **options)
    else:
        img.save(out, 'GIF', optimize=True)


def _save_animation(img, out, output, max_side):
    loop = img.info.get('loop', 0)
    icc = img.info.get('icc_profile')
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        durations.append(frame.info.get('duration', 100))
        frame = frame.convert('RGBA')
        frame = _shrink(frame, max_side)
        _clean(frame)
        frames.append(frame)
    options = dict(save_all=True, append_images=frames[1:], duration=durations, loop=loop)
    if output == 'WEBP':
        if icc:
            options['icc_profile'] = icc
        frames[0].save(out, 'WEBP', quality=85, **options)
    else:
        frames[0].save(out, 'GIF', optimize=True, disposal=2, **options)


# -- Modern formats and smaller widths ----------------------------------------

VARIANTS_DIR = '_variants'
VARIANT_WIDTHS = (640, 1280)
# (extension, Pillow format, options, media type), best first: <picture> uses the first the browser knows.
VARIANT_FORMATS = (
    ('avif', 'AVIF', dict(quality=60), 'image/avif'),
    ('webp', 'WEBP', dict(quality=80, method=6), 'image/webp'),
)
# The widest a post image is shown on a large screen (CSS px), for `sizes`.
MAX_DISPLAY_WIDTH = 760

logger = logging.getLogger('forum.images')


def variant_formats():
    """The VARIANT_FORMATS this Pillow can write (AVIF needs Pillow 11.2+ built with libavif)."""
    return [f for f in VARIANT_FORMATS if features.check(f[0])]


def variant_name(name, width, ext):
    """Path, relative to UPFILES_FOLDER, of the `ext` copy of upload `name` at `width` pixels."""
    return '%s/%s.%dw.%s' % (VARIANTS_DIR, name, width, ext)


def meta_name(name):
    return '%s/%s.json' % (VARIANTS_DIR, name)


def is_upload_name(name):
    """A canonical upload's file name: no folder (variants live in one), no hidden file."""
    return bool(name) and '/' not in name and '\\' not in name and not name.startswith('.') and '\0' not in name


def read_meta(folder, name):
    """The metadata written by write_variants() for upload `name`, or None."""
    if not is_upload_name(name):
        return None
    try:
        with open(os.path.join(folder, meta_name(name)), encoding='utf-8') as f:
            meta = json.load(f)
        width, height, sources = int(meta['width']), int(meta['height']), meta.get('sources') or {}
        known = {f[0] for f in VARIANT_FORMATS}
        sources = {ext: sorted(int(w) for w in widths) for ext, widths in sources.items() if ext in known and widths}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {'width': width, 'height': height, 'sources': sources}


def _write_atomically(path, data):
    tmp = '%s.tmp%d' % (path, os.getpid())
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _complete(folder, name, meta):
    return all(os.path.isfile(os.path.join(folder, variant_name(name, width, ext)))
               for ext, widths in meta['sources'].items() for width in widths)


def write_variants(folder, name, dry_run=False):
    """Writes the AVIF/WebP copies and the JSON file of upload `name` in
    `folder` (see the module docstring). Idempotent: an upload whose JSON and
    copies exist is left alone.

    Returns (meta, written): written is False when everything was there
    already (or, with dry_run, nothing would be written). Returns (None, False)
    for a file that is not an image Pillow can read safely.
    """
    path = os.path.join(folder, name)
    if not is_upload_name(name) or not os.path.isfile(path):
        return None, False
    meta = read_meta(folder, name)
    if meta is not None and _complete(folder, name, meta):
        return meta, False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as img:
                if img.format not in INPUT_FORMATS or img.width * img.height > MAX_PIXELS:
                    return None, False
                if dry_run:
                    return {'width': img.width, 'height': img.height, 'sources': {}}, True
                meta = _make_variants(img, folder, name, os.path.getsize(path))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None, False
    except Exception:  # Pillow raises many kinds for unreadable data
        logger.warning('Could not make image variants of %s', name, exc_info=True)
        return None, False
    _write_atomically(os.path.join(folder, meta_name(name)), json.dumps(meta, sort_keys=True).encode())
    return meta, True


def _make_variants(img, folder, name, canonical_size):
    os.makedirs(os.path.join(folder, VARIANTS_DIR), exist_ok=True)
    if getattr(img, 'n_frames', 1) > 1:
        return {'width': img.width, 'height': img.height, 'sources': {}}
    if img.format == 'ICO':
        img.size = max(img.info.get('sizes') or [img.size])
    img.load()
    # Uploads from before the re-encoding may still carry an EXIF orientation;
    # browsers apply it to the canonical file, so the copies are turned too.
    img = ImageOps.exif_transpose(img)
    icc = img.info.get('icc_profile')
    has_alpha = 'A' in img.getbands() or 'transparency' in img.info
    img = img.convert('RGBA' if has_alpha else 'RGB')
    width, height = img.size
    targets = sorted({w for w in VARIANT_WIDTHS if w < width} | {width})
    sources = {}
    for ext, fmt, options, _type in variant_formats():
        widths = []
        for target in targets:
            out_path = os.path.join(folder, variant_name(name, target, ext))
            if not os.path.isfile(out_path):
                if target == width:
                    resized = img
                else:
                    resized = img.resize((target, max(1, round(height * target / width))), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                resized.save(out, fmt, **dict(options, **({'icc_profile': icc} if icc else {})))
                data = out.getvalue()
                if target == width and len(data) >= canonical_size:
                    continue  # no smaller than the canonical file: not worth a source
                _write_atomically(out_path, data)
            widths.append(target)
        if widths:
            sources[ext] = widths
    return {'width': width, 'height': height, 'sources': sources}


def store_upload(f, filename, max_side=None):
    """Re-encodes an uploaded image (process_image), stores it in
    UPFILES_FOLDER under a new name, writes its variants and returns its URL
    (UPFILES_ALIAS + name). Raises NotAnImage or ImageTooLarge; a failure to
    write the variants is logged and leaves the plain upload."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import FileSystemStorage
    from forum import settings

    if max_side is None:
        max_side = int(settings.UPLOAD_MAX_IMAGE_SIDE)
    data, name = process_image(f, '_'.join(filename.split()), max_side=max_side)
    folder = str(settings.UPFILES_FOLDER)
    stored = FileSystemStorage(folder, str(settings.UPFILES_ALIAS)).save(name, ContentFile(data))
    try:
        write_variants(folder, stored)
    except Exception:
        logger.exception('Could not make image variants of %s', stored)
    return str(settings.UPFILES_ALIAS) + stored


IMG_ELEMENT = re.compile(r'<img\s[^>]*>', re.IGNORECASE)
ATTRIBUTE = re.compile(r'\s([a-zA-Z-]+)="([^"]*)"')


def _upload_name(src, prefixes):
    for prefix in prefixes:
        if prefix and src.startswith(prefix):
            name = unquote(src[len(prefix):].split('?')[0].split('#')[0])
            return name if is_upload_name(name) else None
    return None


def pictures(html):
    """Wraps the images of sanitized post HTML that show an upload with
    variants in <picture> (AVIF/WebP sources at several widths) and gives
    them width/height attributes from the upload's JSON file.

    Only for HTML that went through sanitize_html, which never keeps
    <picture>, <source> or srcset from user input; images of other sites
    and uploads without a JSON file are left as they are."""
    from forum import settings

    alias = str(settings.UPFILES_ALIAS)
    if not alias or alias not in html:
        return html
    folder = str(settings.UPFILES_FOLDER)
    app_url = str(settings.APP_URL or '').rstrip('/')
    prefixes = (alias, app_url + alias) if alias.startswith('/') and app_url else (alias,)

    def replace(match):
        tag = match.group(0)
        attrs = dict((key.lower(), value) for key, value in ATTRIBUTE.findall(tag))
        name = _upload_name(unescape(attrs.get('src', '')), prefixes)
        meta = name and read_meta(folder, name)
        if not meta:
            return tag
        width, height = meta['width'], meta['height']
        shown = width
        if 'width' in attrs or 'height' in attrs:
            if attrs.get('width', '').isdigit():
                shown = min(width, int(attrs['width']) or width)
        else:
            tag = '<img width="%d" height="%d"%s' % (width, height, tag[4:])
        if not meta['sources']:
            return tag
        sizes = '(max-width: 640px) 100vw, %dpx' % min(shown, MAX_DISPLAY_WIDTH)
        sources = []
        for ext, _fmt, _options, media_type in VARIANT_FORMATS:
            widths = meta['sources'].get(ext)
            if widths:
                srcset = ', '.join('%s%s %dw' % (alias, quote(variant_name(name, w, ext)), w) for w in widths)
                sources.append('<source type="%s" srcset="%s" sizes="%s">' % (media_type, escape(srcset), sizes))
        return '<picture>%s%s</picture>' % (''.join(sources), tag)

    return IMG_ELEMENT.sub(replace, html)

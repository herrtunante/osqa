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
"""
import io
import os
import warnings

from PIL import Image, ImageOps, ImageSequence

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

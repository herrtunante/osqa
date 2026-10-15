"""Visual regression: screenshots of key pages compared with committed
baselines (e2e/visual/baselines/<name>.png). Used by e2e/test_visual.py.

A screenshot matches its baseline when at most MAX_DIFF_RATIO of its pixels
differ by more than TOLERANCE in any colour channel. On a mismatch the
actual screenshot, the baseline and a diff image (differing pixels in red
over a faded copy of the page) are written to tmp/e2e/visual/.

Baselines depend on the machine that renders them (the fonts behind
system-ui, the Chromium version), so environment.json records a fingerprint
of the environment that made them; on another environment the comparison is
skipped with a message unless E2E_VISUAL_STRICT=1.

    E2E_UPDATE_BASELINES=1   write the screenshots as the new baselines
    E2E_VISUAL_STRICT=1      compare even on a different environment
"""
import io
import json
import os
import platform

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINES = os.path.join(HERE, 'baselines')
ENVIRONMENT_FILE = os.path.join(BASELINES, 'environment.json')
OUTPUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'tmp', 'e2e', 'visual')

TOLERANCE = 24          # per channel, 0-255: anti-aliasing noise
MAX_DIFF_RATIO = 0.002  # 0.2% of the pixels


def updating():
    return os.environ.get('E2E_UPDATE_BASELINES') == '1'


def strict():
    return os.environ.get('E2E_VISUAL_STRICT') == '1'


def baseline_path(name):
    return os.path.join(BASELINES, name + '.png')


def save_baseline(name, png):
    os.makedirs(BASELINES, exist_ok=True)
    Image.open(io.BytesIO(png)).convert('RGB').save(baseline_path(name), optimize=True)


def compare(name, png):
    """Compares a screenshot (PNG bytes) with its baseline. Returns None when
    they match, else a message naming the files written to OUTPUT."""
    expected = Image.open(baseline_path(name)).convert('RGB')
    actual = Image.open(io.BytesIO(png)).convert('RGB')
    width, height = max(expected.width, actual.width), max(expected.height, actual.height)
    # Pixels outside one of the images (a size change) count as different.
    canvas_e = Image.new('RGB', (width, height), (255, 0, 255))
    canvas_e.paste(expected)
    canvas_a = Image.new('RGB', (width, height), (0, 255, 0))
    canvas_a.paste(actual)
    channels = ImageChops.difference(canvas_e, canvas_a).split()
    over = [c.point(lambda v: 255 if v > TOLERANCE else 0) for c in channels]
    mask = ImageChops.lighter(ImageChops.lighter(over[0], over[1]), over[2])
    differing = mask.histogram()[255]
    ratio = differing / float(width * height)
    if ratio <= MAX_DIFF_RATIO and expected.size == actual.size:
        return None
    os.makedirs(OUTPUT, exist_ok=True)
    faded = Image.blend(canvas_a.convert('L').convert('RGB'), Image.new('RGB', (width, height), 'white'), 0.6)
    diff = Image.composite(Image.new('RGB', (width, height), (230, 0, 0)), faded, mask)
    files = {}
    for kind, image in (('actual', actual), ('expected', expected), ('diff', diff)):
        files[kind] = os.path.join(OUTPUT, '%s-%s.png' % (name, kind))
        image.save(files[kind])
    size = '' if expected.size == actual.size else ' size %dx%d instead of %dx%d;' % (
        actual.width, actual.height, expected.width, expected.height)
    return ('%s differs from its baseline:%s %.3f%% of the pixels differ (limit %.1f%%). See %s. If the change '
            'is intended, update the baselines with E2E_UPDATE_BASELINES=1 (e2e/README.md).' % (
                name, size, ratio * 100, MAX_DIFF_RATIO * 100, files['diff']))


# -- environment ------------------------------------------------------------

# Widths of a sample text in the skin's font stacks (osqa.css --font and
# --mono, written out so every page measures the same): they change when
# system-ui or the monospace font resolves to another font.
SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace'
MEASURE_FONTS = '''([sans, mono]) => {
    const ctx = document.createElement('canvas').getContext('2d');
    const width = family => { ctx.font = '40px ' + family; return Math.round(ctx.measureText(
        'Hamburgefonstiv 0123456789 How do I export?').width * 10) / 10; };
    return {sans: width(sans), mono: width(mono)};
}'''


def environment(browser, page):
    return {'os': platform.system(), 'chromium': browser.version.split('.')[0], 'fonts': page.evaluate(MEASURE_FONTS, [SANS, MONO])}


def recorded_environment():
    try:
        with open(ENVIRONMENT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_environment(env):
    os.makedirs(BASELINES, exist_ok=True)
    with open(ENVIRONMENT_FILE, 'w') as f:
        json.dump(env, f, indent=2, sort_keys=True)
        f.write('\n')


# -- deterministic pages ----------------------------------------------------

# Injected into every page: no animations, transitions or blinking caret,
# no scroll bars, and jQuery effects finish at once.
FREEZE_CSS = '''*, *::before, *::after {
    animation: none !important; transition: none !important; caret-color: transparent !important;
    scroll-behavior: auto !important;
}
html { scrollbar-width: none !important; }
::-webkit-scrollbar { display: none !important; }'''

FREEZE_SCRIPT = '''document.addEventListener('DOMContentLoaded', () => {
    const style = document.createElement('style');
    style.id = 'visual-freeze';
    style.textContent = %s;
    document.head.appendChild(style);
    if (window.jQuery) jQuery.fx.off = true;
});''' % json.dumps(FREEZE_CSS)


def placeholder_avatar():
    """A flat grey square served for every Gravatar image."""
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), (154, 165, 177)).save(buffer, 'PNG')
    return buffer.getvalue()

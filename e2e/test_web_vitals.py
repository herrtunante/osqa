"""Core Web Vitals budgets on a throttled phone (see e2e/web_vitals.py).

The main pages are loaded cold (empty browser cache) on a 390px phone over
slow 4G with the CPU slowed down 4x, and each must stay within the budgets
below. Timing budgets are generous (CI machines differ); layout shift is the
strict one, and request counts and transferred bytes catch a page that starts
loading something new.

    E2E_VITALS_REPORT=file   append the table of figures to `file`
"""
import io
import os
import shutil
import sys
import tempfile

from django.urls import reverse
from PIL import Image, ImageDraw

from forum import settings
from forum.utils import images

from . import web_vitals
from .base import E2ETestCase

# Every page. Timing budgets hold with a wide margin here (LCP at most about
# 2.1 s, TTFB 0.4 s, INP 60 ms); a page over a timing budget is measured once
# more and the better run counts, so a busy CI machine doesn't fail the test.
MAX_CLS = 0.05
MAX_LCP_MS = 2500
MAX_TTFB_MS = 1200
MAX_INP_MS = 200
TIMING = ('LCP', 'TTFB', 'INP')
# Per page: (requests, kilobytes transferred), about 20% above the figures
# measured; jQuery UI (75 kB) or highlight.js (43 kB) loaded where no field
# or code block needs them, or full-size images on a phone, go over.
MAX_REQUESTS_AND_KB = {
    'home': (24, 210),
    'questions': (24, 210),
    'question': (36, 470),
    'tag': (24, 210),
    'profile': (24, 210),
    'search': (25, 210),
    'ask': (32, 330),
    'signin': (25, 200),
}

QUESTION_BODY = '''My survey export shows the plot map below, but the dates in the CSV are text:

![Plot map of the survey area](%s)

I read it in R with:

```r
data <- read.csv("export.csv")
str(data$survey_date)
```

and get `chr` instead of `Date`. The second picture shows the settings:

![Export settings](%s)
'''


def photo(width, height):
    """A picture with enough detail to compress like a photo."""
    img = Image.effect_mandelbrot((width, height), (-2.2, -1.2, 1.0, 1.2), 60).convert('RGB')
    draw = ImageDraw.Draw(img)
    for i in range(0, width, 37):
        draw.line((i, 0, width - i, height), fill=(i % 255, 120, 255 - i % 255), width=3)
    return img


class WebVitalsTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.set_setting(settings.UPFILES_FOLDER, self.folder)
        self.alice = self.create_user('alice', reputation=900, real_name='Alice Martin', location='Rome',
                                      about='Forest inventory with **Collect**.')
        self.bob = self.create_user('bob', reputation=1300)
        map_url = self.upload(photo(1600, 1000), 'plot map.jpg')
        settings_url = self.upload(photo(900, 600), 'settings.png', 'PNG')
        self.question = self.create_question(self.alice, 'How do I read Collect CSV dates as dates in R?',
                                             tags='collect export r', body=QUESTION_BODY % (map_url, settings_url))
        for i in range(3):
            answer = self.create_answer(self.bob, self.question,
                                        'Answer %d: use `colClasses`:\n\n```r\nread.csv("x.csv", colClasses = '
                                        'c(survey_date = "Date"))\n```\n\nIt works with any ISO date.' % i)
            self.create_comment(self.alice, answer, 'Thanks @bob, colClasses did it (%d).' % i)
        for i in range(14):
            self.create_question(self.bob if i % 2 else self.alice, 'Question number %d about exporting data' % i,
                                 tags='collect export' if i % 3 else 'sepal')

    def upload(self, img, name, fmt='JPEG'):
        """Stores `img` as the editor's upload would, and returns its URL."""
        out = io.BytesIO()
        img.save(out, fmt, quality=95)
        out.seek(0)
        return images.store_upload(out, name)

    def pages(self):
        return [
            ('home', '/'),
            ('questions', reverse('questions')),
            ('question', self.question.get_absolute_url()),
            ('tag', reverse('tag_questions', kwargs={'tag': 'collect'})),
            ('profile', self.alice.get_profile_url()),
            ('search', reverse('search') + '?q=dates'),
            ('signin', reverse('auth_signin')),
        ]

    def problems(self, name, f):
        requests, kb = MAX_REQUESTS_AND_KB[name]
        found = []
        for label, value, limit in (('CLS', f['cls'], MAX_CLS), ('LCP', f['lcp'], MAX_LCP_MS),
                                    ('TTFB', f['ttfb'], MAX_TTFB_MS), ('INP', f['inp'], MAX_INP_MS),
                                    ('requests', f['requests'], requests), ('kB', f['bytes'] / 1024.0, kb)):
            if value > limit:
                found.append((label, '%s: %s %.3f > %s%s' % (name, label, value, limit, ' (shifted: %s)' % ', '.join(
                    f['shift_sources']) if label == 'CLS' else '')))
        return found

    def measure_all(self, pages, user=None, title=''):
        results, problems = [], []
        for name, path in pages:
            figures = web_vitals.measure(self, web_vitals.phone_page(self, user), path)
            found = self.problems(name, figures)
            if found and all(label in TIMING for label, _ in found):
                again = web_vitals.measure(self, web_vitals.phone_page(self, user), path)
                if len(self.problems(name, again)) < len(found):
                    figures, found = again, self.problems(name, again)
            results.append((name, figures))
            problems.extend(message for _, message in found)
        table = web_vitals.report(results, title)
        if os.environ.get('E2E_VITALS_REPORT'):
            sys.stderr.write(table)
        self.assertEqual(problems, [], table)

    def test_anonymous_pages(self):
        self.measure_all(self.pages(), title='Anonymous visitor, phone, slow 4G, CPU 4x')

    def test_member_pages(self):
        self.measure_all([('question', self.question.get_absolute_url()), ('ask', reverse('ask'))], self.alice,
                         title='Member, phone, slow 4G, CPU 4x')

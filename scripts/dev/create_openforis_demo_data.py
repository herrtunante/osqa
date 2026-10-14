#!/usr/bin/env python
"""
Fill a fresh database with demo content styled after https://openforis.support:
invented users, questions, answers, comments and votes about the Open Foris
tools, plus the site branding (title, logo, favicon, colours, sidebar, footer).

All people and posts are made up. Content is created through the regular
actions, so reputation, counters, activity and badges are consistent.

Run from the repository root against an empty database:
    python manage.py migrate
    python scripts/dev/create_openforis_demo_data.py
"""
import os
import sys
import random
import urllib.request
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from django.conf import settings as django_settings
if not django_settings.DEBUG:
    sys.exit('Refusing to run with DEBUG = False: this creates users with known passwords.')

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory

from forum import settings, REQUEST_HOLDER
from forum.models import User, Node, NodeRevision, Tag, TagSynonym, Vote, Award, Action, ActionRepute
from forum.actions import (UserJoinsAction, EmailValidationAction, AskAction, AnswerAction, CommentAction,
                           VoteUpAction, VoteDownAction, AcceptAnswerAction)

random.seed(2025)

NOW = datetime.now()
IP = '127.0.0.1'
LIVE_SITE = 'https://openforis.support'
ADMIN = ('alfonso', 'alfonso')
PASSWORD = 'password123'

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

ASSETS = {
    'of_support_logo.jpg': LIVE_SITE + '/upfiles/of_support_logo.jpg',
    'favicon_support_32.ico': LIVE_SITE + '/upfiles/favicon_support_32.ico',
}

# Palette taken from the Open Foris Support logo and the live site's links
# (#996666); it only overrides the colour variables of the default skin.
CUSTOM_CSS = """/* Open Foris Support colours */
:root {
    --brand: #8f5a5a;
    --brand-dark: #6f4242;
    --brand-soft: #f7efef;
    --accent: #17798f;
    --accent-dark: #105d6e;
    --accent-soft: #e5f3f6;
    --info-border: #e6d5d5;
    --border-strong: #d9c6c6;
}

@media (prefers-color-scheme: dark) {
    :root[data-color-scheme="auto"] {
        --brand: #dcaaaa;
        --brand-dark: #efcdcd;
        --brand-soft: #3a2727;
        --accent: #17798f;
        --accent-dark: #2a9bb4;
        --accent-soft: #12303a;
        --info-border: #553a3a;
        --border-strong: #5e4a4a;
        --button-bg: #17798f;
        --button-bg-hover: #2190a9;
    }
}

#logo img {
    max-height: 56px;
    border-radius: 6px;
}

#custom_header .of-topbar {
    padding: 6px 0;
    font-size: 13px;
}
"""

CUSTOM_HEADER = '<div class="of-topbar"><a href="https://www.openforis.org">&larr; back to openforis.org</a></div>'

CUSTOM_FOOTER = ('<p>Open Foris Support &middot; '
                 '<a href="https://openforis.org/termsofservicesupport">Terms of Service</a></p>')

SIDEBAR_UPPER = """<p>Download our tools from <a href="https://www.openforis.org"><strong>Open Foris</strong></a></p>
<p>Our <a href="https://github.com/openforis"><strong>GitHub page</strong></a></p>"""

BRANDING = [
    ('APP_TITLE', 'Open Foris Support - Q&A Forum'),
    ('APP_SHORT_NAME', 'Open Foris Support'),
    ('APP_KEYWORDS', 'Open Foris;Arena;Arena Mobile;Collect;Collect Earth;Collect Earth Online;Earth Map;'
                     'SEPAL;Whisp;Google Earth Engine'),
    ('APP_DESCRIPTION', 'Questions and answers about the Open Foris tools for forest and land monitoring.'),
    ('APP_COPYRIGHT', 'Open Foris Support. Content licensed under Creative Commons.'),
    ('APP_INTRO', '<p>Ask and answer questions, make the Open Foris better!</p>'),
    ('APP_LOGO', '/upfiles/of_support_logo.jpg'),
    ('APP_FAVICON', '/upfiles/favicon_support_32.ico'),
    ('USE_CUSTOM_CSS', True),
    ('CUSTOM_CSS', CUSTOM_CSS),
    ('USE_CUSTOM_HEADER', True),
    ('CUSTOM_HEADER', CUSTOM_HEADER),
    ('CUSTOM_HEADER_RENDER_MODE', 'html'),
    ('USE_CUSTOM_FOOTER', True),
    ('CUSTOM_FOOTER', CUSTOM_FOOTER),
    ('CUSTOM_FOOTER_RENDER_MODE', 'html'),
    ('CUSTOM_FOOTER_MODE', 'above'),
    ('SIDEBAR_UPPER_SHOW', True),
    ('SIDEBAR_UPPER_TEXT', SIDEBAR_UPPER),
    ('SIDEBAR_UPPER_RENDER_MODE', 'html'),
]

# ---------------------------------------------------------------------------
# People (all invented)
# ---------------------------------------------------------------------------

# username, real name, location, about, days since joining
USERS = [
    ('alfonso', 'Alfonso (admin)', 'Rome, Italy', 'Site administrator.', 900),
    ('marta_of', 'Marta Lindqvist', 'Rome, Italy',
     'Works on the Arena and Arena Mobile support desk. Ask me about survey design.', 880),
    ('stefan_dev', 'Stefan Brandt', 'Helsinki, Finland',
     'Developer. Mostly data processing chains, R scripts and the occasional bug hunt.', 860),
    ('giulia_rs', 'Giulia Moretti', 'Rome, Italy',
     'Remote sensing: Collect Earth, Earth Map and Google Earth Engine scripts.', 850),
    ('kwame_nfi', 'Kwame Boateng', 'Kumasi, Ghana',
     'National forest inventory field coordinator. Sixty tablets and counting.', 610),
    ('ana_sig', 'Ana Paula Ferreira', 'Belem, Brazil', 'GIS analyst working on land-use change assessments.', 540),
    ('thanh_forest', 'Nguyen Van Thanh', 'Hanoi, Vietnam', 'Forest inventory data manager.', 480),
    ('lucia_redd', 'Lucia Quispe', 'Lima, Peru', 'REDD+ MRV team. Sample-based area estimation.', 455),
    ('ibrahim_gis', 'Ibrahim Diallo', 'Bamako, Mali', 'Drylands monitoring and restoration mapping.', 400),
    ('priya_env', 'Priya Nair', 'Dehradun, India', 'Forestry researcher. Tree volume models and biomass.', 350),
    ('tomasz_k', 'Tomasz Kowalczyk', 'Krakow, Poland', 'University lecturer using Open Foris tools in class.', 300),
    ('sione_pacific', 'Sione Tupou', "Nuku'alofa, Tonga", 'Small-island inventories, big-island ambitions.', 260),
    ('carla_coop', 'Carla Mendez', 'San Jose, Costa Rica', 'Supply-chain traceability for a coffee cooperative.', 190),
    ('henrik_s', 'Henrik Solberg', 'Oslo, Norway', 'Consultant. Due diligence and deforestation-free sourcing.', 150),
    ('amina_field', 'Amina Yusuf', 'Dodoma, Tanzania', 'Field crew leader.', 95),
    ('newbie_2025', '', '', '', 20),
]

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

TAG_DESCRIPTIONS = {
    'arena': 'Open Foris Arena: the web platform for designing surveys, managing data and running analyses.',
    'arena-mobile': 'Arena Mobile: the Android/iOS app for collecting field data for Arena surveys.',
    'collect': 'Open Foris Collect: the desktop/server tool for survey design and data management.',
    'collectmobile': 'Collect Mobile: the Android data-collection app that works with Collect surveys.',
    'collectearth': 'Collect Earth: augmented visual interpretation on top of Google Earth.',
    'collectearthonline': 'Collect Earth Online: browser-based visual interpretation of sample plots.',
    'earthmap': 'Earth Map: point-and-click access to Google Earth Engine datasets and statistics.',
    'sepal': 'SEPAL: cloud platform for processing and analysing satellite imagery.',
    'whisp': 'Whisp: convergence-of-evidence checks for deforestation risk on plots of land.',
    'calc': 'Open Foris Calc and the Arena data processing chain: R-based analysis of inventory data.',
    'saiku': 'Saiku: the pivot-table analysis tool bundled with Collect Earth and Collect.',
    'google-earth-engine': 'Questions about Google Earth Engine accounts, quotas and scripts used by the tools.',
    'validation': 'Validation rules, required fields and data-quality checks in survey forms.',
    'taxonomy': 'Species lists: importing, exporting and using taxonomies in surveys.',
    'export': 'Exporting data to CSV, Excel, R or other systems.',
    'gps': 'Coordinates, GPS receivers, accuracy and coordinate reference systems.',
    'installation': 'Installing and updating the tools on Windows, macOS, Linux and Android.',
}

TAG_SYNONYMS = {
    'collect-mobile': 'collectmobile',
    'collect-earth': 'collectearth',
    'ceo': 'collectearthonline',
    'earth-map': 'earthmap',
    'gee': 'google-earth-engine',
}

# ---------------------------------------------------------------------------
# Questions. Each: author, days ago, title, tags, body, answers, comments.
# answers: (author, hours after question, body, accepted, [comments])
# comments: (author, text)
# ---------------------------------------------------------------------------

Q = []

def question(author, days, title, tags, body, answers=(), comments=(), views=None):
    Q.append(dict(author=author, days=days, title=title, tags=tags, body=body.strip(),
                  answers=answers, comments=comments, views=views))

question('kwame_nfi', 3, 'Make a field required only when another answer is "yes"',
    'arena validation',
    """
In our plot form there is a boolean attribute `plot_accessible`. When it is **yes** the crew must fill in
`access_time_minutes`; when it is **no** the field should be hidden and not required.

Marking the attribute as *required* makes it mandatory in both cases. Is there a way to make it required
only in specific cases?
""",
    answers=[
        ('marta_of', 5, """
Yes. Both the *Required* and the *Relevant if* properties accept an expression instead of a fixed value.

1. Open the attribute in the form designer.
2. In **Relevant if** enter:

```
plot_accessible == true
```

3. Tick *Hide when not relevant*.
4. In **Required** switch from the checkbox to the expression editor and use the same condition.

A field that is not relevant is never validated, so strictly speaking step 4 is only needed if you want the
field visible but optional in the other case.
""", True, [('kwame_nfi', 'Worked. I had only set the validation rule and missed "relevant if". Thanks!')]),
        ('thanh_forest', 30, """
One addition: after changing relevance rules on a survey that already has records, run *Validation report*
once. Old records keep their previous validation state until they are re-validated.
""", False, []),
    ],
    comments=[('stefan_dev', 'Which version are you on? The expression editor for "required" is fairly recent.')],
    views=412)

question('ana_sig', 6, 'Planet monthly composites no longer show up in Collect Earth Online',
    'collectearthonline',
    """
Since last week the Planet NICFI monthly basemaps in my institution's imagery list load as blank tiles.
Other layers (Sentinel-2, Mapbox) are fine and the same project worked in September.

Was something changed in the latest update, and do I need a new API key?
""",
    answers=[
        ('giulia_rs', 9, """
The basemap programme changed its access terms, so keys issued under the old programme stop working once
they expire. Things to check:

- log in to your Planet account and confirm the key is still listed as active;
- in CEO go to *Institution > Imagery > edit* and paste the current key again;
- use the *monthly* template rather than the biannual one for dates after 2020-09.

If the key is valid and tiles are still blank, open the browser console (F12) and look at the tile
requests: a `401` means the key, a `404` means the mosaic name for that month does not exist.
""", True, [('ana_sig', 'It was a 401. New key fixed it.'),
            ('ibrahim_gis', 'Same here, thanks for the console tip.')]),
    ],
    views=655)

question('amina_field', 8, 'Using offline background maps in the Arena Mobile app',
    'arena-mobile gps',
    """
Most of our plots have no mobile coverage. Can crews see a background map (satellite or topographic) in
Arena Mobile while navigating to the plot, without an internet connection?
""",
    answers=[
        ('marta_of', 3, """
Yes, in two ways:

**Pre-cached online maps.** Open the map while you still have a connection and pan over the area at the
zoom levels you need. Tiles are cached and remain available offline for a while. Fine for a handful of
plots, not for a campaign.

**MBTiles files.** In *Settings > Map > Offline maps* you can load an `.mbtiles` file. Produce it in QGIS
(*Processing > Generate XYZ tiles (MBTiles)*), keep it under roughly 1 GB per file and copy it to the
tablet. This is what I would do for field campaigns.

Keep the maximum zoom at 16 or 17: every extra level multiplies the file size by about four.
""", False, [('amina_field', 'Is there a size limit for the file?'),
             ('marta_of', 'No hard limit, but very large files are slow to open on older tablets.')]),
        ('sione_pacific', 26, """
We did the MBTiles route for 14 islands. One file per island worked much better than a single national
file, and crews only needed to copy the one for their trip.
""", False, []),
    ],
    views=298)

question('tomasz_k', 11, 'Importing a .cep project into the newest Collect Earth Online',
    'collectearthonline collectearth',
    """
I have several Collect Earth desktop projects (`.cep` files) from previous courses. Is it possible to
import them into Collect Earth Online so students can work in the browser, or do I have to rebuild the
survey questions by hand?
""",
    answers=[
        ('giulia_rs', 20, """
There is no direct `.cep` import. The two tools store survey definitions differently. What does carry over:

- **the plots**: export the plot list from the `.cep` (it is a zip, the CSV is inside) and upload it as the
  plot design when creating the CEO project. You need the columns `LON`, `LAT` and `PLOTID`;
- **the legend**: recreate the questions once, then use *Create project from template* for every later
  course.

So it is manual the first time only.
""", False, [('tomasz_k', 'Unzipping the .cep was the missing piece. Thank you.')]),
    ],
    views=187)

question('priya_env', 14, 'Two unrelated forms on the same entity - is it possible?',
    'arena',
    """
Our *tree* entity is measured by two teams: one records dendrometric data (dbh, height), the other records
health and damage. I would like two separate forms so each team only sees its own attributes, but both
write to the same tree record.

Is that possible, or do I need two entities?
""",
    answers=[
        ('marta_of', 7, """
You can do it with one entity. A form page in Arena is just a layout: several pages can show attributes of
the same entity.

- keep a single `tree` entity;
- create two form pages, *Dendrometry* and *Health*;
- on the second page add the tree table again (choose *existing entity*) and select only the health
  attributes.

If the teams must not edit each other's data, that part is not covered by pages. You would need separate
cycles or record ownership rules for that.
""", True, []),
        ('stefan_dev', 50, """
For analysis it does not matter which page an attribute is on: the exported `tree` table has all
columns. Two entities would force you to join them on every export, so I would avoid that.
""", False, [('priya_env', 'Good point about the join.')]),
    ],
    comments=[('thanh_forest', 'We have the same situation with soil and vegetation crews.')],
    views=233)

question('ibrahim_gis', 17, 'Points recorded with Arena Mobile are 20-30 m off',
    'arena-mobile gps',
    """
We compared plot centres recorded on the tablets with a survey-grade receiver. The tablet points are
consistently 20 to 30 metres away, always in roughly the same direction.

The survey uses UTM zone 29N. Is this the tablet GPS, or could it be a projection issue?
""",
    answers=[
        ('stefan_dev', 4, """
A constant offset in one direction is almost never GPS noise, which is random. It usually means a datum
mismatch: for example coordinates captured in WGS84 but interpreted as a local datum, or the other way
round.

Check which SRS the coordinate attribute uses in the survey and which one the reference receiver exports.
If one of them is a national datum, the shift between the two can easily be tens of metres.
""", True, [('ibrahim_gis', 'The reference data was in the old national datum. Mystery solved.')]),
        ('kwame_nfi', 28, """
Also set a minimum accuracy in the coordinate attribute so crews cannot save a fix that is worse than,
say, 8 m. Under canopy we wait two or three minutes before recording.
""", False, []),
    ],
    views=341)

question('thanh_forest', 20, 'Error when deleting a photo from a record',
    'arena',
    """
Steps:

1. open a record with a file attribute that has a photo;
2. click the delete icon;
3. the message *"Unexpected error"* appears and the photo is still there after reloading.

It happens for every record of this survey, for all users. New photos can be uploaded without problems.
""",
    answers=[
        ('stefan_dev', 16, """
Thanks for the clear steps. This is reproducible when the record was created in a previous cycle and the
file attribute was later renamed. Until the fix is released, a workaround is to upload a replacement photo
(which overwrites the old one) instead of deleting it.
""", False, [('thanh_forest', 'The workaround is fine for now.')]),
    ],
    views=120)

question('carla_coop', 23, 'Cannot connect a new Google Earth Engine account',
    'google-earth-engine earthmap',
    """
I registered for Earth Engine with my cooperative's Google account. When I sign in, I get
*"Earth Engine account not registered for this project"*. With my personal Gmail it works.

What is different about the new account?
""",
    answers=[
        ('giulia_rs', 6, """
New Earth Engine accounts have to be linked to a Google Cloud project, and that project has to be
registered for Earth Engine (commercial or non-commercial). Older accounts were created before this was
required, which is why your personal one works.

1. Go to the Earth Engine registration page while signed in with the new account.
2. Create or pick a Cloud project and complete the non-commercial questionnaire if that applies to you.
3. Wait a few minutes, sign out of the tool and sign in again.

Organisation accounts sometimes block project creation; in that case your Workspace admin must allow it.
""", True, [('carla_coop', 'Our admin had disabled project creation. Working now.')]),
    ],
    views=502)

question('lucia_redd', 27, '"503 Service Unavailable" when opening my survey',
    'arena',
    """
For the last two hours every page of my survey returns **503 Service Unavailable**. The login page loads.
Is the server down or is it something in my survey?
""",
    answers=[
        ('marta_of', 2, """
That was on our side: a maintenance restart took longer than planned. It is back now. Nothing is wrong
with your survey and no data was affected. Sorry for the interruption.
""", True, []),
    ],
    comments=[('henrik_s', 'Same here from Oslo, so not a local network issue.'),
              ('lucia_redd', 'Back online for me too.')],
    views=96)

question('henrik_s', 31, 'Adding more commodity and land-cover layers for Brazil in Whisp',
    'whisp',
    """
For plots in Brazil it would help to have the national land-cover maps next to the global datasets,
since they are produced locally and updated yearly.

Is there a process for proposing additional datasets, and what are the criteria?
""",
    answers=[
        ('giulia_rs', 30, """
Suggestions are welcome. A dataset is considered when it is:

- publicly available with a licence that allows this use;
- hosted on, or uploadable to, Earth Engine;
- documented (method, reference year, accuracy);
- relevant to the 2020 cut-off date.

Open an issue on the GitHub repository with links to the data and the documentation. National datasets
are added as *national* columns, so they only appear for plots in that country.
""", False, [('henrik_s', 'Issue opened.')]),
    ],
    views=174)

question('priya_env', 36, 'Default value from a function: minimum number of trees per plot',
    'arena validation',
    """
The minimum number of trees to measure depends on the plot radius: 5 for the small plot, 12 for the large
one. I would like `min_tree_count` to be filled in automatically from `plot_radius` and then used in a
validation on the tree entity (minimum count).

Can the *min count* of an entity come from an expression?
""",
    answers=[
        ('marta_of', 10, """
Yes. *Min count* and *Max count* accept expressions. You do not even need the helper attribute:

```
plot_radius > 10 ? 12 : 5
```

If you want to show the number to the crew, keep `min_tree_count` as a read-only calculated attribute with
the same expression and reference it in *min count*.
""", True, []),
    ],
    views=205)

question('sione_pacific', 41, 'Arena Mobile does not use the external GPS receiver',
    'arena-mobile gps',
    """
We pair a Bluetooth GNSS receiver with the tablets. Other apps show sub-metre accuracy, but Arena Mobile
still reports 4-6 m, as if it were using the internal chip.
""",
    answers=[
        ('kwame_nfi', 12, """
The app reads the Android system location. For an external receiver to be used you have to route it
through *mock location*:

1. enable *Developer options* on the tablet;
2. install the receiver vendor's app and select it under *Select mock location app*;
3. start the vendor app first, wait for a fix, then open Arena Mobile.

After that the accuracy shown in the coordinate attribute should match the receiver.
""", True, [('sione_pacific', 'Step 3 (order of starting the apps) was the trick.')]),
    ],
    views=264)

question('thanh_forest', 47, 'Batch delete or archive all images of a survey',
    'arena export',
    """
Our survey has about 40 000 plot photos and we are close to the storage quota. We have downloaded them
all. Is there a way to remove the files in bulk while keeping the records?
""",
    answers=[
        ('stefan_dev', 22, """
Not from the user interface at the moment; files are deleted one by one or together with the record.
Two options:

- export the survey with files as a backup, then ask the support team to purge the files of that survey;
- reduce future usage by setting a maximum image size on the file attribute (photos are resized on upload).
""", False, [('thanh_forest', 'Is the maximum size applied to photos already uploaded?'),
             ('stefan_dev', 'No, only to new uploads.')]),
    ],
    views=143)

question('lucia_redd', 55, 'Clipping a 1 km systematic grid to provinces for a national Collect Earth survey',
    'collectearth',
    """
I have a national 1 x 1 km grid (about 1.2 million points) and need one Collect Earth project per
province. What is the recommended way to split the grid and keep the plot IDs stable between provinces?
""",
    answers=[
        ('giulia_rs', 18, """
Do the split in GIS rather than in Collect Earth:

1. generate the grid once for the whole country and assign the ID **before** clipping (for example
   `row_col`), so an ID never depends on the province;
2. spatial-join the grid with the province layer and add the province code as an extra column;
3. export one CSV per province with `id, YCoordinate, XCoordinate, province`.

Then use the same `.cep` template with a different CSV for each province. Keep each file under about
50 000 plots or Google Earth gets sluggish.
""", True, [('lucia_redd', 'Assigning IDs before clipping is exactly what I was missing.')]),
        ('ana_sig', 70, """
If a point falls exactly on a province border the spatial join can duplicate it. Use *one-to-one* with
*largest overlap* or deduplicate on the ID afterwards.
""", False, []),
    ],
    views=388)

question('priya_env', 62, 'Calculate horizontal plot radius automatically from slope angle',
    'arena calc',
    """
On slopes the crew measures a slope distance. I want a calculated attribute giving the corrected radius
so that the horizontal area stays constant. Which functions are available for trigonometry?
""",
    answers=[
        ('stefan_dev', 8, """
Math functions are available in expressions. With `slope_deg` in degrees:

```
plot_radius / Math.cos(slope_deg * Math.PI / 180)
```

Put it in a decimal attribute marked as *calculated*. If slope is recorded in percent instead:

```
plot_radius * Math.sqrt(1 + Math.pow(slope_pct / 100, 2))
```
""", True, [('priya_env', 'Perfect, the percent version is the one we need.')]),
    ],
    views=221)

question('kwame_nfi', 70, 'Taxonomy error when exporting the survey',
    'arena taxonomy export',
    """
Exporting the survey fails with *"taxonomy: duplicate code"*, but the import of the species list succeeded
months ago and data collection has been running since.
""",
    answers=[
        ('marta_of', 14, """
The import accepts codes that differ only by case or trailing spaces (`ACA_NIL` and `aca_nil `); the
export is stricter. Download the taxonomy, sort by code in a spreadsheet, fix the duplicates and import it
again. Records keep their link as long as the corrected code is the one they were using.
""", True, []),
    ],
    comments=[('thanh_forest', 'We hit this too. Trailing spaces from a copy-paste.')],
    views=132)

question('carla_coop', 80, 'How can I embed Earth Map in my own website?',
    'earthmap',
    """
We would like to show an Earth Map view of our cooperative's region on our website. Can it be embedded in
an iframe with a fixed area and layer selected?
""",
    answers=[
        ('giulia_rs', 40, """
You can embed it in an iframe. Use the *Share* button after choosing the area and layers: the link keeps
the state, and you can use it as the iframe `src`:

```html
<iframe src="PASTE_SHARED_LINK_HERE" width="100%" height="600" style="border:0"></iframe>
```

Visitors who are not signed in see the public layers; statistics that need Earth Engine processing ask
them to log in.
""", False, []),
    ],
    views=910)

question('henrik_s', 88, 'Whisp API rejects my GeoJSON file',
    'whisp',
    """
Uploading through the web page works, but the API returns `400 invalid geometry` for the same file. The
file has 180 polygons exported from QGIS.
""",
    answers=[
        ('stefan_dev', 9, """
Typical causes, most common first:

- the file is not in **EPSG:4326**. QGIS exports in the layer CRS unless you change it in the export dialog;
- coordinates have a Z value. Export with *Include z-dimension* unchecked;
- self-intersecting polygons. Run *Fix geometries* first.

The web page repairs some of these silently, the API does not.
""", True, [('henrik_s', 'Z values. Thanks.')]),
    ],
    views=276)

question('lucia_redd', 97, 'Saiku in Collect Earth does not start: "port already in use"',
    'collectearth saiku installation',
    """
After the last update, *Tools > Start Saiku* shows "port 8380 already in use" and nothing opens. Restarting
Collect Earth does not help.
""",
    answers=[
        ('giulia_rs', 5, """
A previous Saiku process is still running in the background. On Windows open *Task Manager*, end any
`java.exe` that belongs to Saiku and start it again from Collect Earth. On macOS or Linux:

```
lsof -i :8380
kill <PID>
```

If it happens every time, change the port under *Tools > Properties > Advanced*.
""", True, []),
        ('tomasz_k', 60, 'Rebooting the PC also clears it, which is what my students end up doing.', False, []),
    ],
    views=367)

question('ibrahim_gis', 110, 'NDVI and Landsat charts in Collect Earth are very slow or empty',
    'collectearth google-earth-engine',
    """
The Earth Engine window that opens next to each plot takes over a minute or stays empty. Bing and Google
Earth imagery load normally.
""",
    answers=[
        ('giulia_rs', 11, """
The charts are computed on demand by Earth Engine. It gets slow when:

- many operators share one Google account (requests are queued per user);
- the plot is large, or the time series starts very early;
- the browser blocks third-party cookies, in which case the window stays empty.

Have each operator sign in with their own account and allow cookies for the Earth Engine app.
""", False, [('ibrahim_gis', 'We were ten people on one account. That explains it.')]),
    ],
    views=318)

question('sione_pacific', 125, 'Read-only fields can still be edited in Arena Mobile',
    'arena-mobile validation',
    """
An attribute marked *read only* in the survey designer is locked in the web form but can be changed on
the tablet. Is this expected?
""",
    answers=[
        ('marta_of', 24, """
No, that is a bug in the mobile app: the read-only flag was only applied to calculated attributes. It is
fixed in the next release. Until then, a validation rule comparing the value with its default will at
least flag changed values as errors.
""", True, []),
    ],
    views=109)

question('ana_sig', 140, 'Photo upload fails for images larger than 10 MB',
    'collectmobile',
    """
Photos taken with the tablet camera at full resolution fail to sync. Smaller ones are fine. Is there a
size limit and can it be changed?
""",
    answers=[
        ('thanh_forest', 15, """
Yes, there is a limit per file. Rather than raising it, set the *maximum image size* on the image
attribute in the survey: photos are then resized on the device before they are saved, and syncing is much
faster on slow connections too.
""", False, []),
    ],
    views=154)

question('tomasz_k', 160, 'Installing Collect on Windows 11 without administrator rights',
    'collect installation',
    """
The lab computers do not allow installing software. Is there a portable way to run Collect for a class?
""",
    answers=[
        ('stefan_dev', 30, """
Choose a folder inside the user profile in the installer (for example `C:\\Users\\<name>\\OpenForis`); it
does not need administrator rights unless it writes to *Program Files*. The bundled Java runtime is
installed in the same folder.

For a class, a single shared server instance that students open in the browser is usually less work than
thirty local installations.
""", True, [('tomasz_k', 'Went with the shared instance in the end.')]),
    ],
    views=241)

question('amina_field', 2, 'Tablet says "survey not up to date" but there is no update button',
    'arena-mobile',
    """
Two of our tablets show a yellow "survey not up to date" warning. The others updated automatically. Where
is the update option?
""",
    views=38)

question('newbie_2025', 1, 'Which tool should I start with for a small community forest inventory?',
    'arena collect',
    """
We are a small NGO planning to measure about 60 plots in a community forest. I see Collect, Collect
Mobile, Arena and Arena Mobile. Which combination should a new project use today?
""",
    comments=[('tomasz_k', 'How many people will collect data, and do they have Android tablets?')],
    views=57)

question('henrik_s', 4, 'Whisp map does not let me download the due diligence statement',
    'whisp export',
    """
After the analysis finishes, the *Download* button stays greyed out for plots drawn directly on the map.
Uploaded files work.
""",
    answers=[
        ('carla_coop', 20, """
I had this as well. The button becomes active once every drawn plot has a name: click each polygon and
fill in the plot ID.
""", False, []),
    ],
    views=83)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Messages(object):
    """Stand-in for the django.contrib.messages storage of a request."""
    def add(self, level, message, extra_tags=''):
        pass


def backdate(action, when):
    """Move an action and everything it produced to a date in the past."""
    Action.objects.filter(pk=action.pk).update(action_date=when)
    ActionRepute.objects.filter(action=action).update(date=when)
    Vote.objects.filter(action=action).update(voted_at=when)
    # Badges awarded by the hooks of this action.
    awards = Award.objects.filter(trigger=action)
    Action.objects.filter(pk__in=list(awards.values_list('action_id', flat=True))).update(action_date=when)
    awards.update(awarded_at=when)


def backdate_node(node, when):
    Node.objects.filter(pk=node.pk).update(added_at=when, last_activity_at=when)
    NodeRevision.objects.filter(node=node).update(revised_at=when)


def touch(question, user, when):
    Node.objects.filter(pk=question.pk, last_activity_at__lt=when).update(last_activity_at=when, last_activity_by=user)


def run(action, when, data=None):
    action.save(data=data, threaded=False)
    backdate(action, when)
    return action


def install_assets():
    folder = str(settings.UPFILES_FOLDER)
    os.makedirs(folder, exist_ok=True)
    for name, url in ASSETS.items():
        path = os.path.join(folder, name)
        if os.path.exists(path):
            continue
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=30) as response, open(path, 'wb') as out:
                out.write(response.read())
            print('  downloaded %s' % name)
        except Exception as e:
            print('  could not download %s (%s); copy it to %s by hand' % (name, e, folder))


def apply_branding():
    print('\n[1/5] Branding')
    install_assets()
    for name, value in BRANDING:
        getattr(settings, name).set_value(value)
    print('  %d settings updated' % len(BRANDING))


def create_users():
    print('\n[2/5] Users')
    users = {}
    for username, real_name, location, about, days in USERS:
        joined = NOW - timedelta(days=days, hours=random.randint(0, 23))
        is_admin = username == ADMIN[0]
        user = User(username=username, email='%s@example.com' % username, real_name=real_name,
                    location=location, about=about, date_joined=joined, last_seen=NOW - timedelta(
                        days=random.randint(0, min(days, 12)), minutes=random.randint(0, 600)),
                    is_staff=is_admin, is_superuser=is_admin)
        user.set_password(ADMIN[1] if is_admin else PASSWORD)
        user.save()
        run(UserJoinsAction(user=user, ip=IP), joined)
        if username != 'newbie_2025':
            run(EmailValidationAction(user=user, ip=IP), joined + timedelta(minutes=random.randint(2, 90)))
        users[username] = User.objects.get(pk=user.pk)
    print('  %d users' % len(users))
    return users


def create_content(users):
    print('\n[3/5] Questions, answers and comments')
    counts = dict(questions=0, answers=0, comments=0)
    posts = []

    def add_comments(parent, question, comments, start):
        when = start
        for author, text in comments:
            when = when + timedelta(hours=random.randint(1, 20), minutes=random.randint(0, 59))
            when = min(when, NOW - timedelta(minutes=random.randint(1, 30)))
            action = run(CommentAction(user=users[author], ip=IP), when, dict(text=text, parent=parent))
            backdate_node(action.node, when)
            touch(question, users[author], when)
            counts['comments'] += 1

    # Oldest first, so ids and activity follow the timeline.
    for q in sorted(Q, key=lambda item: -item['days']):
        asked = NOW - timedelta(days=q['days'], hours=random.randint(0, 20), minutes=random.randint(0, 59))
        author = users[q['author']]
        action = run(AskAction(user=author, ip=IP), asked,
                     dict(title=q['title'], text=q['body'], tags=q['tags'], added_at=asked))
        node = action.node
        backdate_node(node, asked)
        Node.objects.filter(pk=node.pk).update(last_activity_by=author)
        counts['questions'] += 1
        posts.append((node, asked))

        add_comments(node, node, q['comments'], asked)

        for answerer, hours, body, accepted, comments in q['answers']:
            answered = min(asked + timedelta(hours=hours, minutes=random.randint(0, 59)),
                           NOW - timedelta(minutes=random.randint(5, 50)))
            answer_action = run(AnswerAction(user=users[answerer], ip=IP), answered,
                                dict(text=body.strip(), question=node))
            answer = answer_action.node
            backdate_node(answer, answered)
            touch(node, users[answerer], answered)
            counts['answers'] += 1
            posts.append((answer, answered))

            add_comments(answer, node, comments, answered)

            if accepted:
                when = min(answered + timedelta(hours=random.randint(1, 30)), NOW - timedelta(minutes=3))
                run(AcceptAnswerAction(user=author, node=answer, ip=IP), when)

        if q['views']:
            Node.objects.filter(pk=node.pk).update(extra_count=q['views'])

    print('  %(questions)d questions, %(answers)d answers, %(comments)d comments' % counts)
    return posts


def create_votes(users, posts):
    print('\n[4/5] Votes')
    everyone = list(users.values())
    up = down = 0
    for node, created in posts:
        node = Node.objects.get(pk=node.pk)
        candidates = [u for u in everyone if u.pk != node.author_id]
        # Good answers and old, much-viewed questions collect more votes.
        age_days = max((NOW - created).days, 1)
        ceiling = 3 + min(age_days // 12, 7)
        if node.node_type == 'answer' and node.nis.accepted:
            ceiling += 3
        for voter in random.sample(candidates, random.randint(0, min(ceiling, len(candidates)))):
            when = created + timedelta(hours=random.randint(1, 24 * min(age_days, 20)))
            when = min(when, NOW - timedelta(minutes=random.randint(1, 120)))
            if random.random() < 0.93:
                run(VoteUpAction(user=voter, node=Node.objects.get(pk=node.pk), ip=IP), when)
                up += 1
            else:
                run(VoteDownAction(user=voter, node=Node.objects.get(pk=node.pk), ip=IP), when)
                down += 1
    print('  %d up, %d down' % (up, down))


def describe_tags(users):
    print('\n[5/5] Tag descriptions and synonyms')
    admin = users[ADMIN[0]]
    described = 0
    for name, description in TAG_DESCRIPTIONS.items():
        described += Tag.objects.filter(name=name).update(description=description)
    synonyms = 0
    for source, target in TAG_SYNONYMS.items():
        tag = Tag.objects.filter(name=target).first()
        if tag and not Tag.objects.filter(name=source).exists():
            TagSynonym.objects.get_or_create(source_name=source, defaults=dict(target=tag, created_by=admin))
            synonyms += 1
    print('  %d descriptions, %d synonyms' % (described, synonyms))


def restore_activity_dates():
    """Voting and accepting save the question again; put the last activity back on the timeline."""
    for question in Node.objects.filter(node_type='question'):
        latest = Node.objects.filter(abs_parent=question).order_by('-added_at').first()
        if latest is None or latest.added_at < question.added_at:
            latest = question
        Node.objects.filter(pk=question.pk).update(last_activity_at=latest.added_at,
                                                   last_activity_by=latest.author_id)
    for node in Node.objects.exclude(node_type='question'):
        Node.objects.filter(pk=node.pk).update(last_activity_at=node.added_at)


def main():
    if User.objects.exists() or Node.objects.exists():
        sys.exit('The database already has users or posts. Run this on a fresh database '
                 '(delete osqa.db and run "python manage.py migrate").')

    # Settings and model instances are cached outside the database; a cache left
    # over from a previous database would hide what is stored now.
    cache.clear()

    request = RequestFactory().get('/')
    request._messages = _Messages()
    request.user = AnonymousUser()
    REQUEST_HOLDER.request = request

    no_mail = lambda *args, **kwargs: None
    with mock.patch('forum.templatetags.email_tags.create_and_send_mail_messages', no_mail), \
         mock.patch('forum.utils.mail.create_and_send_mail_messages', no_mail):
        apply_branding()
        users = create_users()
        posts = create_content(users)
        create_votes(users, posts)
        describe_tags(users)
        restore_activity_dates()

    cache.clear()

    print('\nDone. Log in as %s / %s (administrator); other users use the password "%s".'
          % (ADMIN[0], ADMIN[1], PASSWORD))


if __name__ == '__main__':
    main()

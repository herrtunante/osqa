import logging
import os
import posixpath
import shutil
import tempfile
from zipfile import ZipFile, BadZipFile

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from forum.views.admin import admin_page, TOOL_LINKS
from . import importer


def extract_dump(dump, extract_to):
    """Extract the .xml files of the zip dump into the folder extract_to.

    Members in sub folders are extracted by their file name; a member whose
    name is absolute or goes up a folder is refused, along with the dump.
    """
    members = {}

    for m in dump.namelist():
        if not m.lower().endswith('.xml'):
            continue

        parts = m.replace('\\', '/').split('/')
        name = parts[-1]

        if m.startswith(('/', '\\')) or '..' in parts or ':' in m or not name or name.startswith('.'):
            raise importer.DumpError(_("The dump contains an invalid file name: %s") % m)

        members[name] = m

    for name, m in members.items():
        path = os.path.join(extract_to, name)

        # Defense in depth: the file must end up right inside extract_to.
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(extract_to):
            raise importer.DumpError(_("The dump contains an invalid file name: %s") % m)

        with dump.open(m) as src, open(path, 'wb') as dst:
            shutil.copyfileobj(src, dst)

    return list(members.keys())


@admin_page
def sximporter(request):
    messages = []

    if request.method == "POST" and "dump" in request.FILES:
        extract_to = tempfile.mkdtemp()

        try:
            with ZipFile(request.FILES['dump']) as dump:
                extract_dump(dump, extract_to)

            options = dict([(k, v) for k, v in request.POST.items()])
            options['authenticated_user'] = request.user

            importer.sximport(extract_to, options)
            messages.append(_("The dump was imported."))
        except (BadZipFile, importer.DumpError) as e:
            messages.append(isinstance(e, BadZipFile) and _("The dump is not a zip file.") or str(e))
        except Exception as e:
            logging.exception("Error importing a StackExchange dump")
            messages.append(_("The import failed: %s") % e)
        finally:
            shutil.rmtree(extract_to, ignore_errors=True)

    return ('modules/sximporter/page.html', {
        'names': messages
    })


TOOL_LINKS.append(('sximporter', _lazy('Stack Exchange import')))

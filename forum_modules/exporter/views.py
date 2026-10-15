import os, configparser, datetime, logging, shutil, tempfile
import json

from django.http import HttpResponse, HttpResponseRedirect, Http404, FileResponse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.core.cache import cache
from django.db import connections
from django.urls import reverse
from forum.views.admin import admin_tools_page, admin_page, super_user_required
from forum.models import User
from .forms import ExporterForm, ImporterForm
from threading import Thread
from . import settings as selsettings
from forum import settings

from .exporter import export, CACHE_KEY, EXPORT_STEPS, DATE_AND_AUTHOR_INF_SECTION, DATETIME_FORMAT, TMP_FOLDER
from .importer import start_import, extract_backup, check_backup, FILE_HANDLERS

BACKUP_EXTENSIONS = ('.zip', '.tar.gz')


def run_in_thread(target, *args):
    def run():
        try:
            target(*args)
        finally:
            # The thread opened its own database connections.
            connections.close_all()

    thread = Thread(target=run, daemon=True)
    thread.start()


def is_running():
    state = cache.get(CACHE_KEY)
    return state and state['running']


@admin_tools_page('exporter', _lazy('XML data export'))
def exporter(request):
    if is_running():
        return HttpResponseRedirect(reverse('exporter_running', kwargs=dict(mode='exporter')))

    if request.method == 'POST':
        form = ExporterForm(request.POST)

        if form.is_valid():
            # The running page redirects here while there is no state yet;
            # the export thread replaces this one as soon as it starts.
            now = datetime.datetime.now()
            steps = [s for s in EXPORT_STEPS if not (form.cleaned_data.get('anon_data') and s['fn'].is_user_data())]
            counts = dict((s['id'], s['fn'].count(now)) for s in steps)
            cache.set(CACHE_KEY, dict(running=True, time_started='', state=dict(
                [(id, {'status': _('Queued'), 'count': count, 'parsed': 0}) for id, count in counts.items()] +
                [('overall', {'status': _('Starting'), 'count': sum(counts.values()), 'parsed': 0})])))
            run_in_thread(export, form.cleaned_data, request.user)

            return HttpResponseRedirect(reverse('exporter_running', kwargs=dict(mode='exporter')))
    else:
        form = ExporterForm()

    available = []

    folder = str(selsettings.EXPORTER_BACKUP_STORAGE)

    for f in os.listdir(folder):
        if (not os.path.isdir(os.path.join(folder, f))) and f.endswith('.backup.inf'):
            try:
                with open(os.path.join(folder, f), 'r') as inffile:
                    inf = configparser.RawConfigParser()
                    inf.read_file(inffile)

                    if inf.get(DATE_AND_AUTHOR_INF_SECTION, 'site') == settings.APP_URL and os.path.exists(
                                    os.path.join(folder, inf.get(DATE_AND_AUTHOR_INF_SECTION, 'file-name'))):
                        available.append({
                            'author': User.objects.get(id=inf.get(DATE_AND_AUTHOR_INF_SECTION, 'author')),
                            'date': datetime.datetime.strptime(inf.get(DATE_AND_AUTHOR_INF_SECTION, 'finished'), DATETIME_FORMAT),
                            'file': inf.get(DATE_AND_AUTHOR_INF_SECTION, 'file-name'),
                        })
            except Exception as e:
                pass

    available.sort(key=lambda b: b['date'], reverse=True)

    return ('modules/exporter/exporter.html', {
        'form': form,
        'available': available,
    })


@admin_tools_page('importer', _lazy('XML data import'))
def importer(request):
    if is_running():
        return HttpResponseRedirect(reverse('exporter_running', kwargs=dict(mode='importer')))

    error = None

    if request.method == 'POST':
        form = ImporterForm(request.POST, request.FILES)

        if form.is_valid():
            if not os.path.exists(TMP_FOLDER):
                os.makedirs(TMP_FOLDER)
            location = tempfile.mkdtemp(dir=TMP_FOLDER)

            try:
                extract_backup(form.cleaned_data['dump'], location)
                check_backup(location)
            except Exception as e:
                shutil.rmtree(location, ignore_errors=True)
                logging.warning("Invalid backup file: %s", e)
                error = isinstance(e, ValueError) and str(e) or _('The file is not a valid backup.')
            else:
                def import_and_clean():
                    try:
                        start_import(location, None, request.user)
                    finally:
                        shutil.rmtree(location, ignore_errors=True)

                run_in_thread(import_and_clean)

                return HttpResponseRedirect(reverse('exporter_running', kwargs=dict(mode='importer')))
    else:
        form = ImporterForm()

    return ('modules/exporter/importer.html', {
        'form': form,
        'error': error,
    })


@admin_page
def running(request, mode):
    state = cache.get(CACHE_KEY)
    if state is None:
        return HttpResponseRedirect(reverse('admin_tools', args=['exporter']))

    return ('modules/exporter/running.html', {
        'mode': mode,
        'steps': mode == 'exporter' and EXPORT_STEPS or FILE_HANDLERS,
    })

@super_user_required
def state(request):
    return HttpResponse(json.dumps(cache.get(CACHE_KEY)), content_type="application/json")

@super_user_required
def download(request):
    fname = request.GET.get('file', '')

    # Only serve backups from the backups folder, by their plain file name.
    if not fname or os.path.basename(fname) != fname or not fname.endswith(BACKUP_EXTENSIONS):
        raise Http404

    fname = os.path.join(str(selsettings.EXPORTER_BACKUP_STORAGE), fname)

    if not os.path.isfile(fname):
        raise Http404

    if fname.endswith('.gz'):
        content_type='application/x-gzip'
        filename = 'backup.tar.gz'
    else:
        content_type='application/zip'
        filename = 'backup.zip'

    return FileResponse(open(fname, 'rb'), as_attachment=True, filename=filename, content_type=content_type)

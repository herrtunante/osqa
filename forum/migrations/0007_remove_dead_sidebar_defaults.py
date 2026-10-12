"""Hide the sidebar blocks that still hold the old shipped defaults.

They advertised WebFaction hosting (closed in 2022) and osqa.net (offline).
Settings are written to the database the first time they are read, so
changing the defaults alone does not reach existing sites. Blocks whose
text was edited are left alone.
"""
from django.db import migrations

OLD_DEFAULTS = {
    'SIDEBAR_UPPER': (
        '\n[![WebFaction logo][2]][1]\n## [Reliable OSQA Hosting][1]\n\nWe recommend [**WebFaction**][1] for OSQA '
        'hosting. For under $10/month their reliable servers get the job done. See our [**step-by-step setup guide**]'
        '(http://wiki.osqa.net/display/docs/Installing+OSQA+on+WebFaction).\n\n'
        '[1]: http://www.webfaction.com?affiliate=osqa\n[2]: /m/default/media/images/webfaction.png'
    ),
    'SIDEBAR_LOWER': (
        '\n## Learn more about OSQA\n\nThe [**OSQA website**](http://www.osqa.net/) and [**OSQA wiki**]'
        '(http://wiki.osqa.net/) are great resources to help you learn more about the OSQA open source Q&A system. '
        '[**Join the OSQA chat!**](http://meta.osqa.net/question/79/is-there-an-online-chat-room-or-irc-channel-for-osqa#302)\n'
    ),
}


def _normalized(text):
    return ' '.join(str(text).split())


def hide_old_default_blocks(apps, schema_editor):
    KeyValue = apps.get_model('forum', 'KeyValue')
    changed = False

    for prefix, old_text in OLD_DEFAULTS.items():
        text = KeyValue.objects.filter(key=prefix + '_TEXT').first()
        if text is None or _normalized(text.value) != _normalized(old_text):
            continue

        text.value = ''
        text.save()
        KeyValue.objects.update_or_create(key=prefix + '_SHOW', defaults={'value': False})
        changed = True

    if changed:
        # Settings are also read through the shared cache.
        from django.core.cache import cache
        cache.clear()


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0006_remove_openid_tables"),
    ]

    operations = [
        migrations.RunPython(hide_old_default_blocks, migrations.RunPython.noop),
    ]

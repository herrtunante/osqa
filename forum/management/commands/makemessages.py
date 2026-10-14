from django.core.management.commands import makemessages


class Command(makemessages.Command):
    """Django's makemessages, also extracting _lazy("...") (gettext_lazy for
    strings defined at import time: form labels, badge names, header links)."""
    xgettext_options = makemessages.Command.xgettext_options + ['--keyword=_lazy']

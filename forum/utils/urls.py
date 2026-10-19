from django.urls import reverse, get_script_prefix


def root_relative_reverse(*args, **kwargs):
    """reverse() without the script prefix, for appending to APP_URL.

    Replaces reverse(..., prefix='/'), which Django no longer supports.
    APP_URL already includes any script prefix the site is mounted under.
    """
    return '/' + reverse(*args, **kwargs)[len(get_script_prefix()):]

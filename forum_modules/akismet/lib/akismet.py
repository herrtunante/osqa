"""
A minimal client for the `Akismet <https://akismet.com/developers/>`_ API,
using only the standard library.

Only the ``comment-check`` call is implemented: it is all the module uses.
"""

from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = 'https://rest.akismet.com/1.1/'

# Seconds to wait for Akismet; posting must not hang when it is unreachable.
DEFAULT_TIMEOUT = 5


class AkismetError(Exception):
    """Akismet could not be reached or gave an unexpected answer."""


class Akismet(object):
    def __init__(self, key, blog_url, agent, timeout=DEFAULT_TIMEOUT):
        self.key = key
        self.blog_url = blog_url
        self.user_agent = agent
        self.timeout = timeout

    def _post(self, method, params):
        request = Request(API_URL + method, data=urlencode(params).encode('utf-8'), headers={
            'User-Agent': self.user_agent,
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode('utf-8', 'replace').strip().lower()
        except (OSError, ValueError) as e:
            # URLError, HTTPError and timeouts are all OSErrors.
            raise AkismetError(str(e))

    def comment_check(self, comment, data=None):
        """
        Returns ``True`` if Akismet thinks ``comment`` is spam, ``False`` if not.

        ``data`` holds the other comment-check parameters (``user_ip`` and
        ``user_agent`` are required). Raises ``AkismetError`` on failure.
        """
        params = dict(data or {})
        params.update(api_key=self.key, blog=self.blog_url, comment_content=comment)

        result = self._post('comment-check', params)
        if result == 'true':
            return True
        if result == 'false':
            return False
        raise AkismetError('Unexpected Akismet response: %r' % result[:100])

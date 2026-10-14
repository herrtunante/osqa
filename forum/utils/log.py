"""Logging handlers.

AdminEmailHandler emails server errors through the forum's own SMTP settings
(Admin > Email settings), so it works without Django's EMAIL_* settings. It
writes to ADMINS when set, otherwise to the site's superusers, and sends the
same error at most once every ten minutes.
"""
import hashlib
import logging
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from django.conf import settings as djsettings
from django.core.cache import cache

REPEAT_SECONDS = 600


class AdminEmailHandler(logging.Handler):
    def __init__(self, level=logging.ERROR):
        super().__init__(level)

    def recipients(self):
        admins = [email for name, email in getattr(djsettings, 'ADMINS', ())]
        if admins:
            return admins
        from forum.models import User
        return list(User.objects.filter(is_superuser=True, is_active=True).exclude(email='')
                    .values_list('email', flat=True)[:10])

    def emit(self, record):
        try:
            from forum import settings
            from forum.utils.mail import create_connection
            if not settings.EMAIL_HOST:
                return
            message = record.getMessage()
            key = 'admin-error-mail:' + hashlib.sha256(message[:300].encode('utf-8')).hexdigest()[:32]
            if not cache.add(key, 1, REPEAT_SECONDS):
                return
            recipients = self.recipients()
            if not recipients:
                return

            body = self.format(record)
            body += "\n\nThe same error is emailed at most once every %d minutes; see the log for every " \
                    "occurrence." % (REPEAT_SECONDS // 60)
            mail = MIMEText(body, 'plain', 'utf-8')
            mail['Subject'] = '[%s] %s: %s' % (settings.APP_SHORT_NAME, record.levelname,
                                               message.splitlines()[0][:150] if message else '')
            mail['From'] = str(settings.DEFAULT_FROM_EMAIL)
            mail['To'] = ', '.join(recipients)
            mail['Date'] = formatdate(localtime=True)
            mail['Message-ID'] = make_msgid()

            connection = create_connection()
            try:
                connection.sendmail(str(settings.DEFAULT_FROM_EMAIL), recipients, mail.as_string())
            finally:
                connection.quit()
        except Exception:
            # Never let error reporting raise errors of its own.
            pass

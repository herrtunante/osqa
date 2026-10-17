def api_is_spam(request, text, kind):
    """Spam check of posts made through the API (forum/api/endpoints.py), as
    startup.check_spam does for the web forms; kind is the Akismet content type."""
    from .settings import WORDPRESS_API_KEY
    from .startup import can_bypass_spam_check, is_spam
    if not str(WORDPRESS_API_KEY).strip() or not text or can_bypass_spam_check(request.user):
        return False
    return bool(is_spam(request, text, kind))

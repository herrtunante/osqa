from django import template

from forum.models import AISuggestion

register = template.Library()


@register.inclusion_tag('node/ai_suggestion.html')
def ai_suggestion(question, viewer):
    """The AI-suggested answer of a question, if one is shown."""
    suggestion = AISuggestion.objects.filter(
        question=question, status__in=(AISuggestion.SHOWN, AISuggestion.HELPFUL)).first()
    can_resolve = suggestion is not None and viewer.is_authenticated and (
        viewer == question.author or viewer.is_staff or viewer.is_superuser)
    return {'suggestion': suggestion, 'can_resolve': can_resolve}

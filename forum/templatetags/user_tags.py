from django import template
from django.utils.translation import gettext as _
from django.utils.safestring import mark_safe
import logging

register = template.Library()


@register.filter
def profile_links_trusted(user):
    from forum.utils.antispam import links_trusted
    return links_trusted(user)


@register.filter
def profile_about(user):
    """The user's "about me" as HTML; links only for trusted users."""
    import markdown
    from forum.utils.antispam import links_trusted
    from forum.utils.html import sanitize_profile_html
    return mark_safe(sanitize_profile_html(markdown.markdown(str(user.about)), links_trusted(user)))

class UserSignatureNode(template.Node):
    template = template.loader.get_template('users/signature.html')

    def __init__(self, user, format):
        self.user = template.Variable(user)
        self.format = template.Variable(format)

    def render(self, context):
        # Django 4.2: Template.render() expects a dict, not a Context object
        return self.template.render({
        'user': self.user.resolve(context),
        'format': self.format.resolve(context)
        })

@register.tag
def user_signature(parser, token):
    try:
        tag_name, user, format = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("%r tag requires exactly two arguments" % token.contents.split()[0])

    return UserSignatureNode(user, format)


class ActivityNode(template.Node):
    template = template.loader.get_template('users/activity.html')

    def __init__(self, activity, viewer):
        self.activity = template.Variable(activity)
        self.viewer = template.Variable(viewer)

    def render(self, context):
        try:
            action = self.activity.resolve(context).leaf
            viewer = self.viewer.resolve(context)
            describe = mark_safe(action.describe(viewer))
            return self.template.render(dict(action=action, describe=describe))
        except Exception as e:
            import traceback
            msg = "Error in action describe: \n %s" % (
                traceback.format_exc()
            )
            logging.error(msg)
            return ''

@register.tag
def activity_item(parser, token):
    try:
        tag_name, activity, viewer = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("%r tag requires exactly two arguments" % token.contents.split()[0])

    return ActivityNode(activity, viewer)


@register.tag
def flagged_item(parser, token):
    try:
        tag_name, post, viewer = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("%r tag requires exactly two arguments" % token.contents.split()[0])

    return ActivityNode(post, viewer)


@register.inclusion_tag('users/menu.html')
def user_menu(viewer, user):
    return dict(viewer=viewer, user=user)


"""Versions of a tagged product ("collect": 3.26, 4.0, 4.1) and the version a
question or answer applies to.

Moderators keep a tag's ordered list of versions on its management page
(forum/utils/tags.py); the version chosen for a post, the "still works in
<current>" confirmations and what is shown are in forum/utils/post_versions.py.
"""
from django.db import models

from .node import Node
from .tag import Tag
from .user import User


class TagVersion(models.Model):
    """One version of a tag's product. `position` orders them (oldest first);
    at most one per tag is the current version."""
    tag = models.ForeignKey(Tag, related_name='versions', on_delete=models.CASCADE)
    name = models.CharField(max_length=20)
    position = models.PositiveIntegerField(default=0)
    is_current = models.BooleanField(default=False)
    released = models.DateField(null=True, blank=True)

    class Meta:
        app_label = 'forum'
        ordering = ('tag', 'position', 'id')
        constraints = [models.UniqueConstraint(fields=['tag', 'name'], name='tagversion_tag_name_uniq')]

    def __str__(self):
        return '%s %s' % (self.tag.name, self.name)


class NodeVersion(models.Model):
    """The version of `tag` a question or answer applies to (one per tag), and
    the latest confirmation that it still works in a newer version."""
    node = models.ForeignKey(Node, related_name='versions', on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, related_name='node_versions', on_delete=models.CASCADE)
    version = models.ForeignKey(TagVersion, related_name='node_versions', on_delete=models.CASCADE)
    # "Still works in 4.1" (ConfirmVersionAction): the version, who and when.
    confirmed_version = models.ForeignKey(TagVersion, null=True, blank=True, related_name='confirmations',
                                          on_delete=models.SET_NULL)
    confirmed_by = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_action = models.ForeignKey('Action', null=True, blank=True, related_name='+',
                                         on_delete=models.SET_NULL)

    class Meta:
        app_label = 'forum'
        constraints = [models.UniqueConstraint(fields=['node', 'tag'], name='nodeversion_node_tag_uniq')]

    def __str__(self):
        return 'Node %s: %s' % (self.node_id, self.version_id)

from . import startup

import os.path
from forum import settings
from django.urls import re_path, include
from django.conf import settings as djsettings
from django.contrib import admin
from forum import views as app
from forum.sitemap import OsqaSitemap, index as sitemap_index, sitemap_section_index, sitemap
from django.utils.translation import gettext as _
from django.shortcuts import redirect
from django.views.static import serve
import logging

# Django 1.7+: admin.autodiscover() is automatic, no longer needed
# admin.autodiscover()

sitemaps = {
    'questions': OsqaSitemap
}

APP_PATH = os.path.dirname(__file__)

# Choose the user urls pattern
if bool(settings.INCLUDE_ID_IN_USER_URLS.value):
    core_user_urls_prefix = r'^%s(?P<id>\d+)/(?P<slug>.*)'
else:
    core_user_urls_prefix = r'^%s(?P<slug>.*)'

core_urls = [
    re_path(r'^$', app.readers.index, name='index'),
    #re_path(r'^%s(.*)' % _('nimda/'), admin.site.urls),
    #re_path(r'^%s(.*)' % _('nimda/'), include(admin.site.urls)),
    re_path(r'^nimda/', admin.site.urls),


    re_path(r'^sitemap.xml$', sitemap_index, {'sitemaps': sitemaps}),
    re_path(r'^sitemap-(?P<section>.+)-(?P<page>\d+)\.xml$', sitemap, {'sitemaps': sitemaps}, name="sitemap_section_page"),
    re_path(r'^sitemap-(?P<section>.+)\.xml$', sitemap_section_index, {'sitemaps': sitemaps}, name="sitemap_section_index"),

    re_path(r'^favicon\.ico$', app.meta.favicon),
    re_path(r'^cstyle\.css$', app.meta.custom_css, name='custom_css'),
    
    re_path(r'^m/django_admin/(?P<path>.*)$', app.meta.django_admin_media, name='django_admin_media'),
    re_path(r'^m/(?P<skin>\w+)/media/(?P<path>.*)$', app.meta.media , name='osqa_media'),
    re_path(r'^%s(?P<path>.*)$' % _('upfiles/'), serve, {'document_root': os.path.join(APP_PATH, 'upfiles').replace('\\', '/')}, name='uploaded_file',),
    
    re_path(r'^%s$' % _('faq/'), app.meta.static, {'content': settings.FAQ_PAGE_TEXT, 'title': _('FAQ')}, name='faq'),
    re_path(r'^%s$' % _('about/'), app.meta.static, {'content': settings.ABOUT_PAGE_TEXT, 'title': _('About')}, name='about'),
    re_path(r'^%s$' % _('markdown_help/'), app.meta.markdown_help, name='markdown_help'), re_path(r'^opensearch\.xml$', app.meta.opensearch, name='opensearch'),
    re_path(r'^opensearch\.xml$', app.meta.opensearch, name='opensearch'),
    re_path(r'^%s$' % _('privacy/'), app.meta.privacy, name='privacy'),
    re_path(r'^%s$' % _('logout/'), app.meta.logout, name='logout'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('answers/'), _('edit/')), app.writers.edit_answer, name='edit_answer'),
    re_path(r'^%s(?P<id>\d+)/$' % _('revisions/'), app.readers.revisions, name='revisions'),
    re_path(r'^%s$' % _('questions/'), app.readers.questions, name='questions'),
    re_path(r'^%s%s$' % (_('questions/'), _('ask/')), app.writers.ask, name='ask'),
    re_path(r'^%s%s$' % (_('questions/'), _('related_questions/')), app.commands.related_questions, name='related_questions'),

    re_path(r'^%s%s$' % (_('questions/'), _('unanswered/')), app.readers.unanswered, name='unanswered'),
    re_path(r'^%s(?P<mode>[\w\-]+)/(?P<user>\d+)/(?P<slug>.*)/$' % _('questions/'), app.readers.user_questions, name='user_questions'),
    
    
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('questions/'), _('edit/')), app.writers.edit_question, name='edit_question'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('questions/'), _('close/')), app.commands.close, kwargs=dict(close=True), name='close'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('questions/'), _('reopen/')), app.commands.close, kwargs=dict(close=False), name='reopen'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('questions/'), _('answer/')), app.writers.answer, name='answer'),
    re_path(r'^%s(?P<action>\w+)/$' % _('pending-data/'), app.writers.manage_pending_data, name='manage_pending_data'),
    
    re_path(r'^%s(?P<id>\d+)/(?P<vote_type>[a-z]+)/' % _('vote/'), app.commands.vote_post, name='vote_post'),
    re_path(r'^%s(?P<id>\d+)/$' % _('like_comment/'), app.commands.like_comment, name='like_comment'),
    re_path(r'^%s(?P<id>\d+)/' % _('comment/'), app.commands.comment, name='comment'),
    re_path(r'^%s(?P<id>\d+)/$' % _('delete_comment/'), app.commands.delete_comment, name='delete_comment'),
    re_path(r'^%s(?P<id>\d+)/$' % _('convert_comment/'), app.commands.convert_comment_to_answer, name='convert_comment'),
    re_path(r'^%s(?P<id>\d+)/$' % _('accept_answer/'), app.commands.accept_answer, name='accept_answer'),
    re_path(r'^%s(?P<id>\d+)/$' % _('answer_link/'), app.commands.answer_permanent_link, name='answer_permanent_link'),
    re_path(r'^%s(?P<id>\d+)/$' % _('mark_favorite/'), app.commands.mark_favorite, name='mark_favorite'),
    re_path(r'^%s%s(?P<user_id>\d+)/%s(?P<answer_id>\d+)/$' % (_('award_points/'), _('user/'), _('answer/')), app.commands.award_points, name='award_points'),
    
    re_path(r'^%s(?P<id>\d+)/' % _('flag/'), app.commands.flag_post, name='flag_post'),
    re_path(r'^%s(?P<id>\d+)/' % _('delete/'), app.commands.delete_post, name='delete_post'),
    re_path(r'^%s(?P<id>\d+)/(?P<user>\d+)?$' % _('subscribe/'), app.commands.subscribe, name='subscribe'),
    re_path(r'^%s(?P<id>\d+)/$' % _('subscribe/'), app.commands.subscribe, name='subscribe_simple'),
    re_path(r'^%s' % _('matching_tags/'), app.commands.matching_tags, name='matching_tags'),
    re_path(r'^%s' % _('matching_users/'), app.commands.matching_users, name='matching_users'),
    re_path(r'^%s(?P<id>\d+)/' % _('node_markdown/'), app.commands.node_markdown, name='node_markdown'),
    re_path(r'^%s(?P<id>\d+)/' % _('convert/'), app.commands.convert_to_comment, name='convert_to_comment'),
    re_path(r'^%s(?P<id>\d+)/' % _('convert_to_question/'), app.writers.convert_to_question,name='convert_to_question'),
    re_path(r'^%s(?P<id>\d+)/' % _('wikify/'), app.commands.wikify, name='wikify'),
    
    re_path(r'^%s(?P<id>\d+)/(?P<slug>[\w-]*)$' % _('question/'), redirect, {'url': '/questions/%(id)s/%(slug)s'}),
    re_path(r'^%s(?P<id>\d+)/?$' % _('questions/'), app.readers.question, name='question'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/(?P<answer>\d+)$' % _('questions/'), app.readers.question),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)$' % _('questions/'), app.readers.question, name='question'),
    
    
    re_path(r'^%s$' % _('tags/'), app.readers.tags, name='tags'),
    re_path(r'^%s(?P<tag>[^/]+)/%s$' % (_('tags/'), _('edit/')), app.writers.tag_edit, name='tag_edit'),
    re_path(r'^%s(?P<tag>.*)/$' % _('tags/'), app.readers.tag, name='tag_questions'),     
    re_path(r'^%s%s(?P<tag>[^/]+)/$' % (_('mark-tag/'),_('interesting/')), app.commands.mark_tag, kwargs={'reason':'good','action':'add'}, name='mark_interesting_tag'),     
    re_path(r'^%s%s(?P<tag>[^/]+)/$' % (_('mark-tag/'),_('ignored/')), app.commands.mark_tag, kwargs={'reason':'bad','action':'add'}, name='mark_ignored_tag'),     
    re_path(r'^%s(?P<tag>[^/]+)/$' % _('unmark-tag/'), app.commands.mark_tag, kwargs={'action':'remove'}, name='mark_ignored_tag'),     
    
    re_path(r'^%s$' % _('users/'), app.users.users, name='users'),
    # re_path(r'^%s$' % _('online_users/'), app.users.online_users, name='online_users'),    
    
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('edit/')), app.users.edit_user, name='edit_user'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('users/'), _('award/')), app.users.award_points, name='user_award_points'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('users/'), _('suspend/')), app.users.suspend, name='user_suspend'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('users/'), _('report/')), app.users.report_user, name='user_report'),
    re_path(r'^%s(?P<id>\d+)/%s(?P<action>[a-z]+)/(?P<status>[a-z]+)/$' % (_('users/'), _('powers/')), app.users.user_powers, name='user_powers'),
    re_path((core_user_urls_prefix + '/%s$') % (_('users/'), _('subscriptions/')), app.users.user_subscriptions, name='user_subscriptions'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('account/'), _('data/')), app.account.account_data, name='user_account_data'),
    re_path(r'^%s(?P<id>\d+)/%s%s$' % (_('account/'), _('data/'), _('export/')), app.account.export_data, name='user_export_data'),
    re_path(r'^%s(?P<id>\d+)/%s%s$' % (_('account/'), _('data/'), _('delete/')), app.account.delete_account, name='user_delete_account'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('preferences/')), app.users.user_preferences, name='user_preferences'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('favorites/')), app.users.user_favorites, name='user_favorites'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('reputation/')), app.users.user_reputation, name='user_reputation'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('votes/')), app.users.user_votes, name='user_votes'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>.*)/%s$' % (_('users/'), _('recent/')), app.users.user_recent, name='user_recent'),
    re_path(core_user_urls_prefix % _('users/'), app.users.user_profile, name='user_profile'),
    re_path(r'^%s$' % _('badges/'), app.meta.badges, name='badges'),
    re_path(r'^%s(?P<id>\d+)/(?P<slug>[\w-]+)?$' % _('badges/'), app.meta.badge, name='badge'),
    # (r'^admin/doc/' % _('admin/doc'), include('django.contrib.admindocs.urls')),
    
    re_path(r'^%s$' % _('upload/'), app.writers.upload, name='upload'),
    re_path(r'^%s$' % _('preview/'), app.writers.markdown_preview, name='markdown_preview'),
    re_path(r'^%s$' % _('search/'), app.readers.search, name='search'),
    re_path(r'^%s$' % _('contact/'), app.meta.feedback, name='feedback'),

    re_path(r'^i18n/', include('django.conf.urls.i18n')),

    re_path(r'^%s%s$' % (_('account/'), _('signin/')), app.auth.signin_page, name='auth_signin'),
    re_path(r'^%s%s$' % (_('account/'), _('signout/')), app.auth.signout, name='user_signout'),
    re_path(r'^%s(?P<provider>\w+)/%s$' % (_('account/'), _('signin/')), app.auth.prepare_provider_signin, name='auth_provider_signin'),
    re_path(r'^%s(?P<provider>\w+)/%s$' % (_('account/'), _('done/')), app.auth.process_provider_signin, name='auth_provider_done'),
    re_path(r'^%s%s$' % (_('account/'), _('register/')), app.auth.external_register, name='auth_external_register'),
    re_path(r'^%s%s(?P<user>\d+)/(?P<code>.+)/$' % (_('account/'), _('validate/')), app.auth.validate_email, name='auth_validate_email'),
    re_path(r'^%s%s$' % (_('account/'), _('tempsignin/')), app.auth.request_temp_login, name='auth_request_tempsignin'),
    re_path(r'^%s%s(?P<user>\d+)/(?P<code>.+)/$' % (_('account/'), _('tempsignin/')), app.auth.temp_signin, name='auth_tempsignin'),
    re_path(r'^%s(?P<id>\d+)/%s$' % (_('account/'), _('authsettings/')), app.auth.auth_settings, name='user_authsettings'),
    re_path(r'^%s%s(?P<id>\d+)/%s$' % (_('account/'), _('providers/'), _('remove/')), app.auth.remove_external_provider, name='user_remove_external_provider'),
    re_path(r'^%s%s%s$' % (_('account/'), _('providers/'), _('add/')), app.auth.signin_page, name='user_add_external_provider'),
    re_path(r'^%s%s$' %(_('account/'), _('send-validation/')), app.auth.send_validation_email, name='send_validation_email'),
    
    
    re_path(r'^%s$' % _('admin/'), app.admin.dashboard, name='admin_index'),
    re_path(r'^%s%s$' % (_('admin/'), _('switch_interface/')), app.admin.interface_switch, name='admin_switch_interface'),
    re_path(r'^%s%s$' % (_('admin/'), _('statistics/')), app.admin.statistics, name='admin_statistics'),
    re_path(r'^%s%s$' % (_('admin/'), _('denormalize/')), app.admin.recalculate_denormalized, name='admin_denormalize'),
    re_path(r'^%s%s$' % (_('admin/'), _('go_bootstrap/')), app.admin.go_bootstrap, name='admin_go_bootstrap'),
    re_path(r'^%s%s$' % (_('admin/'), _('go_defaults/')), app.admin.go_defaults, name='admin_go_defaults'),
    re_path(r'^%s%s(?P<set_name>\w+)/(?P<var_name>\w+)/$' % (_('admin/'), _('settings/')), app.admin.get_default, name='admin_default'),
    re_path(r'^%s%s$' % (_('admin/'), _('maintenance/')), app.admin.maintenance, name='admin_maintenance'),
    re_path(r'^%s%s$' % (_('admin/'), _('flagged_posts/')), app.admin.flagged_posts, name='admin_flagged_posts'),
    re_path(r'^%s%s$' % (_('admin/'), _('static_pages/')), app.admin.static_pages, name='admin_static_pages'),
    
    re_path(r'^%s%s%s$' % (_('admin/'), _('static_pages/'), _('new/')), app.admin.edit_page, name='admin_new_page'),
    re_path(r'^%s%s%s(?P<id>\d+)/$' % (_('admin/'), _('static_pages/'), _('edit/')), app.admin.edit_page, name='admin_edit_page'),
    re_path(r'^%s%s%s(?P<id>\d+)/$' % (_('admin/'), _('static_pages/'), _('delete/')), app.admin.delete_page, name='admin_delete_page'),

    re_path(r'^%s%s(?P<name>\w+)/$' % (_('admin/'), _('tools/')), app.admin.tools_page, name='admin_tools'),
    
    re_path(r'^%s%s(?P<set_name>\w+)/$' % (_('admin/'), _('settings/')), app.admin.settings_set, name='admin_set'),
    
    re_path(r'%s%s' % (_('admin/'), _('test_email_settings/')), app.admin.test_email_settings, name='test_email_settings'),
    
    re_path(r'^feeds/rss[/]?$', app.readers.feed, name='latest_questions_feed'),

]

from forum.modules import get_modules_script

module_patterns = get_modules_script('urls')

urlpatterns = []

for pattern_file in module_patterns:
    pattern = getattr(pattern_file, 'urlpatterns', None)
    if pattern:
        urlpatterns += pattern

module_defined = {}

for t in urlpatterns:
    if hasattr(t, 'name') and t.name:
        module_defined[t.name] = True

core_defined = []

for u in core_urls:
    if not(hasattr(u, 'name') and u.name and (u.name in module_defined)):
        core_defined.append(u)

def urlname(name):
    if name in module_defined:
        return None
    return name

urlpatterns += core_defined

# Django Debug Toolbar URLs
if djsettings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns = [
            re_path(r'^__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
    except ImportError:
        pass


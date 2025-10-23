# OSQA Database Tables - Quick Reference

**Total Tables:** 66
**Database File:** `osqa.db`
**Django Version:** 4.2.11


## Django Core (11 tables)

### `auth_group`
- **Records:** 0
- **Fields:** 2
- **Key columns:** id, name

### `auth_group_permissions`
- **Records:** 0
- **Fields:** 3
- **Key columns:** id, group_id, permission_id

### `auth_permission`
- **Records:** 272
- **Fields:** 4
- **Key columns:** id, content_type_id, codename, name

### `auth_user`
- **Records:** 0
- **Fields:** 11
- **Key columns:** id, password, last_login, is_superuser, username, ...

### `auth_user_groups`
- **Records:** 0
- **Fields:** 3
- **Key columns:** id, user_id, group_id

### `auth_user_user_permissions`
- **Records:** 0
- **Fields:** 3
- **Key columns:** id, user_id, permission_id

### `django_admin_log`
- **Records:** 0
- **Fields:** 8
- **Key columns:** id, object_id, object_repr, action_flag, change_message, ...

### `django_content_type`
- **Records:** 68
- **Fields:** 3
- **Key columns:** id, app_label, model

### `django_migrations`
- **Records:** 21
- **Fields:** 4
- **Key columns:** id, app, name, applied

### `django_session`
- **Records:** 5
- **Fields:** 3
- **Key columns:** session_key, session_data, expire_date

### `django_site`
- **Records:** 1
- **Fields:** 3
- **Key columns:** id, name, domain


## User Management (3 tables)

### `forum_authkeyuserassociation`
- **Records:** 0
- **Fields:** 5
- **Key columns:** id, key, provider, added_at, user_id

### `forum_user`
- **Records:** 0
- **Fields:** 13
- **Key columns:** user_ptr_id, is_approved, email_isvalid, reputation, gold, ...

### `forum_userproperty`
- **Records:** 0
- **Fields:** 4
- **Key columns:** id, key, value, user_id


## Content (Nodes) (5 tables)

### `forum_node`
- **Records:** 0
- **Fields:** 19
- **Key columns:** id, title, tagnames, body, node_type, ...

### `forum_node_tags`
- **Records:** 0
- **Fields:** 3
- **Key columns:** id, node_id, tag_id

### `forum_nodeeditaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_noderevision`
- **Records:** 0
- **Fields:** 9
- **Key columns:** id, title, tagnames, body, summary, ...

### `forum_nodestate`
- **Records:** 0
- **Fields:** 4
- **Key columns:** id, state_type, action_id, node_id


## Tags (3 tables)

### `forum_markedtag`
- **Records:** 0
- **Fields:** 4
- **Key columns:** id, reason, tag_id, user_id

### `forum_retagaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_tag`
- **Records:** 0
- **Fields:** 5
- **Key columns:** id, name, created_at, used_count, created_by_id


## Actions (34 tables)

### `forum_acceptansweraction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_action`
- **Records:** 0
- **Fields:** 12
- **Key columns:** id, ip, action_type, action_date, extra, ...

### `forum_actionrepute`
- **Records:** 0
- **Fields:** 6
- **Key columns:** id, date, value, by_canceled, action_id, ...

### `forum_answeraction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** nodeeditaction_ptr_id

### `forum_answertocommentaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_answertoquestionaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** nodeeditaction_ptr_id

### `forum_askaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** nodeeditaction_ptr_id

### `forum_awardaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_awardpointsaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_bonusrepaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_closeaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_commentaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_commenttoansweraction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_commenttoquestionaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** nodeeditaction_ptr_id

### `forum_deleteaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_editpageaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_editprofileaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_emailvalidationaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_favoriteaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_flagaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_newpageaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_publishaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_reportaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_reviseaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** nodeeditaction_ptr_id

### `forum_rollbackaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_suspendaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_unknownaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_userjoinsaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_userloginaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_voteaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id

### `forum_votedownaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** voteaction_ptr_id

### `forum_voteupaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** voteaction_ptr_id

### `forum_voteupcommentaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** voteupaction_ptr_id

### `forum_wikifyaction`
- **Records:** 0
- **Fields:** 1
- **Key columns:** action_ptr_id


## Votes (1 tables)

### `forum_vote`
- **Records:** 0
- **Fields:** 6
- **Key columns:** id, value, voted_at, action_id, node_id, ...


## Badges & Awards (2 tables)

### `forum_award`
- **Records:** 0
- **Fields:** 7
- **Key columns:** id, awarded_at, action_id, badge_id, node_id, ...

### `forum_badge`
- **Records:** 0
- **Fields:** 4
- **Key columns:** id, type, cls, awarded_count


## Settings (2 tables)

### `forum_keyvalue`
- **Records:** 0
- **Fields:** 3
- **Key columns:** id, key, value

### `forum_subscriptionsettings`
- **Records:** 0
- **Fields:** 16
- **Key columns:** id, enable_notifications, member_joins, new_question, new_question_watched_tags, ...


## Authentication (2 tables)

### `forum_openidassociation`
- **Records:** 0
- **Fields:** 7
- **Key columns:** id, server_url, handle, secret, issued, ...

### `forum_openidnonce`
- **Records:** 0
- **Fields:** 4
- **Key columns:** id, server_url, timestamp, salt


## Other (3 tables)

### `forum_flag`
- **Records:** 0
- **Fields:** 6
- **Key columns:** id, reason, flagged_at, action_id, node_id, ...

### `forum_questionsubscription`
- **Records:** 0
- **Fields:** 5
- **Key columns:** id, auto_subscription, last_view, question_id, user_id

### `forum_validationhash`
- **Records:** 0
- **Fields:** 6
- **Key columns:** id, hash_code, seed, expiration, type, ...


---

## Notes

- **Empty database:** All tables show 0 records (except system tables)
- **Ready for migration:** Schema is created and waiting for data
- **Action tables:** 34 different action types track all user activities
- **Polymorphic design:** `forum_node` contains questions, answers, and comments
- **See `DATABASE_MIGRATION_GUIDE.md` for detailed migration instructions**

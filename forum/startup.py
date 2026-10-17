import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__),'markdownext'))

from forum.modules import get_modules_script

get_modules_script('settings')
get_modules_script('startup')


import forum.badges
import forum.subscriptions
import forum.notifications
import forum.review_notices  # noqa: F401  (first posts review: author notices)
import forum.registry  # noqa: F401  (fills the UI registry)
get_modules_script('registry')




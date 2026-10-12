import re

import markdown
from markdown.preprocessors import Preprocessor

from forum import settings


class SettingsExtension(markdown.Extension):
    def extendMarkdown(self, md):
        md.registerExtension(self)
        md.preprocessors.register(SettingsPre(md), 'MinRep', 100)

SETTING_RE = re.compile(r'\|[A-Z_]+\|')

def setting_rep_callback(m):
    setting_name = m.group(0).strip('|')
    if hasattr(settings, setting_name):
        return str(getattr(settings, setting_name))
    else:
        return ''


class SettingsPre(Preprocessor):
    def run(self, lines):
        return [SETTING_RE.sub(setting_rep_callback, line) for line in lines]


def makeExtension(**kwargs):
    return SettingsExtension(**kwargs)

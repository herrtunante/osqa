import os
import re

from django.template.loaders.base import Loader as BaseLoader
from django.template import Origin
from django.conf import settings
from forum.skins import SkinsTemplateLoader

MODULES_TEMPLATE_PREFIX = 'modules/'
NO_OVERRIDE_TEMPLATE_PREFIX = 'no_override/'
MODULES_TEMPLATE_FOLDER = 'templates'
MODULES_TEMPLATE_OVERRIDES_FOLDER = 'template_overrides'

TEMPLATE_OVERRIDE_LOOKUP_PATHS = [f for f in [
        os.path.join(os.path.dirname(m.__file__), MODULES_TEMPLATE_OVERRIDES_FOLDER) for m in settings.MODULE_LIST
    ] if os.path.exists(f)
]


class ModulesTemplateLoader(BaseLoader):
    """Django 4.2 compatible template loader for OSQA modules system"""

    modules_re = re.compile('^%s(\w+)\/(.*)$' % MODULES_TEMPLATE_PREFIX)

    def get_template_sources(self, template_name):
        """
        Generator that yields possible template sources (Origin objects).
        """
        if template_name.startswith(MODULES_TEMPLATE_PREFIX):
            # Handle modules/ prefixed templates
            match = self.modules_re.search(template_name)
            if match:
                file_name = os.path.join(
                    settings.MODULES_FOLDER,
                    match.group(1),
                    MODULES_TEMPLATE_FOLDER,
                    match.group(2)
                )
                if os.path.exists(file_name):
                    yield Origin(
                        name=file_name,
                        template_name=template_name,
                        loader=self,
                    )

        elif template_name.startswith(NO_OVERRIDE_TEMPLATE_PREFIX):
            # Delegate to skins loader without override prefix
            stripped_name = template_name[len(NO_OVERRIDE_TEMPLATE_PREFIX):]
            # Instantiate the skins loader with our engine
            skins_loader = SkinsTemplateLoader(self.engine)
            for origin in skins_loader.get_template_sources(stripped_name):
                yield origin

        else:
            # Check template override folders from modules
            for override_path in TEMPLATE_OVERRIDE_LOOKUP_PATHS:
                file_name = os.path.join(override_path, template_name)
                if os.path.exists(file_name):
                    yield Origin(
                        name=file_name,
                        template_name=template_name,
                        loader=self,
                    )

    def get_contents(self, origin):
        """
        Read and return the template source from the given origin.
        """
        try:
            with open(origin.name, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            # If we can't read it, delegate to parent class
            return super().get_contents(origin)


# Reference to loader class for use in settings
# Django will instantiate this with the proper engine parameter
module_templates_loader = ModulesTemplateLoader

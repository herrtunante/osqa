from django.conf import settings
from django.template.loaders.base import Loader as BaseLoader
from django.template import TemplateDoesNotExist, Origin
from django.conf import settings as djsettings
import os.path
import logging

SKINS_FOLDER = os.path.dirname(__file__)
SKIN_TEMPLATES_FOLDER = 'templates'
DEFAULT_SKIN_NAME = 'default'
FORCE_DEFAULT_PREFIX = "%s/" % DEFAULT_SKIN_NAME


class SkinsTemplateLoader(BaseLoader):
    """Django 4.2 compatible template loader for OSQA skins system"""

    def get_template_sources(self, template_name):
        """
        Generator that yields possible template sources (Origin objects).
        """
        if template_name.startswith(FORCE_DEFAULT_PREFIX):
            # Handle forced default skin templates
            file_name = os.path.join(
                SKINS_FOLDER,
                DEFAULT_SKIN_NAME,
                SKIN_TEMPLATES_FOLDER,
                template_name[len(FORCE_DEFAULT_PREFIX):]
            )
            if os.path.exists(file_name):
                yield Origin(
                    name=file_name,
                    template_name=template_name,
                    loader=self,
                )
        else:
            # Check configured skin, then default skin
            for skin in (settings.OSQA_DEFAULT_SKIN, DEFAULT_SKIN_NAME):
                file_name = os.path.join(SKINS_FOLDER, skin, SKIN_TEMPLATES_FOLDER, template_name)
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
        except FileNotFoundError:
            raise TemplateDoesNotExist(origin.name)
        except Exception as e:
            logging.error(f"Error reading template {origin.name}: {e}")
            raise TemplateDoesNotExist(origin.name)


# Reference to loader class for use in settings
# Django will instantiate this with the proper engine parameter
load_template_source = SkinsTemplateLoader


def find_media_source(url):
    """returns url prefixed with the skin name
    of the first skin that contains the file
    directories are searched in this order:
    settings.OSQA_DEFAULT_SKIN, then 'default', then 'commmon'
    if file is not found - returns None
    and logs an error message
    """
    while url[0] == '/': url = url[1:]
    d = os.path.dirname
    n = os.path.normpath
    j = os.path.join
    f = os.path.isfile
    skins = n(j(d(d(__file__)),'skins'))
    try:
        media = os.path.join(skins, settings.OSQA_DEFAULT_SKIN, url)
        assert(f(media))
        use_skin = settings.OSQA_DEFAULT_SKIN
    except:
        try:
            media = j(skins, 'default', url)
            assert(f(media))
            use_skin = 'default'
        except:
            media = j(skins, 'common', url)
            try:
                assert(f(media))
                use_skin = 'common'
            except:
                logging.error('could not find media for %s' % url)
                use_skin = ''
                return None
    return use_skin + '/' + url


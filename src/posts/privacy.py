"""Personal data of posts: the frozen author name is replaced when an account is anonymized."""

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext

from core.templatetags.component_tags import member_name

from .models import Comment, Post

AUTHOR_DISPLAY_MAX_LENGTH = Post._meta.get_field("author_display").max_length


def author_display_for(user) -> str:
    """The author name frozen on a new post or comment: the full name, else the e-mail's
    local part (never the address itself), in the site's default language."""
    with translation.override(settings.LANGUAGE_CODE):
        return member_name(user)[:AUTHOR_DISPLAY_MAX_LENGTH]


def anonymize_author(user) -> None:
    """Replace ``author_display`` on the posts and comments of ``user`` (inside the
    anonymization transaction). The name is stored in the site's default language."""
    with translation.override(settings.LANGUAGE_CODE):
        name = gettext("Former employee")
    Post.objects.filter(author=user).update(author_display=name)
    Comment.objects.filter(author=user).update(author_display=name)

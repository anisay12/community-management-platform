"""Personal data of posts: the frozen author name is replaced when an account is anonymized."""

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext

from .models import Comment, Post


def anonymize_author(user) -> None:
    """Replace ``author_display`` on the posts and comments of ``user`` (inside the
    anonymization transaction). The name is stored in the site's default language."""
    with translation.override(settings.LANGUAGE_CODE):
        name = gettext("Former employee")
    Post.objects.filter(author=user).update(author_display=name)
    Comment.objects.filter(author=user).update(author_display=name)

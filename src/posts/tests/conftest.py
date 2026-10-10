import pytest
from django.utils import timezone

from posts.models import Comment, Post
from posts.rendering import render_body


@pytest.fixture
def make_post():
    """Create a post directly (ORM only), published unless ``status`` says otherwise."""

    def _make(community, author, **extra):
        extra.setdefault("title", "A post")
        extra.setdefault("body", "Some **body**")
        extra.setdefault("status", Post.Status.PUBLISHED)
        if extra["status"] == Post.Status.PUBLISHED:
            extra.setdefault("published_at", timezone.now())
        extra.setdefault("body_html", render_body(extra["body"]))
        extra.setdefault("author_display", author.get_full_name() if author else "")
        return Post.objects.create(community=community, author=author, **extra)

    return _make


@pytest.fixture
def make_comment():
    """Create a comment directly (ORM only)."""

    def _make(post, author, parent=None, **extra):
        extra.setdefault("body", "A comment")
        extra.setdefault("body_html", render_body(extra["body"]))
        extra.setdefault("author_display", author.get_full_name() if author else "")
        return Comment.objects.create(post=post, author=author, parent=parent, **extra)

    return _make

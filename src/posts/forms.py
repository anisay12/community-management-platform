"""Forms of the post editor and of the post actions (Task 5).

Forms only shape and bound the input; the business rules (allowed kinds, tag creation,
versions) are enforced by ``posts.services_posts`` and reported back as form errors.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import POST_BODY_MAX_LENGTH, Post

TITLE_MAX_LENGTH = Post._meta.get_field("title").max_length
REASON_MAX_LENGTH = 2000
TAGS_MAX_LENGTH = 1000


def split_tags(value: str) -> list[str]:
    """Tag names typed as a comma-separated list (blank entries dropped)."""
    return [name.strip() for name in (value or "").split(",") if name.strip()]


class PostForm(forms.Form):
    """Title, Markdown body and tags; ``kind`` on creation only, ``version`` on edition."""

    kind = forms.ChoiceField(label=_("Kind"), choices=Post.Kind.choices)
    title = forms.CharField(label=_("Title"), max_length=TITLE_MAX_LENGTH)
    body = forms.CharField(
        label=_("Text"),
        required=False,
        max_length=POST_BODY_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 12}),
        help_text=_(
            "Markdown is supported: **bold**, *italic*, lists, links and `code`. "
            "Mention a member with @first.last."
        ),
    )
    tags = forms.CharField(
        label=_("Tags"),
        required=False,
        max_length=TAGS_MAX_LENGTH,
        help_text=_("Separate tags with commas."),
    )
    version = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)

    def __init__(self, *args, kinds=None, editing=False, can_create_tags=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["kind"]
        else:
            del self.fields["version"]
            # Every kind validates (the service answers ``kind_forbidden``); only the allowed
            # ones are offered.
            self.fields["kind"].widget.choices = [
                (value, label) for value, label in Post.Kind.choices if value in (kinds or ())
            ]
        self.fields["body"].widget.attrs.update(
            {"maxlength": POST_BODY_MAX_LENGTH, "data-tl-counter": "post-body-counter"}
        )
        self.fields["tags"].widget.attrs.update({"list": "tag-suggestions", "autocomplete": "off"})
        if can_create_tags:
            self.fields["tags"].help_text = _(
                "Separate tags with commas. Pick an existing tag or type a new one."
            )
        else:
            self.fields["tags"].help_text = _(
                "Separate tags with commas. Only existing tags can be used."
            )

    def tag_names(self) -> list[str]:
        return split_tags(self.cleaned_data.get("tags", ""))


class ReasonForm(forms.Form):
    """The mandatory reason of a hiding or of a review rejection."""

    reason = forms.CharField(
        label=_("Reason"),
        max_length=REASON_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ReviewRejectForm(forms.Form):
    """The mandatory note sent back to the author of a refused post."""

    note = forms.CharField(
        label=_("Note to the author"),
        max_length=REASON_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={"required": _("Explain to the author why the post is refused.")},
    )


class ShareForm(forms.Form):
    target = forms.ChoiceField(label=_("Community"))
    title = forms.CharField(label=_("Title"), max_length=TITLE_MAX_LENGTH)
    comment = forms.CharField(
        label=_("Comment (optional)"),
        required=False,
        max_length=POST_BODY_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Markdown is supported."),
    )

    def __init__(self, *args, targets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = [(c.slug, c.name) for c in targets]

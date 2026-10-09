from django import forms
from django.utils.translation import gettext_lazy as _

from .models import User


class UserAddForm(forms.ModelForm):
    """Admin "add user" form: users are invited, so no password is set at creation."""

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                _("A user with this email address already exists."), code="duplicate_email"
            )
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.status = User.Status.PENDING
        user.set_unusable_password()
        if commit:
            user.save()
            self.save_m2m()
        return user

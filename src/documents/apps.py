from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "documents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        self._register_pages()
        self._register_privacy()

    def _register_pages(self):
        """Community tab, navigation entries and dashboard blocks of the document pages."""

    def _register_privacy(self):
        """GDPR export and erasure hooks for documents and download logs."""

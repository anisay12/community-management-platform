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
        from accounts.privacy import register_anonymizer, register_exporter

        from .privacy import anonymize_documents, export_documents

        register_exporter("documents", export_documents)
        register_anonymizer(anonymize_documents)

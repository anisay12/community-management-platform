from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "documents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        self._register_pages()
        self._register_privacy()

    def _register_pages(self):
        """Community tab of the document pages (the dashboard blocks live in
        ``core.dashboard``; no sidebar entry: resources are reached through the community)."""
        from django.utils.translation import gettext_lazy as _

        from communities import tabs
        from communities.policies import can_view_content

        tabs.register(
            tabs.Tab(
                key="resources",
                label=_("Resources"),
                url_name="documents:community_documents",
                order=20,
                is_visible=can_view_content,
            )
        )

    def _register_privacy(self):
        """GDPR export and erasure hooks for documents and download logs."""
        from accounts.privacy import register_anonymizer, register_exporter

        from .privacy import anonymize_documents, export_documents

        register_exporter("documents", export_documents)
        register_anonymizer(anonymize_documents)

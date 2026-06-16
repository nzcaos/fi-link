from django.apps import AppConfig


class MatrixConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "matrix"
    verbose_name = "Matrix-Messenger"

    def ready(self):
        from . import signals  # noqa: F401  (connects membership-sync receivers)

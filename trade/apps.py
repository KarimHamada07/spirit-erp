from django.apps import AppConfig
from django.db.models.signals import post_migrate


class TradeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trade'
    verbose_name = 'إدارة المحل'

    def ready(self):
        from . import audit, signals  # noqa: F401  (سجل المراجعة + أحداث النظام)
        from .roles import ensure_roles
        from .treasury import ensure_defaults
        post_migrate.connect(ensure_roles, sender=self,
                             dispatch_uid="trade_ensure_roles")
        post_migrate.connect(ensure_defaults, sender=self,
                             dispatch_uid="trade_ensure_defaults")

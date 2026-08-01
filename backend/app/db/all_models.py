"""Imports every module's models so Base.metadata is complete.

Used by alembic/env.py and the test bootstrap. Every new module MUST be
added here when its models.py lands.
"""
from app.modules.admin import models as admin_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.credit import models as credit_models  # noqa: F401
from app.modules.customers import models as customers_models  # noqa: F401
from app.modules.fulfilment import models as fulfilment_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.notifications import models as notifications_models  # noqa: F401
from app.modules.orders import models as orders_models  # noqa: F401
from app.modules.payments import models as payments_models  # noqa: F401
from app.modules.pricing import models as pricing_models  # noqa: F401

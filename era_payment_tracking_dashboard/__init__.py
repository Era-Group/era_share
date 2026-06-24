from . import models


def post_init_hook(env):
    """عند التثبيت: ينشئ متابعة لكل أمر بيع مؤكَّد موجود مسبقًا."""
    env['payment.tracking']._backfill_existing_orders()

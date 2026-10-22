"""
middlewares paketi — aiogram 3.x middleware'lari.

Ulashish (bot.py):
    from middlewares.subscription import SubscriptionMiddleware
    dp.update.outer_middleware(SubscriptionMiddleware())
"""

from middlewares.subscription import SUBSCRIPTION_PROMPT_TEXT, SubscriptionMiddleware
from middlewares.throttling import ThrottlingMiddleware

__all__ = [
    "SubscriptionMiddleware",
    "SUBSCRIPTION_PROMPT_TEXT",
    "ThrottlingMiddleware",
]

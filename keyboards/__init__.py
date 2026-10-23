"""
keyboards paketi — botning barcha klaviaturalari.

Modullar:
 - keyboards.reply    : doimiy asosiy reply menyu
 - keyboards.inline   : obuna, do'kon, referal, balans, to'ldirish
 - keyboards.builders : admin panel boshqaruvi tugmalari

Barcha tugmalar `services.button_registry` orqali quriladi (rang + premium emoji).

Tez import:
    from keyboards import get_main_menu_kb, get_shop_categories_kb
"""

from keyboards.builders import (
    get_admin_channel_manage_kb,
    get_admin_main_kb,
    get_admin_product_list_kb,
    get_admin_products_kb,
    get_button_edit_kb,
    get_buttons_manage_kb,
    get_product_category_kb,
    get_product_delete_confirm_kb,
    get_product_detail_kb,
    get_withdrawal_decision_kb,
)
from keyboards.inline import (
    get_balance_kb,
    get_deposit_decision_kb,
    get_payment_methods_kb,
    get_products_list_kb,
    get_referral_kb,
    get_shop_categories_kb,
    get_subscription_kb,
    get_topup_cancel_kb,
)
from keyboards.reply import get_main_menu_kb

__all__ = [
    "get_main_menu_kb",
    "get_subscription_kb",
    "get_shop_categories_kb",
    "get_products_list_kb",
    "get_payment_methods_kb",
    "get_referral_kb",
    "get_balance_kb",
    "get_topup_cancel_kb",
    "get_deposit_decision_kb",
    "get_admin_main_kb",
    "get_admin_channel_manage_kb",
    "get_withdrawal_decision_kb",
    "get_buttons_manage_kb",
    "get_button_edit_kb",
    "get_admin_products_kb",
    "get_product_category_kb",
    "get_admin_product_list_kb",
    "get_product_detail_kb",
    "get_product_delete_confirm_kb",
]

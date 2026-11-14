from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from trade.activity_views import activity, activity_ack, activity_feed
from trade.icon_views import apple_touch_icon
from trade.custody_views import (custody, custody_deposit, custody_detail,
                                 custody_receipt, custody_screenshot, custody_settle)
from trade.audit_views import audit
from trade.delete_views import (customer_delete, model_delete, purchase_delete,
                                 sale_delete)
from trade.report_views import report_export, report_print, reports
from trade.search_views import search_api
from trade.customer_views import (customer_add, customer_detail, customer_pay, customer_set_seller,
                                  customer_statement_pdf, customers)
from trade.online_views import (online_create, online_detail, online_list,
                                online_new, online_status)
from trade.pos_views import pos, pos_checkout, pos_customer_prices, sale_pdf, sale_print
from trade.stock_views import inventory, inventory_detail, purchase_confirm
from trade.treasury_views import (commissions_view, daily_close_view,
                                  treasury, treasury_action)
from trade.views import dashboard

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("apple-touch-icon.png", apple_touch_icon),
    path("apple-touch-icon-precomposed.png", apple_touch_icon),
    path("dashboard/", dashboard, name="dashboard"),
    path("pos/", pos, name="pos"),
    path("pos/checkout/", pos_checkout, name="pos_checkout"),
    path("pos/customer-prices/", pos_customer_prices, name="pos_customer_prices"),
    path("invoice/<int:pk>/", sale_print, name="sale_print"),
    path("invoice/<int:pk>/pdf/", sale_pdf, name="sale_pdf"),
    path("inventory/", inventory, name="inventory"),
    path("inventory/<int:pk>/", inventory_detail, name="inventory_detail"),
    path("purchase/<int:pk>/confirm/", purchase_confirm, name="purchase_confirm"),
    path("activity/", activity, name="activity"),
    path("activity/feed/", activity_feed, name="activity_feed"),
    path("activity/ack/", activity_ack, name="activity_ack"),
    path("custody/", custody, name="custody"),
    path("custody/settle/", custody_settle, name="custody_settle"),
    path("custody/deposit/", custody_deposit, name="custody_deposit"),
    path("custody/<int:pk>/", custody_detail, name="custody_detail"),
    path("custody/<int:pk>/screenshot/", custody_screenshot, name="custody_screenshot"),
    path("custody/<int:pk>/receipt/", custody_receipt, name="custody_receipt"),
    path("search/api/", search_api, name="search_api"),
    path("reports/", reports, name="reports"),
    path("reports/export/", report_export, name="report_export"),
    path("reports/print/", report_print, name="report_print"),
    path("audit/", audit, name="audit"),
    path("customers/<int:pk>/delete/", customer_delete, name="customer_delete"),
    path("inventory/<int:pk>/delete/", model_delete, name="model_delete"),
    path("sales/<int:pk>/delete/", sale_delete, name="sale_delete"),
    path("purchases/<int:pk>/delete/", purchase_delete, name="purchase_delete"),
    path("customers/", customers, name="customers"),
    path("customers/add/", customer_add, name="customer_add"),
    path("customers/<int:pk>/", customer_detail, name="customer_detail"),
    path("customers/<int:pk>/pay/", customer_pay, name="customer_pay"),
    path("customers/<int:pk>/seller/", customer_set_seller, name="customer_set_seller"),
    path("customers/<int:pk>/statement/pdf/", customer_statement_pdf, name="customer_statement_pdf"),
    path("treasury/", treasury, name="treasury"),
    path("treasury/action/", treasury_action, name="treasury_action"),
    path("treasury/daily/", daily_close_view, name="daily_close"),
    path("treasury/commissions/", commissions_view, name="commissions"),
    path("online/", online_list, name="online_list"),
    path("online/new/", online_new, name="online_new"),
    path("online/create/", online_create, name="online_create"),
    path("online/<int:pk>/", online_detail, name="online_detail"),
    path("online/<int:pk>/status/", online_status, name="online_status"),
    # زر Dashboard في القائمة يفتح لوحة التحكم الجديدة
    path("admin/", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("admin/", admin.site.urls),
]



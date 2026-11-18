from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from trade.activity_views import activity, activity_ack, activity_feed
from trade.icon_views import apple_touch_icon
from trade.custody_views import (clearance_request, clearance_review, clearance_screenshot,
                                 custody, custody_approvals, custody_deposit, custody_detail,
                                 custody_receipt, custody_screenshot, custody_settle,
                                 holder_detail, my_custody)
from trade.analytics_views import analytics
from trade.attendance_views import (attendance_photo, attendance_report, my_attendance, punch,
                                    workplace_add)
from trade.audit_views import audit
from trade.delete_views import (customer_delete, model_delete, purchase_delete,
                                 sale_delete)
from trade.report_views import profit_report, report_export, report_print, reports
from trade.search_views import search_api
from trade.quick_views import quick_expense, quick_payment
from trade.customer_views import (customer_add, customer_detail, customer_pay, customer_set_seller,
                                  customer_statement_pdf, customers, statement_public)
from trade.online_views import (online_create, online_detail, online_list,
                                online_new, online_status)
from trade.pwa_views import manifest, offline, service_worker
from trade.pos_views import (invoice_public, sale_approvals, pos_return_customer, return_print, pos, pos_checkout, pos_customer_prices, pos_return,
                             pos_return_lookup, sale_pdf, sale_print)
from trade.stock_views import inventory, inventory_detail, purchase_confirm
from trade.treasury_views import (commissions_view, daily_close_view,
                                  treasury, treasury_action)
from trade.data_views import data_center, data_confirm, data_file, data_preview, data_upload
from trade.debt_views import debt_followup_add, debts
from trade.supplier_views import supplier_statement_view, suppliers
from trade.employee_views import (employee_detail, employee_statement_file, employee_txn_add, employees,
                                  leave_request, leave_review, leaves, my_statement, payroll, payroll_approve)
from trade.mission_views import (mission_photo, mission_point, mission_start, missions_report,
                                 my_missions)
from trade.stock_count_views import (reorder, reorder_create, stock_count_action, stock_count_detail,
                                     stock_count_set, stock_counts)
from trade.views import dashboard

from trade import coding_views
from trade import store_admin_views as store_admin
from trade.store_views import robots as store_robots

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("apple-touch-icon.png", apple_touch_icon),
    path("manifest.webmanifest", manifest, name="pwa_manifest"),
    path("sw.js", service_worker, name="pwa_sw"),
    path("offline/", offline, name="pwa_offline"),
    path("apple-touch-icon-precomposed.png", apple_touch_icon),
    path("dashboard/", dashboard, name="dashboard"),
    path("pos/", pos, name="pos"),
    path("pos/checkout/", pos_checkout, name="pos_checkout"),
    path("pos/customer-prices/", pos_customer_prices, name="pos_customer_prices"),
    path("pos/return/lookup/", pos_return_lookup, name="pos_return_lookup"),
    path("pos/return/", pos_return, name="pos_return"),
    path("invoice/<int:pk>/", sale_print, name="sale_print"),
    path("invoice/<int:pk>/pdf/", sale_pdf, name="sale_pdf"),
    path("i/<str:token>/", invoice_public, name="invoice_public"),
    path("s/<str:token>/", statement_public, name="statement_public"),
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
    path("custody/mine/", my_custody, name="my_custody"),
    path("custody/approvals/", custody_approvals, name="custody_approvals"),
    path("custody/holders/<int:pk>/", holder_detail, name="custody_holder"),
    path("custody/holders/<int:pk>/clear/", clearance_request, name="custody_clearance_request"),
    path("custody/clearance/<int:pk>/review/", clearance_review, name="custody_clearance_review"),
    path("custody/clearance/<int:pk>/proof/", clearance_screenshot, name="custody_clearance_proof"),
    path("custody/<int:pk>/screenshot/", custody_screenshot, name="custody_screenshot"),
    path("custody/<int:pk>/receipt/", custody_receipt, name="custody_receipt"),
    path("search/api/", search_api, name="search_api"),
    path("reports/", reports, name="reports"),
    path("reports/export/", report_export, name="report_export"),
    path("reports/print/", report_print, name="report_print"),
    path("reports/profit/", profit_report, name="profit_report"),
    path("analytics/", analytics, name="analytics"),
    path("attendance/", my_attendance, name="my_attendance"),
    path("attendance/punch/", punch, name="attendance_punch"),
    path("attendance/report/", attendance_report, name="attendance_report"),
    path("attendance/workplace/add/", workplace_add, name="workplace_add"),
    path("attendance/<int:pk>/photo/", attendance_photo, name="attendance_photo"),
    path("missions/", my_missions, name="my_missions"),
    path("missions/start/", mission_start, name="mission_start"),
    path("missions/<int:pk>/point/", mission_point, name="mission_point"),
    path("missions/report/", missions_report, name="missions_report"),
    path("missions/photo/<int:pk>/", mission_photo, name="mission_photo"),
    path("employees/", employees, name="employees"),
    path("employees/me/", my_statement, name="my_statement"),
    path("employees/payroll/", payroll, name="payroll"),
    path("employees/payroll/approve/", payroll_approve, name="payroll_approve"),
    path("employees/<int:user_id>/", employee_detail, name="employee_detail"),
    path("employees/<int:user_id>/add/", employee_txn_add, name="employee_txn_add"),
    path("employees/<int:user_id>/statement/<str:fmt_>/", employee_statement_file, name="employee_statement_file"),
    path("leaves/", leaves, name="leaves"),
    path("leaves/request/", leave_request, name="leave_request"),
    path("leaves/<int:pk>/review/", leave_review, name="leave_review"),
    path("stock-count/", stock_counts, name="stock_counts"),
    path("stock-count/<int:pk>/", stock_count_detail, name="stock_count_detail"),
    path("stock-count/<int:pk>/set/", stock_count_set, name="stock_count_set"),
    path("stock-count/<int:pk>/action/", stock_count_action, name="stock_count_action"),
    path("reorder/", reorder, name="reorder"),
    path("reorder/create/", reorder_create, name="reorder_create"),
    path("data/", data_center, name="data_center"),
    path("data/<str:key>/<str:kind>.xlsx", data_file, name="data_file"),
    path("data/<str:key>/upload/", data_upload, name="data_upload"),
    path("data/preview/<str:token>/", data_preview, name="data_preview"),
    path("data/confirm/<str:token>/", data_confirm, name="data_confirm"),
    path("audit/", audit, name="audit"),
    path("customers/<int:pk>/delete/", customer_delete, name="customer_delete"),
    path("inventory/<int:pk>/delete/", model_delete, name="model_delete"),
    path("sales/<int:pk>/delete/", sale_delete, name="sale_delete"),
    path("purchases/<int:pk>/delete/", purchase_delete, name="purchase_delete"),
    path("coding/customer/", coding_views.code_customer, name="code_customer"),
    path("coding/customer/<int:pk>/", coding_views.code_customer, name="code_customer_edit"),
    path("coding/supplier/", coding_views.code_supplier, name="code_supplier"),
    path("coding/supplier/<int:pk>/", coding_views.code_supplier, name="code_supplier_edit"),
    path("coding/item/", coding_views.code_item, name="code_item"),
    path("coding/item/<int:pk>/", coding_views.code_item, name="code_item_edit"),
    path("sales/approvals/", sale_approvals, name="sale_approvals"),
    path("pos/return/customer/", pos_return_customer, name="pos_return_customer"),
    path("returns/<str:group>/print/", return_print, name="return_print"),
    path("customers/", customers, name="customers"),
    path("customers/debts/", debts, name="debts"),
    path("customers/<int:pk>/followup/", debt_followup_add, name="debt_followup_add"),
    path("customers/add/", customer_add, name="customer_add"),
    path("customers/<int:pk>/", customer_detail, name="customer_detail"),
    path("customers/<int:pk>/pay/", customer_pay, name="customer_pay"),
    path("quick/payment/", quick_payment, name="quick_payment"),
    path("quick/expense/", quick_expense, name="quick_expense"),
    path("customers/<int:pk>/seller/", customer_set_seller, name="customer_set_seller"),
    path("customers/<int:pk>/statement/pdf/", customer_statement_pdf, name="customer_statement_pdf"),
    path("customers/<int:pk>/statement/<str:fmt>/", customer_statement_pdf, name="customer_statement"),
    path("suppliers/", suppliers, name="suppliers"),
    path("suppliers/<int:pk>/statement/<str:fmt>/", supplier_statement_view, name="supplier_statement"),
    path("treasury/", treasury, name="treasury"),
    path("treasury/action/", treasury_action, name="treasury_action"),
    path("treasury/daily/", daily_close_view, name="daily_close"),
    path("treasury/commissions/", commissions_view, name="commissions"),
    path("online/", online_list, name="online_list"),
    path("online/new/", online_new, name="online_new"),
    path("online/create/", online_create, name="online_create"),
    path("online/<int:pk>/", online_detail, name="online_detail"),
    path("online/<int:pk>/status/", online_status, name="online_status"),
    # الموقع: معاينة المتجر من دومين السيستم، وإدارته
    path("shop/", include(("trade.store_urls", "store"))),
    path("robots.txt", store_robots),
    path("store/orders/", store_admin.orders, name="store_orders"),
    path("store/orders/<int:pk>/", store_admin.order_detail, name="store_order"),
    path("store/products/", store_admin.products, name="store_products"),
    path("store/products/<int:pk>/", store_admin.product_edit, name="store_product"),
    path("store/images/<int:pk>/delete/", store_admin.image_delete, name="store_image_delete"),
    # زر Dashboard في القائمة يفتح لوحة التحكم الجديدة
    path("admin/", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("admin/", admin.site.urls),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

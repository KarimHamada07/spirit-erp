from django.urls import path

from . import store_views as v

app_name = "store"
urlpatterns = [
    path("", v.home, name="home"),
    path("shop/", v.listing, name="all"),
    path("c/<str:category>/", v.listing, name="category"),
    path("p/<str:code>/", v.product, name="product"),
    path("cart/", v.cart, name="cart"),
    path("cart/add/", v.cart_add, name="cart_add"),
    path("cart/update/", v.cart_update, name="cart_update"),
    path("checkout/", v.checkout, name="checkout"),
    path("order/<str:token>/", v.order_done, name="order_done"),
    path("track/", v.track, name="track"),
    path("robots.txt", v.robots, name="robots"),
    path("sitemap.xml", v.sitemap, name="sitemap"),
]

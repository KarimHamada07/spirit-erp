from django import template

from trade import barcodes

register = template.Library()


@register.simple_tag
def barcode(value, height=12, bar=0.33):
    return barcodes.svg(str(value), height=height, bar=bar)


@register.filter
def sale_code(pk):
    return barcodes.sale_code(pk)


@register.filter
def online_code(pk):
    return barcodes.online_code(pk)

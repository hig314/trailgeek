from django.shortcuts import get_object_or_404, render

from .models import Page


def page(request, slug):
    p = get_object_or_404(Page, slug=slug, published=True)
    return render(request, "pages/page.html", {"page": p})

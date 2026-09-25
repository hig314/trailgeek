"""Editable site content, edited in /admin/. Lifted from landslidescience's
`pages` app, with the body in Markdown instead of raw HTML."""
import markdown
import nh3
from django.db import models
from django.urls import reverse
from django.utils.safestring import mark_safe

_EXTRA_TAGS = {"figure", "figcaption", "iframe"}


class Page(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Markdown.", blank=True)
    published = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("page", args=[self.slug])

    def body_html(self):
        html = markdown.markdown(self.body, extensions=["extra", "toc", "sane_lists"])
        return mark_safe(nh3.clean(html, tags=nh3.ALLOWED_TAGS | _EXTRA_TAGS))

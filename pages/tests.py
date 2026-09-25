from django.test import TestCase

from .models import Page


class PageTests(TestCase):
    def test_markdown_renders_and_scripts_are_stripped(self):
        Page.objects.create(slug="about", title="About", body="# Hi\n\n<script>x()</script>ok")
        r = self.client.get("/about/")
        self.assertContains(r, "<h1")
        self.assertNotContains(r, "<script>x()")

    def test_unpublished_is_404(self):
        Page.objects.create(slug="draft", title="Draft", published=False)
        self.assertEqual(self.client.get("/draft/").status_code, 404)

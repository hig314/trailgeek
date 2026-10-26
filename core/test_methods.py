from django.test import TestCase


class MethodsSandboxTests(TestCase):
    def test_page_lists_each_surface_file(self):
        r = self.client.get("/methods/sandbox/")
        self.assertEqual(r.status_code, 200)
        for name in ("cone", "dome", "plane"):
            self.assertContains(r, f"core/methods/analytic-{name}.json")
        self.assertContains(r, "core/methods/terrain-gully.json")

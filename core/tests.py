from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase


class SmokeTests(TestCase):
    def test_home_renders_map(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="map"')

    def test_healthz_reports_postgis(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["postgis"])

    def test_init_groups_is_idempotent(self):
        call_command("init_groups")
        call_command("init_groups")
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            {"data_users", "trail_editors", "site_admins"},
        )
        self.assertTrue(
            Group.objects.get(name="site_admins").permissions.filter(codename="change_page").exists()
        )

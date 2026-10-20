import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from . import basemaps

BING = {"id": "qmsshared-5976", "qms_id": 5976, "label": "Bing Maps Satellite Imagery", "category": "Shared",
        "tiles": "https://t0.tiles.virtualearth.net/tiles/a{q}.jpeg?g=685&mkt=en-us&n=z", "scheme": "xyz",
        "minzoom": 0, "maxzoom": 19, "reproject": None, "attr": "Bing (via NextGIS QMS)", "epsg": 3857, "public": True}


def reply(layers):
    r = mock.MagicMock()
    r.read.return_value = json.dumps({"layers": layers}).encode()
    r.__enter__.return_value = r
    return r


@override_settings(LS_QMS_PROMOTED_URL="https://ls.invalid/inventory/api/qms/promoted/")
class SharedQmsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_public_https_layers_only(self):
        private = dict(BING, id="qmsshared-1", public=False)
        plain = dict(BING, id="qmsshared-2", tiles="http://example.com/{z}/{x}/{y}.png")
        odd = dict(BING, id="qmsshared-3", reproject="epsg9999")
        with mock.patch("urllib.request.urlopen", return_value=reply([BING, private, plain, odd])):
            got = basemaps.shared_qms_layers()
        self.assertEqual([b["id"] for b in got], ["qmsshared-5976"])
        self.assertNotIn("public", got[0])
        self.assertEqual(got[0]["category"], "Shared")

    def test_cached_then_last_good_when_unreachable(self):
        with mock.patch("urllib.request.urlopen", return_value=reply([BING])) as u:
            basemaps.shared_qms_layers()
            basemaps.shared_qms_layers()
        self.assertEqual(u.call_count, 1)                     # cached
        cache.delete(basemaps.CACHE_KEY)                      # the hour is up
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(len(basemaps.shared_qms_layers()), 1)   # the last list read

    def test_none_when_never_reached(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(basemaps.shared_qms_layers(), [])

    @override_settings(LS_QMS_PROMOTED_URL="")
    def test_off_when_no_url(self):
        with mock.patch("urllib.request.urlopen") as u:
            self.assertEqual(basemaps.shared_qms_layers(), [])
        u.assert_not_called()

    def test_home_page_carries_them(self):
        with mock.patch("urllib.request.urlopen", return_value=reply([BING])):
            r = self.client.get("/")
        self.assertContains(r, 'id="tg-shared-basemaps"')
        self.assertContains(r, "qmsshared-5976")

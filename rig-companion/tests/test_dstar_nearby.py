import io
import json
import tempfile
import unittest
from pathlib import Path

from rigzero.dstar_nearby import Nearby, distance_km, grid_to_latlon, parse_latlon


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PLACES = {"1.1.1.10": (44.98, -93.27, "Minneapolis", "Minnesota"),
          "1.1.1.20": (41.88, -87.63, "Chicago", "Illinois"),
          "8.8.8.30": (51.5, -0.12, "London", "England")}


class NearbyTests(unittest.TestCase):
    def test_grid_and_distance(self):
        lat, lon = grid_to_latlon("EN34")
        self.assertAlmostEqual(lat, 44.5)
        self.assertAlmostEqual(lon, -93.0)
        lat, lon = grid_to_latlon("EN34ql")
        self.assertTrue(44 < lat < 45 and -94 < lon < -92)
        self.assertIsNone(grid_to_latlon("ZZ99"))
        self.assertEqual(parse_latlon("44.9,-93.2"), (44.9, -93.2))
        self.assertAlmostEqual(distance_km((44.98, -93.27), (41.88, -87.63)), 571, delta=10)

    def test_refresh_and_closest(self):
        requests = []

        def opener(request, timeout=0):
            addresses = json.loads(request.data)
            requests.append(addresses)
            return FakeResponse(json.dumps([
                {"status": "success", "lat": PLACES[a][0], "lon": PLACES[a][1], "city": PLACES[a][2],
                 "regionName": PLACES[a][3], "countryCode": "GB" if a.startswith("8") else "US", "query": a}
                if a in PLACES else {"status": "fail", "query": a} for a in addresses]).encode())

        table = {"REF010": ("1.1.1.10", 20001), "XRF020": ("ref20.example.org", 30001),
                 "DCS030": ("8.8.8.30", 30051), "XLX040": ("10.0.0.1", 30001)}
        with tempfile.TemporaryDirectory() as temp:
            book = Nearby(Path(temp), opener=opener, sleep=lambda s: None)
            self.assertFalse(book.ready(table))
            book.refresh(table, resolve=lambda host: "1.1.1.20")
            self.assertEqual(book.error, "")
            self.assertTrue(book.ready(table))
            rows = book.closest(table, grid_to_latlon("EN34"))
            self.assertEqual([r[0] for r in rows], ["REF010", "XRF020", "DCS030"])
            self.assertEqual(rows[0][2], "Minneapolis, Minnesota")
            self.assertEqual(rows[2][2], "London, GB")
            again = Nearby(Path(temp), opener=opener)
            self.assertTrue(again.ready(table))
            self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()

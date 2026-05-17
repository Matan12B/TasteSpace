import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    catalog_id_from_row,
    load_catalog,
    load_profile,
    normalize_list,
    parse_stremio_extra,
    parse_csv_favorite,
    parse_csv_rating,
    result_to_stremio_meta,
    save_profile,
)


class TasteSpaceAppTests(unittest.TestCase):
    def test_parse_stremio_extra(self):
        self.assertEqual(
            parse_stremio_extra("search=dark%20thriller&skip=100"),
            {"search": "dark thriller", "skip": "100"},
        )

    def test_normalize_list_handles_old_and_new_genres(self):
        self.assertEqual(normalize_list(["science fiction", "adventure"]), ["science fiction", "adventure"])
        self.assertEqual(normalize_list("action & adventure"), ["action", "adventure"])

    def test_load_catalog_builds_structured_embedding_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            catalog_path = Path(tmp_dir) / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "tt123",
                            "type": "movie",
                            "name": "Example",
                            "genres": ["mystery", "drama"],
                            "overview": "A quiet puzzle.",
                            "keywords": ["slow burn", "investigation"],
                            "vote_average": 8.0,
                            "vote_count": 1000,
                            "popularity": 50,
                            "year": 2020,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            catalog = load_catalog(catalog_path)

        self.assertEqual(catalog.iloc[0]["genres_list"], ["mystery", "drama"])
        self.assertIn("Keywords: slow burn investigation", catalog.iloc[0]["text"])
        self.assertGreater(catalog.iloc[0]["quality_score"], 0)

    def test_result_to_stremio_meta(self):
        meta = result_to_stremio_meta(
            {
                "id": "tt123",
                "type": "movie",
                "name": "Example",
                "poster": "https://example.test/poster.jpg",
                "genres": ["mystery"],
                "overview": "A quiet puzzle.",
                "year": 2020,
                "vote_average": 8.1,
                "background": "https://example.test/backdrop.jpg",
            }
        )

        self.assertEqual(meta["genres"], ["mystery"])
        self.assertEqual(meta["releaseInfo"], "2020")
        self.assertEqual(meta["imdbRating"], "8.1")
        self.assertEqual(meta["posterShape"], "poster")

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "taste_profile.json"
            with patch("app.PROFILE_PATH", profile_path):
                save_profile(
                    {
                        "liked": ["tt0468569"],
                        "disliked": ["tt1234567"],
                        "favorites": ["tt0137523"],
                        "watchlist": ["tt0137523"],
                        "seen": ["tt0114369"],
                        "vibes": ["dark psychological thriller"],
                        "ratings": {"tt0468569": 5},
                    }
                )
                profile = load_profile()

        self.assertEqual(profile["liked"], ["tt0468569"])
        self.assertEqual(profile["disliked"], ["tt1234567"])
        self.assertEqual(profile["favorites"], ["tt0137523"])
        self.assertEqual(profile["watchlist"], ["tt0137523"])
        self.assertEqual(profile["seen"], ["tt0114369"])
        self.assertEqual(profile["vibes"], ["dark psychological thriller"])
        self.assertEqual(profile["ratings"], {"tt0468569": 5})

    def test_csv_rating_parsing(self):
        self.assertEqual(parse_csv_rating("10", "imdb"), 5)
        self.assertEqual(parse_csv_rating("7", "imdb"), 4)
        self.assertEqual(parse_csv_rating("4.5", "letterboxd"), 5)
        self.assertIsNone(parse_csv_rating("", "letterboxd"))

    def test_csv_favorite_parsing(self):
        self.assertTrue(parse_csv_favorite({"Liked": "Yes"}, "letterboxd"))
        self.assertFalse(parse_csv_favorite({"Liked": ""}, "letterboxd"))

    def test_catalog_id_from_imdb_csv_row(self):
        self.assertEqual(catalog_id_from_row({"Const": "tt0468569"}), "tt0468569")
        self.assertIsNone(catalog_id_from_row({"Const": "tt0000001"}))
        self.assertIsNone(catalog_id_from_row({"Title Type": "tvEpisode", "Title": "Pilot"}))


if __name__ == "__main__":
    unittest.main()

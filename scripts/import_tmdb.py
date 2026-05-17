import argparse
from datetime import date
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = BASE_DIR / ".env"
DEFAULT_OUTPUT_PATH = BASE_DIR / "data" / "catalog.json"

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
TMDB_BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/w780"
USER_AGENT = "TasteSpace/0.1"
KEYWORD_BLOCKLIST = {
    "aftercreditsstinger",
    "duringcreditsstinger",
    "amused",
    "excited",
    "cheerful",
    "nostalgic",
    "complex",
    "dramatic",
    "suspenseful",
    "hilarious",
    "absurd",
    "3d animation",
}
SourceSpec = tuple[str, str, dict[str, Any]]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def tmdb_get(
    path: str,
    api_key: str,
    params: dict[str, Any] | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    query_params = dict(params or {})
    query_params["api_key"] = api_key

    query = urllib.parse.urlencode(query_params)
    url = f"{TMDB_BASE_URL}/{path.lstrip('/')}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as error:
            retryable = error.code in {429, 500, 502, 503, 504}
            if retryable and attempt < retries - 1:
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
                time.sleep(delay)
                continue

            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TMDB request failed ({error.code}) for {path}: {details}") from error
        except urllib.error.URLError as error:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue

            raise RuntimeError(f"TMDB request failed for {path}: {error.reason}") from error

    raise RuntimeError(f"TMDB request failed for {path}")


def load_genres(api_key: str, language: str) -> dict[str, dict[int, str]]:
    movie_genres = tmdb_get(
        "genre/movie/list",
        api_key,
        {"language": language},
    ).get("genres", [])
    tv_genres = tmdb_get(
        "genre/tv/list",
        api_key,
        {"language": language},
    ).get("genres", [])

    return {
        "movie": {genre["id"]: genre["name"].lower() for genre in movie_genres},
        "series": {genre["id"]: genre["name"].lower() for genre in tv_genres},
    }


def base_tmdb_sources() -> list[SourceSpec]:
    return [
        ("movie", "trending/movie/week", {}),
        ("series", "trending/tv/week", {}),
        ("movie", "movie/popular", {}),
        ("series", "tv/popular", {}),
        ("movie", "movie/top_rated", {}),
        ("series", "tv/top_rated", {}),
    ]


def year_windows(start_year: int = 1960, window_size: int = 5) -> list[tuple[int, int]]:
    current_year = date.today().year
    windows: list[tuple[int, int]] = []
    end_year = current_year
    while end_year >= start_year:
        first_year = max(start_year, end_year - window_size + 1)
        windows.append((first_year, end_year))
        end_year = first_year - 1
    return windows


def discover_params(
    media_type: str,
    min_votes: int,
    year_range: tuple[int, int] | None = None,
    genre_id: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "include_adult": "false",
        "include_video": "false",
        "sort_by": "popularity.desc",
        "vote_count.gte": min_votes,
    }
    if genre_id is not None:
        params["with_genres"] = genre_id

    if year_range is not None:
        start_year, end_year = year_range
        if media_type == "movie":
            params["primary_release_date.gte"] = f"{start_year}-01-01"
            params["primary_release_date.lte"] = f"{end_year}-12-31"
        else:
            params["first_air_date.gte"] = f"{start_year}-01-01"
            params["first_air_date.lte"] = f"{end_year}-12-31"

    return params


def tmdb_sources(
    genres_by_type: dict[str, dict[int, str]],
    min_votes: int,
    include_discover: bool,
) -> list[SourceSpec]:
    sources = base_tmdb_sources()
    if not include_discover:
        return sources

    for media_type, endpoint in (("movie", "discover/movie"), ("series", "discover/tv")):
        sources.append((media_type, endpoint, discover_params(media_type, min_votes)))

        for year_range in year_windows():
            sources.append(
                (
                    media_type,
                    endpoint,
                    discover_params(media_type, min_votes, year_range=year_range),
                )
            )

        for genre_id in genres_by_type[media_type]:
            sources.append(
                (
                    media_type,
                    endpoint,
                    discover_params(media_type, min_votes, genre_id=genre_id),
                )
            )

    return sources


def external_imdb_id(api_key: str, media_type: str, tmdb_id: int) -> str | None:
    endpoint_type = "movie" if media_type == "movie" else "tv"
    external_ids = tmdb_get(f"{endpoint_type}/{tmdb_id}/external_ids", api_key)
    imdb_id = external_ids.get("imdb_id")

    if isinstance(imdb_id, str) and imdb_id.startswith("tt"):
        return imdb_id

    return None


def tmdb_details(api_key: str, media_type: str, tmdb_id: int, language: str) -> dict[str, Any]:
    endpoint_type = "movie" if media_type == "movie" else "tv"
    append = "external_ids,credits,keywords,release_dates,content_ratings"
    return tmdb_get(
        f"{endpoint_type}/{tmdb_id}",
        api_key,
        {
            "append_to_response": append,
            "language": language,
        },
    )


def poster_url(poster_path: str | None) -> str | None:
    if not poster_path:
        return None

    return f"{TMDB_IMAGE_BASE_URL}{poster_path}"


def backdrop_url(backdrop_path: str | None) -> str | None:
    if not backdrop_path:
        return None

    return f"{TMDB_BACKDROP_BASE_URL}{backdrop_path}"


def release_year(item: dict[str, Any], media_type: str) -> int | None:
    field = "release_date" if media_type == "movie" else "first_air_date"
    value = item.get(field)
    if not isinstance(value, str) or len(value) < 4:
        return None

    try:
        return int(value[:4])
    except ValueError:
        return None


def genre_text(
    item: dict[str, Any],
    media_type: str,
    genres_by_type: dict[str, dict[int, str]],
) -> list[str]:
    return [
        genres_by_type[media_type][genre_id]
        for genre_id in item.get("genre_ids", [])
        if genre_id in genres_by_type[media_type]
    ]


def keyword_names(details: dict[str, Any], media_type: str) -> list[str]:
    raw_keywords = details.get("keywords", {})
    if media_type == "movie":
        items = raw_keywords.get("keywords", [])
    else:
        items = raw_keywords.get("results", [])

    names: list[str] = []
    for keyword in items:
        if not isinstance(keyword, dict) or not isinstance(keyword.get("name"), str):
            continue
        name = " ".join(keyword["name"].lower().replace("_", " ").split())
        if not name or name in KEYWORD_BLOCKLIST:
            continue
        if len(name) < 3 or name.isdigit():
            continue
        if name not in names:
            names.append(name)
    return names[:16]


def cast_names(details: dict[str, Any]) -> list[str]:
    cast = details.get("credits", {}).get("cast", [])
    return [
        person["name"].strip()
        for person in cast
        if isinstance(person, dict)
        and isinstance(person.get("name"), str)
        and person["name"].strip()
    ][:8]


def crew_names(details: dict[str, Any], media_type: str) -> list[str]:
    crew = details.get("credits", {}).get("crew", [])
    jobs = {"Director"} if media_type == "movie" else {"Creator", "Executive Producer"}
    names: list[str] = []

    for person in crew:
        if not isinstance(person, dict):
            continue
        if person.get("job") not in jobs:
            continue
        name = person.get("name")
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())

    return names[:5]


def certification(details: dict[str, Any], media_type: str, country: str = "US") -> str | None:
    field = "release_dates" if media_type == "movie" else "content_ratings"
    for entry in details.get(field, {}).get("results", []):
        if not isinstance(entry, dict) or entry.get("iso_3166_1") != country:
            continue

        ratings = entry.get("release_dates") if media_type == "movie" else [entry]
        for rating in ratings or []:
            if not isinstance(rating, dict):
                continue
            value = rating.get("certification") if media_type == "movie" else rating.get("rating")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def normalize_item(
    item: dict[str, Any],
    details: dict[str, Any],
    media_type: str,
    imdb_id: str,
    genres_by_type: dict[str, dict[int, str]],
) -> dict[str, Any] | None:
    name = item.get("title") if media_type == "movie" else item.get("name")
    overview = item.get("overview")

    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(overview, str) or not overview.strip():
        return None

    normalized = {
        "id": imdb_id,
        "type": media_type,
        "name": name.strip(),
        "genres": genre_text(item, media_type, genres_by_type),
        "overview": overview.strip(),
        "tmdb_id": item["id"],
        "source": "tmdb",
    }

    poster = poster_url(item.get("poster_path"))
    if poster:
        normalized["poster"] = poster

    background = backdrop_url(item.get("backdrop_path") or details.get("backdrop_path"))
    if background:
        normalized["background"] = background

    year = release_year(item, media_type)
    if year:
        normalized["year"] = year

    tagline = details.get("tagline")
    if isinstance(tagline, str) and tagline.strip():
        normalized["tagline"] = tagline.strip()

    runtime = details.get("runtime") if media_type == "movie" else details.get("episode_run_time")
    if isinstance(runtime, int) and runtime > 0:
        normalized["runtime_minutes"] = runtime
    elif isinstance(runtime, list) and runtime and isinstance(runtime[0], int):
        normalized["runtime_minutes"] = runtime[0]

    keywords = keyword_names(details, media_type)
    if keywords:
        normalized["keywords"] = keywords

    cast = cast_names(details)
    if cast:
        normalized["cast"] = cast

    crew = crew_names(details, media_type)
    if crew:
        normalized["directors" if media_type == "movie" else "creators"] = crew

    rating = certification(details, media_type)
    if rating:
        normalized["certification"] = rating

    origin_country = details.get("origin_country")
    if isinstance(origin_country, list) and origin_country:
        normalized["origin_country"] = origin_country

    original_language = details.get("original_language")
    if isinstance(original_language, str) and original_language:
        normalized["original_language"] = original_language

    vote_average = item.get("vote_average")
    if isinstance(vote_average, (int, float)):
        normalized["vote_average"] = round(float(vote_average), 2)

    vote_count = item.get("vote_count")
    if isinstance(vote_count, int):
        normalized["vote_count"] = vote_count

    popularity = item.get("popularity")
    if isinstance(popularity, (int, float)):
        normalized["popularity"] = round(float(popularity), 3)

    return normalized


def fetch_source_items(
    api_key: str,
    media_type: str,
    endpoint: str,
    params: dict[str, Any],
    pages: int,
    language: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for page in range(1, pages + 1):
        data = tmdb_get(
            endpoint,
            api_key,
            {
                **params,
                "language": language,
                "page": page,
            },
        )
        items.extend(data.get("results", []))

        total_pages = data.get("total_pages")
        if isinstance(total_pages, int) and page >= total_pages:
            break

    return items


def import_catalog(
    api_key: str,
    pages: int,
    limit: int,
    language: str,
    sleep_seconds: float,
    min_votes: int,
    include_discover: bool,
) -> list[dict[str, Any]]:
    genres_by_type = load_genres(api_key, language)
    catalog: list[dict[str, Any]] = []
    seen_imdb_ids: set[str] = set()
    seen_tmdb_ids: set[tuple[str, int]] = set()

    for media_type, endpoint, params in tmdb_sources(genres_by_type, min_votes, include_discover):
        source_label = endpoint
        if params.get("with_genres"):
            genre_name = genres_by_type[media_type].get(int(params["with_genres"]), params["with_genres"])
            source_label = f"{endpoint} genre={genre_name}"
        elif params.get("primary_release_date.gte") or params.get("first_air_date.gte"):
            start = params.get("primary_release_date.gte") or params.get("first_air_date.gte")
            end = params.get("primary_release_date.lte") or params.get("first_air_date.lte")
            source_label = f"{endpoint} years={str(start)[:4]}-{str(end)[:4]}"

        print(f"Fetching {source_label}...", file=sys.stderr)
        source_items = fetch_source_items(api_key, media_type, endpoint, params, pages, language)

        for item in source_items:
            tmdb_id = item.get("id")
            if not isinstance(tmdb_id, int):
                continue
            tmdb_key = (media_type, tmdb_id)
            if tmdb_key in seen_tmdb_ids:
                continue
            seen_tmdb_ids.add(tmdb_key)

            details = tmdb_details(api_key, media_type, tmdb_id, language)
            external_ids = details.get("external_ids", {})
            imdb_id = external_ids.get("imdb_id")
            if not isinstance(imdb_id, str) or not imdb_id.startswith("tt"):
                imdb_id = external_imdb_id(api_key, media_type, tmdb_id)
            time.sleep(sleep_seconds)

            if not imdb_id or imdb_id in seen_imdb_ids:
                continue

            normalized = normalize_item(item, details, media_type, imdb_id, genres_by_type)
            if not normalized:
                continue

            seen_imdb_ids.add(imdb_id)
            catalog.append(normalized)

            if len(catalog) >= limit:
                return catalog

    return catalog


def write_catalog(catalog: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a TMDB-backed catalog for TasteSpace.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to write the normalized TasteSpace catalog JSON.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="Number of pages to fetch from each TMDB source endpoint.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help="Maximum normalized items to write after de-duplication.",
    )
    parser.add_argument(
        "--language",
        default="en-US",
        help="TMDB language code to request.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="Seconds to sleep between external ID lookups.",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=50,
        help="Minimum TMDB vote count for discover sources.",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Only use trending/popular/top-rated sources.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate items without writing catalog.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(DEFAULT_DOTENV_PATH)

    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print(
            "Missing TMDB_API_KEY. Add it to .env or export it in your shell.",
            file=sys.stderr,
        )
        return 2

    catalog = import_catalog(
        api_key=api_key,
        pages=max(1, args.pages),
        limit=max(1, args.limit),
        language=args.language,
        sleep_seconds=max(0.0, args.sleep),
        min_votes=max(0, args.min_votes),
        include_discover=not args.no_discover,
    )

    if args.dry_run:
        print(json.dumps(catalog[:5], ensure_ascii=False, indent=2))
        print(f"Fetched {len(catalog)} normalized items.", file=sys.stderr)
        return 0

    write_catalog(catalog, args.output)
    print(f"Wrote {len(catalog)} items to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

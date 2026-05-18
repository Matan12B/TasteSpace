import csv
import hashlib
import io
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans

APP_VERSION = "0.1.0"
MODEL_NAME = "all-MiniLM-L6-v2"
STREMIO_CATALOG_ID = "taste-space"
STREMIO_WATCHLIST_CATALOG_ID = "taste-space-watchlist"
STREMIO_PAGE_SIZE = 100
SEMANTIC_WEIGHT = 0.76
QUALITY_WEIGHT = 0.14
POPULARITY_WEIGHT = 0.07
RECENCY_WEIGHT = 0.03
TASTE_WEIGHT = 0.86

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
CACHE_DIR = BASE_DIR / "data" / "cache"
PROFILE_PATH = BASE_DIR / "data" / "taste_profile.json"
UI_DIR = BASE_DIR / "ui"
CATALOG_REQUIRED_FIELDS = {"id", "type", "name", "genres", "overview"}
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
DEFAULT_PROFILE = {
    "liked": [],
    "disliked": [],
    "favorites": [],
    "watchlist": [],
    "seen": [],
    "vibes": [],
    "ratings": {},
}
VIBE_PRESETS = [
    {
        "slug": "mind-benders",
        "name": "Mind Benders",
        "query": "mind bending psychological mystery twist ending unreliable narrator cult thriller",
    },
    {
        "slug": "dark-crime",
        "name": "Dark Crime",
        "query": "dark crime detective serial killer noir investigation morally complex thriller",
    },
    {
        "slug": "psychological-thrillers",
        "name": "Psychological Thrillers",
        "query": "psychological thriller tense disturbing obsessive paranoid suspense",
    },
    {
        "slug": "violent-revenge",
        "name": "Violent Revenge",
        "query": "violent revenge gritty action crime antihero brutal stylish",
    },
    {
        "slug": "slow-burn-mystery",
        "name": "Slow Burn Mystery",
        "query": "slow burn atmospheric mystery quiet investigation secrets moody",
    },
    {
        "slug": "prestige-drama",
        "name": "Prestige Drama",
        "query": "prestige drama complex characters acclaimed emotional serious",
    },
    {
        "slug": "sci-fi-mystery",
        "name": "Sci-Fi Mystery",
        "query": "science fiction mystery cerebral thoughtful futuristic existential",
    },
    {
        "slug": "comfort-comedy",
        "name": "Comfort Comedy",
        "query": "comfort comedy funny warm lighthearted witty feel good",
    },
    {
        "slug": "animated-adventure",
        "name": "Animated Adventure",
        "query": "animated adventure imaginative family fantasy funny heartfelt",
    },
    {
        "slug": "epic-fantasy",
        "name": "Epic Fantasy",
        "query": "epic fantasy adventure mythology magic kingdoms heroic",
    },
    {
        "slug": "stylish-action",
        "name": "Stylish Action",
        "query": "stylish action slick choreography high energy spectacle",
    },
    {
        "slug": "romantic-dramedy",
        "name": "Romantic Dramedy",
        "query": "romantic dramedy charming emotional relationships funny bittersweet",
    },
]

app = FastAPI(title="TasteSpace API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/ui-static", StaticFiles(directory=UI_DIR), name="ui-static")


def normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return " ".join(normalize_text(item) for item in value if normalize_text(item))
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
    if isinstance(value, str):
        separators = [",", "|"]
        parts = [value]
        for separator in separators:
            if separator in value:
                parts = value.split(separator)
                break
        if len(parts) == 1:
            parts = value.split()
        return [part.strip() for part in parts if part.strip() and part.strip() != "&"]
    return []


def clean_keywords(value: Any) -> list[str]:
    keywords = normalize_list(value)
    cleaned: list[str] = []
    for keyword in keywords:
        normalized = " ".join(keyword.lower().replace("_", " ").split())
        if not normalized or normalized in KEYWORD_BLOCKLIST:
            continue
        if len(normalized) < 3 or normalized.isdigit():
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned[:16]


def normalized_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    return default


def build_embedding_text(row: pd.Series) -> str:
    fields = [
        ("Title", row.get("name")),
        ("Type", row.get("type")),
        ("Genres", row.get("genres")),
        ("Keywords", clean_keywords(row.get("keywords"))),
        ("Tagline", row.get("tagline")),
        ("Cast", row.get("cast")),
        ("Crew", row.get("directors") or row.get("creators")),
        ("Overview", row.get("overview")),
    ]
    return "\n".join(
        f"{label}: {text}"
        for label, value in fields
        if (text := normalize_text(value))
    )


def load_catalog(path: Path) -> pd.DataFrame:
    with path.open(encoding="utf-8") as file:
        items = json.load(file)

    catalog_df = pd.DataFrame(items)
    missing_fields = CATALOG_REQUIRED_FIELDS - set(catalog_df.columns)
    if missing_fields:
        fields = ", ".join(sorted(missing_fields))
        raise ValueError(f"Catalog is missing required fields: {fields}")

    catalog_df["genres_list"] = catalog_df["genres"].apply(normalize_list)
    if "keywords" in catalog_df:
        catalog_df["keywords"] = catalog_df["keywords"].apply(clean_keywords)
    catalog_df["text"] = catalog_df.apply(build_embedding_text, axis=1)
    catalog_df["quality_score"] = catalog_df.apply(catalog_quality_score, axis=1)
    catalog_df["popularity_score"] = (
        catalog_df["popularity"].apply(popularity_score)
        if "popularity" in catalog_df
        else 0.0
    )
    catalog_df["recency_score"] = (
        catalog_df["year"].apply(recency_score)
        if "year" in catalog_df
        else 0.0
    )
    return catalog_df


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return {
            "liked": [],
            "disliked": [],
            "favorites": [],
            "watchlist": [],
            "seen": [],
            "vibes": [],
            "ratings": {},
        }

    with PROFILE_PATH.open(encoding="utf-8") as file:
        raw_profile = json.load(file)

    profile: dict[str, Any] = {
        "liked": [],
        "disliked": [],
        "favorites": [],
        "watchlist": [],
        "seen": [],
        "vibes": [],
        "ratings": {},
    }
    for key in ("liked", "disliked", "favorites", "watchlist", "seen", "vibes"):
        values = raw_profile.get(key, [])
        if isinstance(values, list):
            profile[key] = [
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ]

    ratings = raw_profile.get("ratings", {})
    if isinstance(ratings, dict):
        profile["ratings"] = {
            item_id.strip(): float(rating)
            for item_id, rating in ratings.items()
            if isinstance(item_id, str)
            and item_id.strip()
            and isinstance(rating, (int, float))
            and 0.5 <= float(rating) <= 5
        }
    return profile


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return profile


def embedding_cache_path(texts: list[str]) -> Path:
    payload = json.dumps(
        {
            "model": MODEL_NAME,
            "texts": texts,
        },
        ensure_ascii=True,
        sort_keys=True,
    ).encode("utf-8")
    cache_key = hashlib.sha256(payload).hexdigest()[:16]
    return CACHE_DIR / f"catalog_embeddings_{cache_key}.npy"


def load_or_create_embeddings(
    sentence_model: SentenceTransformer,
    texts: list[str],
) -> np.ndarray:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = embedding_cache_path(texts)

    if cache_path.exists():
        cached_embeddings = np.load(cache_path)
        if cached_embeddings.shape[0] == len(texts):
            return cached_embeddings

    embeddings = sentence_model.encode(texts, normalize_embeddings=True)
    np.save(cache_path, embeddings)
    return embeddings


def assign_clusters(catalog_df: pd.DataFrame, embeddings: np.ndarray) -> pd.DataFrame:
    clustered_catalog = catalog_df.copy()
    if len(clustered_catalog) <= 1:
        clustered_catalog["cluster"] = 0
        return clustered_catalog

    cluster_count = min(3, len(clustered_catalog))
    kmeans = KMeans(n_clusters=cluster_count, random_state=42, n_init="auto")
    clustered_catalog["cluster"] = kmeans.fit_predict(embeddings)
    return clustered_catalog


def catalog_quality_score(row: pd.Series) -> float:
    vote_average = normalized_number(row.get("vote_average"))
    vote_count = normalized_number(row.get("vote_count"))
    if vote_average <= 0 or vote_count <= 0:
        return 0.0

    vote_score = max(0.0, min(vote_average / 10.0, 1.0))
    confidence = min(math.log10(vote_count + 1) / 4.0, 1.0)
    return vote_score * confidence


def popularity_score(value: Any) -> float:
    popularity = normalized_number(value)
    if popularity <= 0:
        return 0.0
    return min(math.log10(popularity + 1) / 3.0, 1.0)


def recency_score(value: Any) -> float:
    year = normalized_number(value)
    if year <= 0:
        return 0.0
    return max(0.0, min((year - 1980) / 50.0, 1.0))


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)


@lru_cache(maxsize=1)
def get_base_catalog() -> pd.DataFrame:
    return load_catalog(CATALOG_PATH)


@lru_cache(maxsize=1)
def get_catalog_embeddings() -> np.ndarray:
    catalog_df = get_base_catalog()
    return load_or_create_embeddings(get_model(), catalog_df["text"].tolist())


@lru_cache(maxsize=1)
def get_catalog() -> pd.DataFrame:
    return assign_clusters(get_base_catalog(), get_catalog_embeddings())


@lru_cache(maxsize=512)
def embed_query(vibe: str) -> np.ndarray:
    return get_model().encode(vibe, normalize_embeddings=True)


def catalog_indexes_for_ids(item_ids: list[str]) -> np.ndarray:
    if not item_ids:
        return np.array([], dtype=int)

    catalog_df = get_catalog()
    return catalog_df.index[catalog_df["id"].isin(item_ids)].to_numpy()


def base_catalog_match_count(item_ids: list[str]) -> int:
    if not item_ids:
        return 0
    catalog_df = get_base_catalog()
    return int(catalog_df["id"].isin(item_ids).sum())


def weighted_item_vector(item_weights: dict[str, float]) -> np.ndarray | None:
    if not item_weights:
        return None

    catalog_df = get_catalog()
    matched_items = catalog_df[catalog_df["id"].isin(item_weights.keys())]
    if matched_items.empty:
        return None

    embeddings = get_catalog_embeddings()[matched_items.index.to_numpy()]
    weights = np.array([item_weights[item_id] for item_id in matched_items["id"]])
    weight_total = np.sum(np.abs(weights))
    if weight_total == 0:
        return None

    return np.sum(embeddings * weights[:, np.newaxis], axis=0) / weight_total


def taste_vector(profile: dict[str, Any]) -> np.ndarray | None:
    vectors: list[np.ndarray] = []
    item_weights: dict[str, float] = {}

    for item_id, rating in profile["ratings"].items():
        weight = (float(rating) - 3.0) / 2.0
        if weight:
            item_weights[item_id] = weight

    liked_indexes = catalog_indexes_for_ids(profile["liked"])
    if len(liked_indexes):
        for item_id in profile["liked"]:
            item_weights.setdefault(item_id, 0.8)

    favorite_indexes = catalog_indexes_for_ids(profile["favorites"])
    if len(favorite_indexes):
        for item_id in profile["favorites"]:
            item_weights[item_id] = max(item_weights.get(item_id, 0.0), 1.4)

    for vibe in profile["vibes"]:
        vectors.append(embed_query(vibe) * 0.8)

    disliked_indexes = catalog_indexes_for_ids(profile["disliked"])
    if len(disliked_indexes):
        for item_id in profile["disliked"]:
            item_weights.setdefault(item_id, -0.7)

    item_vector = weighted_item_vector(item_weights)
    if item_vector is not None:
        vectors.append(item_vector)

    if not vectors:
        return None

    vector = np.sum(vectors, axis=0)
    norm = np.linalg.norm(vector)
    if norm == 0:
        return None

    return vector / norm


def disliked_item_ids(profile: dict[str, Any]) -> set[str]:
    disliked = set(profile["disliked"])
    disliked.update(
        item_id
        for item_id, rating in profile["ratings"].items()
        if float(rating) <= 2
    )
    return disliked


def hidden_item_ids(profile: dict[str, Any]) -> set[str]:
    hidden = disliked_item_ids(profile)
    hidden.update(profile["seen"])
    hidden.update(profile["ratings"].keys())
    return hidden


def state_for_item(item_id: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    current_profile = profile or load_profile()
    return {
        "rating": current_profile["ratings"].get(item_id),
        "liked": item_id in current_profile["liked"],
        "disliked": item_id in current_profile["disliked"],
        "favorite": item_id in current_profile["favorites"],
        "watchlisted": item_id in current_profile["watchlist"],
        "seen": item_id in current_profile["seen"],
    }


def vibe_preset_for_catalog_id(catalog_id: str) -> dict[str, str] | None:
    prefix = f"{STREMIO_CATALOG_ID}-vibe-"
    if not catalog_id.startswith(prefix):
        return None

    slug = catalog_id.removeprefix(prefix)
    for preset in VIBE_PRESETS:
        if preset["slug"] == slug:
            return preset
    return None


def top_vibe_presets(limit: int = 4) -> list[dict[str, Any]]:
    profile = load_profile()
    vector = taste_vector(profile)
    if vector is None:
        return [
            {**preset, "score": None}
            for preset in VIBE_PRESETS[:limit]
        ]

    scored = []
    for preset in VIBE_PRESETS:
        score = float(np.dot(embed_query(preset["query"]), vector))
        scored.append({**preset, "score": round(score, 4)})

    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def poster_url(imdb_id: str, poster: str | None = None) -> str:
    if isinstance(poster, str) and poster.strip():
        return poster

    return f"https://images.metahub.space/poster/medium/{imdb_id}/img"


def row_to_result(
    row: pd.Series,
    score: float | None = None,
    semantic_score: float | None = None,
) -> dict:
    result = {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "poster": poster_url(row["id"], row.get("poster")),
        "genres": row.get("genres_list") or normalize_list(row["genres"]),
        "overview": row["overview"],
        "cluster": int(row.get("cluster", 0)),
    }

    if score is not None:
        result["score"] = round(float(score), 4)
    if semantic_score is not None:
        result["semantic_score"] = round(float(semantic_score), 4)

    for field in ("tmdb_id", "year", "source", "vote_average", "vote_count", "popularity", "tagline", "background"):
        value = row.get(field)
        if isinstance(value, str) and value:
            result[field] = value
        elif isinstance(value, (int, float)) and not pd.isna(value):
            result[field] = round(float(value), 3) if field in {"vote_average", "popularity"} else int(value)

    return result


def result_to_stremio_meta(result: dict) -> dict:
    meta = {
        "id": result["id"],
        "type": result["type"],
        "name": result["name"],
        "poster": result["poster"],
        "genres": result["genres"],
        "description": result["overview"],
        "posterShape": "poster",
    }

    if result.get("year"):
        meta["releaseInfo"] = str(result["year"])
    if result.get("vote_average"):
        meta["imdbRating"] = str(result["vote_average"])
    if result.get("background"):
        meta["background"] = result["background"]

    return meta


def filtered_catalog(media_type: str) -> pd.DataFrame:
    catalog = get_catalog()
    if media_type == "all":
        return catalog

    return catalog[catalog["type"] == media_type]


def rank_without_vibe(candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    ranked = candidates.copy()
    ranked["score"] = (
        (ranked["quality_score"] * 0.60)
        + (ranked["popularity_score"] * 0.30)
        + (ranked["recency_score"] * 0.10)
    )
    return ranked.sort_values(["score", "popularity_score"], ascending=False).head(limit)


def rank_for_profile(candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    profile = load_profile()
    ranked = candidates.copy()
    hidden = hidden_item_ids(profile)
    if hidden:
        ranked = ranked[~ranked["id"].isin(hidden)]
    if ranked.empty:
        return ranked

    vector = taste_vector(profile)
    if vector is None:
        return rank_without_vibe(ranked, limit)

    candidate_embeddings = get_catalog_embeddings()[ranked.index.to_numpy()]
    ranked["taste_score"] = np.dot(candidate_embeddings, vector)
    ranked["score"] = (
        ranked["taste_score"] * TASTE_WEIGHT
        + ranked["quality_score"] * QUALITY_WEIGHT
        + ranked["popularity_score"] * POPULARITY_WEIGHT
        + ranked["recency_score"] * RECENCY_WEIGHT
    )
    return ranked.sort_values("score", ascending=False).head(limit)


def semantic_recommendations(media_type: str, vibe: str | None, limit: int) -> list[dict]:
    candidates = filtered_catalog(media_type).copy()
    profile = load_profile()
    hidden = hidden_item_ids(profile)
    if hidden:
        candidates = candidates[~candidates["id"].isin(hidden)]

    if candidates.empty:
        return []

    if not vibe:
        ranked = rank_for_profile(candidates, limit)
        return [
            row_to_result(row, row["score"])
            for _, row in ranked.iterrows()
        ]

    query_embedding = embed_query(vibe.strip())
    candidate_embeddings = get_catalog_embeddings()[candidates.index.to_numpy()]
    candidates["semantic_score"] = np.dot(candidate_embeddings, query_embedding)
    vector = taste_vector(profile)
    if vector is not None:
        candidates["taste_score"] = np.dot(candidate_embeddings, vector)
        candidates["score"] = (
            candidates["semantic_score"] * 0.58
            + candidates["taste_score"] * 0.25
            + candidates["quality_score"] * 0.10
            + candidates["popularity_score"] * 0.05
            + candidates["recency_score"] * 0.02
        )
    else:
        candidates["score"] = (
            candidates["semantic_score"] * SEMANTIC_WEIGHT
            + candidates["quality_score"] * QUALITY_WEIGHT
            + candidates["popularity_score"] * POPULARITY_WEIGHT
            + candidates["recency_score"] * RECENCY_WEIGHT
        )
    candidates = candidates.sort_values("score", ascending=False).head(limit)

    return [
        row_to_result(row, row["score"], row["semantic_score"])
        for _, row in candidates.iterrows()
    ]


def profile_catalog_items(media_type: str, list_name: str, limit: int, skip: int = 0) -> list[dict]:
    profile = load_profile()
    item_ids = profile.get(list_name, [])
    if not isinstance(item_ids, list):
        return []

    catalog_df = get_base_catalog()
    if media_type != "all":
        catalog_df = catalog_df[catalog_df["type"] == media_type]

    rows = catalog_df[catalog_df["id"].isin(item_ids)]
    by_id = {
        row["id"]: row_to_result(row)
        for _, row in rows.iterrows()
    }
    ordered = [
        by_id[item_id]
        for item_id in item_ids
        if item_id in by_id
    ]
    return ordered[skip:skip + limit]


def parse_stremio_extra(extra: str | None) -> dict:
    if not extra:
        return {}

    parsed = parse_qs(extra, keep_blank_values=True)
    return {
        key: values[0]
        for key, values in parsed.items()
        if values
    }


def stremio_catalog(media_type: str, catalog_id: str, extra: str | None = None) -> dict:
    if media_type not in {"movie", "series"}:
        return {"metas": []}

    vibe_preset = vibe_preset_for_catalog_id(catalog_id)
    if catalog_id not in {STREMIO_CATALOG_ID, STREMIO_WATCHLIST_CATALOG_ID} and vibe_preset is None:
        return {"metas": []}

    extra_args = parse_stremio_extra(extra)
    try:
        skip = max(0, int(extra_args.get("skip", "0") or "0"))
    except ValueError:
        skip = 0

    if catalog_id == STREMIO_WATCHLIST_CATALOG_ID:
        page = profile_catalog_items(media_type, "watchlist", STREMIO_PAGE_SIZE, skip)
        return {
            "metas": [
                result_to_stremio_meta(item)
                for item in page
            ]
        }

    search = extra_args.get("search") or (vibe_preset["query"] if vibe_preset else None)
    recommendations = semantic_recommendations(
        media_type=media_type,
        vibe=search,
        limit=STREMIO_PAGE_SIZE + skip,
    )
    page = recommendations[skip:skip + STREMIO_PAGE_SIZE]

    return {
        "metas": [
            result_to_stremio_meta(item)
            for item in page
        ]
    }


@app.get("/manifest.json")
def stremio_manifest():
    catalogs = []
    top_vibes = VIBE_PRESETS[:4]
    for media_type, label in (("movie", "Movies"), ("series", "Series")):
        catalogs.append(
            {
                "type": media_type,
                "id": STREMIO_CATALOG_ID,
                "name": f"TasteSpace {label} For You",
                "extra": [
                    {"name": "search", "isRequired": False},
                    {"name": "skip", "isRequired": False},
                ],
            }
        )
        catalogs.append(
            {
                "type": media_type,
                "id": STREMIO_WATCHLIST_CATALOG_ID,
                "name": f"TasteSpace {label} Watchlist",
                "extra": [
                    {"name": "skip", "isRequired": False},
                ],
            }
        )
        for preset in top_vibes:
            catalogs.append(
                {
                    "type": media_type,
                    "id": f"{STREMIO_CATALOG_ID}-vibe-{preset['slug']}",
                    "name": preset["name"],
                    "extra": [
                        {"name": "skip", "isRequired": False},
                    ],
                }
            )

    return {
        "id": "community.tastespace",
        "version": APP_VERSION,
        "name": "TasteSpace",
        "description": "AI-powered vibe-based movie and series recommendations.",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "catalogs": catalogs,
        "idPrefixes": ["tt"],
        "behaviorHints": {
            "configurable": False,
        },
    }


@app.get("/")
def root():
    return {
        "name": "TasteSpace API",
        "status": "ok",
        "catalog_items": len(get_base_catalog()),
        "model": MODEL_NAME,
        "endpoints": {
            "health": "/health",
            "recommendations": "/recommendations/{user_id}?type=movie&vibe=dark psychological thriller&limit=10",
            "taste_spaces": "/taste-spaces",
            "stremio_manifest": "/manifest.json",
            "stremio_catalog": f"/catalog/movie/{STREMIO_CATALOG_ID}.json",
            "ui": "/ui",
        },
    }


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return (UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "catalog_items": len(get_base_catalog()),
        "model": MODEL_NAME,
    }


@app.get("/taste-spaces")
def taste_spaces():
    clusters = []

    for cluster_id, rows in get_catalog().groupby("cluster"):
        clusters.append({
            "cluster": int(cluster_id),
            "items": [
                row_to_result(row)
                for _, row in rows.iterrows()
            ],
        })

    return {"clusters": clusters}


@app.get("/catalog/{media_type}/{catalog_id}.json")
def stremio_catalog_feed(media_type: str, catalog_id: str):
    return stremio_catalog(media_type, catalog_id)


@app.get("/catalog/{media_type}/{catalog_id}/{extra}.json")
def stremio_catalog_feed_with_extra(media_type: str, catalog_id: str, extra: str):
    return stremio_catalog(media_type, catalog_id, extra)


@app.get("/recommendations/{user_id}")
def recommendations(
    user_id: str,
    media_type: Literal["movie", "series", "all"] = Query("movie", alias="type"),
    vibe: str | None = Query(None, min_length=2),
    limit: int = Query(20, ge=1, le=50),
):
    return {
        "user_id": user_id,
        "type": media_type,
        "vibe": vibe,
        "results": semantic_recommendations(media_type, vibe, limit),
    }


@app.get("/items/search")
def search_items(
    query: str = Query(..., min_length=2),
    media_type: Literal["movie", "series", "all"] = Query("all", alias="type"),
    limit: int = Query(10, ge=1, le=50),
):
    query_text = query.casefold()
    candidates = filtered_catalog(media_type)
    matches = candidates[
        candidates["name"].apply(lambda value: query_text in normalize_text(value).casefold())
    ].head(limit)
    current_profile = load_profile()

    return {
        "query": query,
        "type": media_type,
        "results": [
            {
                **row_to_result(row),
                "state": state_for_item(row["id"], current_profile),
            }
            for _, row in matches.iterrows()
        ],
    }


@app.get("/profile")
def profile(include_vibes: bool = Query(False)):
    current_profile = load_profile()
    current_profile["catalog_matches"] = {
        "liked": base_catalog_match_count(current_profile["liked"]),
        "disliked": base_catalog_match_count(current_profile["disliked"]),
        "favorites": base_catalog_match_count(current_profile["favorites"]),
        "watchlist": base_catalog_match_count(current_profile["watchlist"]),
        "seen": base_catalog_match_count(current_profile["seen"]),
        "rated": base_catalog_match_count(list(current_profile["ratings"].keys())),
    }
    if include_vibes:
        current_profile["top_vibes"] = top_vibe_presets()
    return current_profile


@app.get("/profile/top-vibes")
def profile_top_vibes():
    return {"top_vibes": top_vibe_presets()}


def update_profile_list(
    key: Literal["liked", "disliked", "favorites", "watchlist", "seen", "vibes"],
    value: str,
    add: bool,
) -> dict:
    cleaned_value = value.strip()
    if not cleaned_value:
        raise HTTPException(status_code=400, detail="Value cannot be empty.")

    current_profile = load_profile()
    values = current_profile[key]

    if add and cleaned_value not in values:
        values.append(cleaned_value)
    elif not add:
        current_profile[key] = [item for item in values if item != cleaned_value]

    if key == "liked" and add:
        current_profile["disliked"] = [
            item for item in current_profile["disliked"] if item != cleaned_value
        ]
    elif key == "disliked" and add:
        current_profile["liked"] = [
            item for item in current_profile["liked"] if item != cleaned_value
        ]
        current_profile["favorites"] = [
            item for item in current_profile["favorites"] if item != cleaned_value
        ]
    elif key == "favorites" and add:
        if cleaned_value not in current_profile["liked"]:
            current_profile["liked"].append(cleaned_value)
        current_profile["disliked"] = [
            item for item in current_profile["disliked"] if item != cleaned_value
        ]
    elif key == "seen" and add:
        current_profile["watchlist"] = [
            item for item in current_profile["watchlist"] if item != cleaned_value
        ]
    elif key == "watchlist" and add:
        current_profile["seen"] = [
            item for item in current_profile["seen"] if item != cleaned_value
        ]

    return save_profile(current_profile)


def normalize_rating_value(rating: float) -> float:
    return max(0.5, min(5.0, round(float(rating) * 2) / 2))


def rate_profile_item(item_id: str, rating: float) -> dict[str, Any]:
    ensure_catalog_item(item_id)
    if rating < 0.5 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be from 0.5 to 5.")

    current_profile = load_profile()
    apply_rating_to_profile(current_profile, item_id, normalize_rating_value(rating))
    return save_profile(current_profile)


def apply_rating_to_profile(profile: dict[str, Any], item_id: str, rating: float) -> dict[str, Any]:
    rating = normalize_rating_value(rating)
    profile["ratings"][item_id] = rating
    if item_id not in profile["seen"]:
        profile["seen"].append(item_id)
    profile["watchlist"] = [
        value for value in profile["watchlist"] if value != item_id
    ]

    if rating >= 4 and item_id not in profile["liked"]:
        profile["liked"].append(item_id)
    if rating <= 2 and item_id not in profile["disliked"]:
        profile["disliked"].append(item_id)

    if rating >= 4:
        profile["disliked"] = [
            value for value in profile["disliked"] if value != item_id
        ]
    if rating <= 2:
        profile["liked"] = [
            value for value in profile["liked"] if value != item_id
        ]
        profile["favorites"] = [
            value for value in profile["favorites"] if value != item_id
        ]

    return profile


def remove_profile_rating(item_id: str) -> dict[str, Any]:
    current_profile = load_profile()
    current_profile["ratings"].pop(item_id, None)
    return save_profile(current_profile)


def ensure_catalog_item(item_id: str) -> None:
    catalog_df = get_base_catalog()
    if catalog_df[catalog_df["id"] == item_id].empty:
        raise HTTPException(status_code=404, detail=f"{item_id} is not in the local catalog.")


@app.post("/profile/likes/{item_id}")
def like_item(item_id: str):
    ensure_catalog_item(item_id)
    return update_profile_list("liked", item_id, True)


@app.delete("/profile/likes/{item_id}")
def remove_liked_item(item_id: str):
    return update_profile_list("liked", item_id, False)


@app.post("/profile/dislikes/{item_id}")
def dislike_item(item_id: str):
    ensure_catalog_item(item_id)
    return update_profile_list("disliked", item_id, True)


@app.delete("/profile/dislikes/{item_id}")
def remove_disliked_item(item_id: str):
    return update_profile_list("disliked", item_id, False)


@app.post("/profile/favorites/{item_id}")
def favorite_item(item_id: str):
    ensure_catalog_item(item_id)
    return update_profile_list("favorites", item_id, True)


@app.delete("/profile/favorites/{item_id}")
def remove_favorite_item(item_id: str):
    return update_profile_list("favorites", item_id, False)


@app.post("/profile/watchlist/{item_id}")
def add_watchlist_item(item_id: str):
    ensure_catalog_item(item_id)
    return update_profile_list("watchlist", item_id, True)


@app.delete("/profile/watchlist/{item_id}")
def remove_watchlist_item(item_id: str):
    return update_profile_list("watchlist", item_id, False)


@app.post("/profile/seen/{item_id}")
def add_seen_item(item_id: str):
    ensure_catalog_item(item_id)
    return update_profile_list("seen", item_id, True)


@app.delete("/profile/seen/{item_id}")
def remove_seen_item(item_id: str):
    return update_profile_list("seen", item_id, False)


@app.post("/profile/ratings/{item_id}")
def rate_item(item_id: str, rating: float = Query(..., ge=0.5, le=5)):
    return rate_profile_item(item_id, rating)


@app.delete("/profile/ratings/{item_id}")
def remove_rating(item_id: str):
    return remove_profile_rating(item_id)


@app.post("/profile/vibes")
def add_vibe(vibe: str = Query(..., min_length=2)):
    return update_profile_list("vibes", vibe, True)


@app.delete("/profile/vibes")
def remove_vibe(vibe: str = Query(..., min_length=2)):
    return update_profile_list("vibes", vibe, False)


@app.get("/profile/items/{item_id}")
def item_profile_state(item_id: str):
    ensure_catalog_item(item_id)
    return state_for_item(item_id)


@app.get("/profile/lists/{list_name}")
def profile_list_items(
    list_name: Literal["liked", "disliked", "favorites", "watchlist", "seen", "rated"],
    limit: int = Query(50, ge=1, le=200),
):
    current_profile = load_profile()
    if list_name == "rated":
        item_ids = list(current_profile["ratings"].keys())[:limit]
    else:
        item_ids = current_profile[list_name][:limit]
    catalog_df = get_base_catalog()
    rows = catalog_df[catalog_df["id"].isin(item_ids)]
    by_id = {
        row["id"]: {
            **row_to_result(row),
            "state": state_for_item(row["id"], current_profile),
        }
        for _, row in rows.iterrows()
    }
    return {
        "list": list_name,
        "results": [
            by_id[item_id]
            for item_id in item_ids
            if item_id in by_id
        ],
    }


def find_row_value(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        for key, value in row.items():
            if key.strip().casefold() == name.casefold() and value and value.strip():
                return value.strip()
    return None


def parse_csv_rating(value: str | None, source: str) -> float | None:
    if not value:
        return None

    try:
        raw_rating = float(value)
    except ValueError:
        return None

    if raw_rating <= 0:
        return None
    if source == "imdb":
        raw_rating = raw_rating / 2.0

    return normalize_rating_value(raw_rating)


def parse_csv_favorite(row: dict[str, str], source: str) -> bool:
    values = [
        find_row_value(row, ("Liked", "Like", "Favorite", "Heart")),
    ]
    if source == "letterboxd":
        values.append(find_row_value(row, ("Letterboxd URI",)))

    for value in values:
        if not value:
            continue
        if value.strip().casefold() in {"yes", "true", "1", "liked", "heart"}:
            return True
    return False


def catalog_id_from_row(row: dict[str, str]) -> str | None:
    direct_id = find_row_value(row, ("Const", "IMDb ID", "imdb_id", "imdbID", "IMDbID"))
    if direct_id and direct_id.startswith("tt"):
        catalog_df = get_base_catalog()
        if not catalog_df[catalog_df["id"] == direct_id].empty:
            return direct_id
        return None

    title_type = find_row_value(row, ("Title Type", "Type"))
    if title_type and title_type.casefold() in {"tvEpisode".casefold(), "short", "videoGame".casefold()}:
        return None

    title = find_row_value(row, ("Title", "Name"))
    if not title:
        return None

    year_value = find_row_value(row, ("Year", "Release Year"))
    year: int | None = None
    if year_value:
        try:
            year = int(float(year_value))
        except ValueError:
            year = None

    candidates = get_base_catalog()
    matches = candidates[
        candidates["name"].apply(lambda value: normalize_text(value).casefold() == title.casefold())
    ]
    if year is not None and "year" in matches:
        year_matches = matches[matches["year"] == year]
        if not year_matches.empty:
            matches = year_matches

    if matches.empty:
        return None
    return str(matches.iloc[0]["id"])


def import_profile_csv(source: Literal["imdb", "letterboxd"], csv_text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV is missing a header row.")

    profile = load_profile()
    imported_ratings = 0
    imported_favorites = 0
    imported_watchlist = 0
    unmatched = 0
    skipped_episodes = 0

    for row in reader:
        title_type = find_row_value(row, ("Title Type", "Type"))
        if title_type and title_type.casefold() == "tvepisode":
            skipped_episodes += 1
            continue

        item_id = catalog_id_from_row(row)
        if not item_id:
            unmatched += 1
            continue

        rating = parse_csv_rating(
            find_row_value(row, ("Your Rating", "Rating")),
            source,
        )
        if rating is not None:
            apply_rating_to_profile(profile, item_id, rating)
            imported_ratings += 1
        if parse_csv_favorite(row, source):
            if item_id not in profile["favorites"]:
                profile["favorites"].append(item_id)
            if item_id not in profile["liked"]:
                profile["liked"].append(item_id)
            profile["disliked"] = [
                value for value in profile["disliked"] if value != item_id
            ]
            imported_favorites += 1

        if rating is None and item_id not in profile["watchlist"] and item_id not in profile["seen"]:
            profile["watchlist"].append(item_id)
            imported_watchlist += 1

    save_profile(profile)
    return {
        "source": source,
        "imported_ratings": imported_ratings,
        "imported_favorites": imported_favorites,
        "imported_watchlist": imported_watchlist,
        "unmatched": unmatched,
        "skipped_episodes": skipped_episodes,
    }


@app.post("/profile/import/imdb")
def import_imdb(csv_text: str = Body(..., media_type="text/plain")):
    return import_profile_csv("imdb", csv_text)


@app.post("/profile/import/letterboxd")
def import_letterboxd(csv_text: str = Body(..., media_type="text/plain")):
    return import_profile_csv("letterboxd", csv_text)

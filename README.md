# TasteSpace

AI-powered movie and TV recommendation backend for Stremio.

## Run Locally

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

Open:

```txt
http://127.0.0.1:8000/manifest.json
```

Use that URL as the Stremio add-on manifest.

The offline flags are recommended after the sentence-transformers model has been
downloaded once. They prevent startup from making Hugging Face network checks.

Open the local dashboard:

```txt
http://127.0.0.1:8000/ui
```

## LAN Control

Run the separate control server on the PC that hosts TasteSpace:

```bat
scripts\start_control_server.bat
```

On macOS/Linux:

```bash
./scripts/start_control_server.sh
```

The control server listens on port `9000` and can start, stop, and restart
TasteSpace. It prints a control token when it starts and also stores it at:

```txt
data/control_token.txt
```

From your iPhone on the same home network, open:

```txt
http://YOUR-PC-LAN-IP:9000
```

Paste the token into the page once. The page stores it in the browser and uses
it for control actions.

The main TasteSpace addon still runs on port `8000`:

```txt
http://YOUR-PC-LAN-IP:8000/ui
http://YOUR-PC-LAN-IP:8000/manifest.json
```

Optional environment variables:

```txt
TASTESPACE_CONTROL_PORT=9000
TASTESPACE_APP_PORT=8000
TASTESPACE_CONTROL_TOKEN=choose-your-own-token
TASTESPACE_OFFLINE=1
TASTESPACE_ALLOW_SLEEP=1
```

Set `TASTESPACE_OFFLINE=1` only after the sentence-transformers model is already
downloaded on that machine.

Sleep is disabled by default because some Windows GPU/driver setups fail during
forced sleep. Only set `TASTESPACE_ALLOW_SLEEP=1` if sleep/resume is stable on
your PC.

## Import TMDB Catalog

Create a local `.env` file:

```bash
cp .env.example .env
```

Set:

```txt
TMDB_API_KEY=your_tmdb_v3_api_key_here
```

Then import:

```bash
.venv/bin/python scripts/import_tmdb.py --pages 2 --limit 250
```

For a larger home catalog, run this on the server machine:

```bash
.venv/bin/python scripts/import_tmdb.py --pages 20 --limit 5000 --min-votes 50
```

On Windows:

```bat
.venv\Scripts\python.exe scripts\import_tmdb.py --pages 20 --limit 5000 --min-votes 50
```

The importer uses trending/popular/top-rated lists plus TMDB discover sources by
year windows and genre. `--limit` is still a ceiling; if you want broader
coverage, increase `--pages`, lower `--min-votes`, or both.

This writes normalized TMDB items to:

```txt
data/catalog.json
```

Restart the backend after importing. The app will build or reuse a local embedding cache under:

```txt
data/cache/
```

## Vector Search

TasteSpace embeds each catalog item using:

```txt
title, type, genres, keywords, tagline, cast, directors/creators, overview
```

The user's Stremio search/vibe is embedded into the same vector space. Results
are ranked by a blend of semantic similarity, vote confidence, popularity, and
recency.

If no vibe/search is provided, the catalog defaults to a quality/popularity sort
unless you seed a local taste profile. With a profile, the default Stremio movie
and series rows are ranked by your likes, dislikes, and saved vibe phrases.

## Taste Profile

The easiest way to train TasteSpace is the local dashboard:

```txt
http://127.0.0.1:8000/ui
```

From there you can search titles, rate them 1-5 stars, mark items as seen,
heart titles that define your taste, manage your watchlist, add manual vibe
phrases, and import IMDb or Letterboxd CSV exports.

Find local catalog items:

```bash
curl "http://127.0.0.1:8000/items/search?query=dark%20knight&type=movie"
```

Rate items with IMDb IDs from the search results:

```bash
curl -X POST "http://127.0.0.1:8000/profile/ratings/tt0468569?rating=5"
curl -X POST "http://127.0.0.1:8000/profile/ratings/tt0114369?rating=5"
curl -X POST "http://127.0.0.1:8000/profile/ratings/tt1234567?rating=1"
```

Ratings mean:

```txt
5 = love it
4 = like it
3 = neutral
2 = dislike it
1 = strongly dislike it
```

Use the heart button for titles that define your taste even if the star rating
is not objectively high. Hearts carry a stronger positive weight than a normal
5-star rating.

You can still seed simple likes and dislikes:

```bash
curl -X POST "http://127.0.0.1:8000/profile/likes/tt0468569"
curl -X POST "http://127.0.0.1:8000/profile/dislikes/tt1234567"
```

Add vibe phrases that describe what you want the rows to feel like:

```bash
curl -X POST "http://127.0.0.1:8000/profile/vibes?vibe=dark%20psychological%20thriller"
curl -X POST "http://127.0.0.1:8000/profile/vibes?vibe=slow%20atmospheric%20sci-fi"
```

Inspect the current profile:

```bash
curl "http://127.0.0.1:8000/profile"
```

The profile is stored locally at:

```txt
data/taste_profile.json
```

TasteSpace also infers your top vibe lanes from the profile and exposes them as
Stremio rows. For example, if your ratings point toward titles like Se7en, the
manifest can surface rows such as Mind Benders, Dark Crime, or Psychological
Thrillers. Reinstall or refresh the Stremio addon after changing ratings if the
row names do not update immediately.

CSV imports expect IMDb IDs when available. IMDb exports usually include them in
the `Const` column. Letterboxd exports are matched by title and year, so some
items may remain unmatched unless they already exist in the local TMDB catalog.
If the export includes a liked/favorite column, TasteSpace imports that as a
heart signal.

## Test

```bash
.venv/bin/python -m unittest discover
```

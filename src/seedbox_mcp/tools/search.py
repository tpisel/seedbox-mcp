from __future__ import annotations

from typing import Any

from seedbox_mcp.runtime import Services
from seedbox_mcp.schemas import ToolResponse
from seedbox_mcp.tools.common import clamp_limit, confidence, is_exact_title_year_match, safe_tool


async def media_search(
    services: Services,
    query: str | None = None,
    types: list[str] | None = None,
    include_existing: bool = True,
    include_external_lookup: bool = True,
    limit: int = 10,
    director: str | None = None,
    actor: str | None = None,
    genre: str | None = None,
    language: str | None = None,
    year: int | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        if not query and not any([director, actor, genre, language, year, country]):
            return ToolResponse.failure("validation", "media_search requires query or at least one attribute filter.")
        if query is not None and not query.strip():
            return ToolResponse.failure("validation", "media_search requires a non-empty query.")

        bounded = clamp_limit(limit, default=10, maximum=50)
        # Crew/location filters can only be applied via Plex; always include it when they're set.
        has_crew_filter = any([director, actor, country])
        # External lookup is gated only by the caller's flag — attribute filters must NOT
        # silently disable it (a year/genre/language refinement is no reason to stop offering
        # not-yet-owned titles to add). Year is post-filtered on the lookup results below.
        effective_external = include_external_lookup
        # Skip the existing Radarr/Sonarr libraries when a crew filter is active: those list
        # responses lack director/actor/country, so we'd return unfiltered partial matches.
        # External lookup still runs (it carries the addable candidates) but likewise can't
        # filter on crew — surfaced via a warning to the caller.
        skip_arr = has_crew_filter

        wanted = set(types or ["movie", "series", "plex"])
        if has_crew_filter:
            wanted.add("plex")

        include_directors = bool(director or actor)
        warnings: list[str] = []
        if has_crew_filter:
            warnings.append(
                "director/actor/country filters are matched in Plex only; "
                "any Radarr/Sonarr results are not filtered by cast/crew/country."
            )
        candidates: list[dict[str, Any]] = []
        if include_existing and "movie" in wanted and not skip_arr:
            candidates.extend(await _radarr_existing(services, query, genre=genre, language=language, year=year))
        if effective_external and query and "movie" in wanted:
            candidates.extend(await _radarr_lookup(services, query, year=year))
        if include_existing and "series" in wanted and not skip_arr:
            candidates.extend(await _sonarr_existing(services, query, genre=genre, year=year))
        if effective_external and query and "series" in wanted:
            candidates.extend(await _sonarr_lookup(services, query, year=year))
        if include_existing and "plex" in wanted:
            candidates.extend(
                await _plex_existing(
                    services,
                    query,
                    bounded,
                    director=director,
                    actor=actor,
                    genre=genre,
                    language=language,
                    year=year,
                    country=country,
                    include_directors=include_directors,
                )
            )
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return ToolResponse.success({"query": query, "candidates": candidates[:bounded]}, warnings)

    return await safe_tool(run)


async def _radarr_existing(
    services: Services,
    query: str | None,
    genre: str | None = None,
    language: str | None = None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    movies = await services.radarr.get("/api/v3/movie")
    results = []
    for item in _as_list(movies):
        if genre and not _match_tag(genre, item.get("genres") or []):
            continue
        if language and not _match_str(language, (item.get("originalLanguage") or {}).get("name", "")):
            continue
        if year is not None and item.get("year") != year:
            continue
        title = str(item.get("title", ""))
        score = confidence(query, title, item.get("year")) if query else 1.0
        if query and score < 0.45:
            continue
        safe, match_type = is_exact_title_year_match(query, year, item.get("title"), item.get("year"))
        results.append(
            {
                "kind": "movie",
                "source": "radarr",
                "title": item.get("title"),
                "year": item.get("year"),
                "exists": True,
                "confidence": score,
                "match_type": match_type,
                "safe_for_action": safe,
                "radarr_id": item.get("id"),
                "tmdb_id": item.get("tmdbId"),
                "imdb_id": item.get("imdbId"),
            }
        )
    return results


async def _radarr_lookup(services: Services, query: str, year: int | None = None) -> list[dict[str, Any]]:
    movies = await services.radarr.get("/api/v3/movie/lookup", {"term": query})
    results = []
    for item in _as_list(movies):
        if year is not None and item.get("year") != year:
            continue
        _, match_type = is_exact_title_year_match(query, None, item.get("title"), item.get("year"))
        results.append(
            {
                "kind": "movie",
                "source": "radarr_lookup",
                "title": item.get("title"),
                "year": item.get("year"),
                "exists": False,
                "confidence": confidence(query, str(item.get("title", "")), item.get("year")),
                "match_type": match_type,
                "safe_for_action": False,
                "tmdb_id": item.get("tmdbId"),
                "imdb_id": item.get("imdbId"),
            }
        )
    return results


async def _sonarr_existing(
    services: Services,
    query: str | None,
    genre: str | None = None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    series = await services.sonarr.get("/api/v3/series")
    results = []
    for item in _as_list(series):
        if genre and not _match_tag(genre, item.get("genres") or []):
            continue
        if year is not None and item.get("year") != year:
            continue
        title = str(item.get("title", ""))
        score = confidence(query, title, item.get("year")) if query else 1.0
        if query and score < 0.45:
            continue
        safe, match_type = is_exact_title_year_match(query, year, item.get("title"), item.get("year"))
        results.append(
            {
                "kind": "series",
                "source": "sonarr",
                "title": item.get("title"),
                "year": item.get("year"),
                "exists": True,
                "confidence": score,
                "match_type": match_type,
                "safe_for_action": safe,
                "sonarr_id": item.get("id"),
                "tvdb_id": item.get("tvdbId"),
                "imdb_id": item.get("imdbId"),
            }
        )
    return results


async def _sonarr_lookup(services: Services, query: str, year: int | None = None) -> list[dict[str, Any]]:
    series = await services.sonarr.get("/api/v3/series/lookup", {"term": query})
    results = []
    for item in _as_list(series):
        if year is not None and item.get("year") != year:
            continue
        _, match_type = is_exact_title_year_match(query, None, item.get("title"), item.get("year"))
        results.append(
            {
                "kind": "series",
                "source": "sonarr_lookup",
                "title": item.get("title"),
                "year": item.get("year"),
                "exists": False,
                "confidence": confidence(query, str(item.get("title", "")), item.get("year")),
                "match_type": match_type,
                "safe_for_action": False,
                "tvdb_id": item.get("tvdbId"),
                "imdb_id": item.get("imdbId"),
            }
        )
    return results


async def _plex_existing(
    services: Services,
    query: str | None,
    limit: int,
    director: str | None = None,
    actor: str | None = None,
    genre: str | None = None,
    language: str | None = None,
    year: int | None = None,
    country: str | None = None,
    include_directors: bool = False,
) -> list[dict[str, Any]]:
    plex_filters: dict[str, Any] = {}
    if director:
        plex_filters["director="] = director
    if actor:
        plex_filters["actor="] = actor
    if genre:
        plex_filters["genre="] = genre
    if country:
        plex_filters["country="] = country
    if year is not None:
        plex_filters["year="] = year
    if language:
        plex_filters["audioLanguage="] = language

    candidates: list[dict[str, Any]] = []
    for section_name in [services.settings.plex_movie_section, services.settings.plex_tv_section]:
        for item in await services.plex.search(section_name, query, limit, plex_filters or None):
            score = confidence(query, str(item.get("title", "")), item.get("year")) if query else 1.0
            safe, match_type = is_exact_title_year_match(query, year, item.get("title"), item.get("year"))
            candidate: dict[str, Any] = {
                "kind": "plex_item",
                "source": "plex",
                "title": item.get("title"),
                "year": item.get("year"),
                "exists": True,
                "confidence": score,
                "match_type": match_type,
                "safe_for_action": safe,
                "plex_rating_key": item.get("rating_key"),
            }
            if include_directors:
                candidate["directors"] = item.get("directors")
            candidates.append(candidate)
    return candidates


def _match_tag(query_val: str, tags: list[str]) -> bool:
    q = query_val.lower()
    return any(q in tag.lower() for tag in tags)


def _match_str(query_val: str, value: str) -> bool:
    return query_val.lower() in value.lower()


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

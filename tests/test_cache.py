from backend.cache import RecommendationCache


def test_recommendation_cache_hits_and_evicts_oldest_entry() -> None:
    cache = RecommendationCache(ttl_seconds=60, max_entries=1)
    cache.set("first", [(1, 0.8, "first reason")])
    assert cache.get("first") == [(1, 0.8, "first reason")]

    cache.set("second", [(2, 0.7, "second reason")])
    assert cache.get("first") is None
    assert cache.get("second") == [(2, 0.7, "second reason")]
    cache.close()

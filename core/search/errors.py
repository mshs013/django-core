class SearchError(ValueError):
    """A search/filter definition was rejected (unknown field/operator/relation, bad value, too large). Message is user safe."""

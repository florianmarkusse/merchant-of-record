# See __init__.pyi.

from collections.abc import Sequence

class URLFetcher:
    def __init__(self, *, allowed_protocols: Sequence[str]) -> None: ...

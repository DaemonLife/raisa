"""Settings: where files live, how long the cache is fresh, limits."""
import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    # The XDG spec says empty or relative values must be ignored.
    value = os.environ.get(var, "")
    return Path(value) if os.path.isabs(value) else Path.home() / default


CACHE = _xdg("XDG_CACHE_HOME", ".cache") / "raisa"       # pages, images, cookies
DATA = _xdg("XDG_DATA_HOME", ".local/share") / "raisa"   # bookmarks, the last pages


PAGE_TTL = 24 * 3600          # a cached page stays fresh for a day

HTTP_TIMEOUT = 25             # seconds per request
MAX_PAGE_BYTES = 20 << 20     # 20 MB: no real page is bigger
MAX_IMAGE_BYTES = 25 << 20    # larger images are not downloaded

ICON_PX = 150                 # not bigger on the long side: an icon, never enlarged
IMAGE_WORKERS = 4             # images downloaded in parallel


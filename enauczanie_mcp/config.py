import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    moodle_url: str
    moodle_token: str
    download_dir: Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_settings(env_file: Path = PROJECT_DIR / ".env") -> Settings:
    values = {**_read_env_file(env_file), **os.environ}
    missing = [key for key in ("MOODLE_URL", "MOODLE_TOKEN") if not values.get(key)]
    if missing:
        raise RuntimeError(f"Missing settings: {', '.join(missing)}. Set them in {env_file} or the environment.")
    return Settings(
        moodle_url=values["MOODLE_URL"].rstrip("/"),
        moodle_token=values["MOODLE_TOKEN"],
        download_dir=Path(values.get("ENAUCZANIE_DOWNLOAD_DIR", PROJECT_DIR / "downloads")),
    )

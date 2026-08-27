"""Minimal .env loader (KEY=VALUE lines, # comments, optional surrounding quotes). No-op if the file is
missing — on Vercel the variables come from project settings. ponytail: avoids python-dotenv and, more
importantly, avoids sourcing .env through `sh`, which strips the quotes out of JSON values."""
from __future__ import annotations

import os


def load(path: str) -> int:
    """Set variables that are not already in the environment. Returns how many were set."""
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v
                n += 1
    return n

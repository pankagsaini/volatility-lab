"""Read a size-limited JSON artifact from the deployed website."""

import json
import re

import requests


def fetch_json(filename, max_bytes, *, timeout=(5, 15)):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.json", filename):
        raise ValueError("Invalid public artifact name")
    with requests.get(f"https://volatilitylab.in/{filename}", stream=True, allow_redirects=False, timeout=timeout) as response:
        if response.status_code == 404:
            raise FileNotFoundError(f"Published {filename} is missing")
        if response.status_code != 200:
            raise RuntimeError(f"Cannot verify published {filename} (HTTP {response.status_code})")
        body = bytearray()
        for chunk in response.iter_content(65536):
            if len(body) + len(chunk) > max_bytes:
                raise ValueError(f"Published {filename} exceeds the size limit")
            body.extend(chunk)
    return json.loads(body)

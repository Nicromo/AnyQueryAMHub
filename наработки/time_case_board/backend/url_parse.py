from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedPostUrl:
    team_name: str
    post_id: str


@dataclass
class ParsedChannelUrl:
    team_name: str
    channel_name: str


def parse_post_permalink(url: str) -> ParsedPostUrl | None:
    url = url.strip()
    m = re.search(r"/([a-zA-Z0-9_-]+)/pl/([a-zA-Z0-9]+)", url)
    if not m:
        return None
    return ParsedPostUrl(team_name=m.group(1), post_id=m.group(2))


def parse_channel_url(url: str) -> ParsedChannelUrl | None:
    url = url.strip()
    m = re.search(r"/([a-zA-Z0-9_-]+)/channels/([a-zA-Z0-9_-]+)", url)
    if not m:
        return None
    return ParsedChannelUrl(team_name=m.group(1), channel_name=m.group(2))

"""Несколько каналов TiMe в одной колонке доски."""

from __future__ import annotations

import json
from sqlalchemy.orm import Session

from backend.database import KanbanColumn
from backend.time_client import TimeClient
from backend.url_parse import ParsedChannelUrl


def parse_targets_json(col: KanbanColumn) -> list[dict[str, str]]:
    """Список {team_name, channel_name, channel_id}; legacy — один канал из колонки."""
    raw = (getattr(col, "channels_json", None) or "").strip()
    if raw:
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                out: list[dict[str, str]] = []
                for x in arr:
                    if not isinstance(x, dict):
                        continue
                    cn = (x.get("channel_name") or "").strip()
                    if not cn:
                        continue
                    out.append(
                        {
                            "team_name": (x.get("team_name") or col.team_name or "tinkoff").strip(),
                            "channel_name": cn,
                            "channel_id": (x.get("channel_id") or "").strip(),
                        }
                    )
                if out:
                    return out
        except json.JSONDecodeError:
            pass
    if (col.channel_name or "").strip():
        return [
            {
                "team_name": col.team_name,
                "channel_name": col.channel_name.strip(),
                "channel_id": (col.channel_id or "").strip(),
            }
        ]
    return []


def serialize_targets(targets: list[dict[str, str]]) -> str:
    return json.dumps(targets, ensure_ascii=False)


def resolve_all_channel_ids(db: Session, col: KanbanColumn, client: TimeClient) -> list[str]:
    """Резолвит id всех каналов колонки, при необходимости обновляет channels_json и legacy-поля."""
    targets = parse_targets_json(col)
    if not targets:
        return []
    changed = False
    ids: list[str] = []
    for t in targets:
        cid = (t.get("channel_id") or "").strip()
        if not cid:
            team = client.get_team_by_name(t["team_name"])
            ch = client.get_channel_by_name(team["id"], t["channel_name"])
            cid = str(ch.get("id") or "")
            t["channel_id"] = cid
            changed = True
        ids.append(cid)
    if changed:
        col.channels_json = serialize_targets(targets)
        col.team_name = targets[0]["team_name"]
        col.channel_name = targets[0]["channel_name"]
        col.channel_id = targets[0].get("channel_id") or ""
        db.add(col)
        db.commit()
    return ids


def column_allows_post_channel(db: Session, col: KanbanColumn, client: TimeClient, post_channel_id: str) -> bool:
    if not post_channel_id:
        return False
    allowed = set(resolve_all_channel_ids(db, col, client))
    return post_channel_id in allowed


def targets_from_parsed_urls(parsed_list: list[ParsedChannelUrl]) -> list[dict[str, str]]:
    """Из списка ParsedChannelUrl — цели без channel_id."""
    return [
        {"team_name": p.team_name, "channel_name": p.channel_name, "channel_id": ""}
        for p in parsed_list
    ]

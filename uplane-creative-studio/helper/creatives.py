"""
Creative storage + matching.

The Chrome extension scrapes each ad's creative thumbnail from Meta Ads Manager
and POSTs them here as data URLs. We decode and store them locally (Meta CDN URLs
expire), keyed by a normalized ad name, so Tab 1 can show a thumbnail per ad by
matching on the ad name from the CSV.

  data/creatives/<client>/<hash>.<ext>   the image bytes
  data/creatives/<client>/_manifest.json  normalized_name -> {file, ad_name, ...}
  data/creatives/<client>/_pull.json       the app's "please fetch these names" note
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Optional

import faststart  # web-optimize uploaded videos (moov atom to front) so browsers can play them
import videoweb   # transcode uploaded videos to clean browser-safe H.264 (ffmpeg)
from views import DATA_DIR, all_ad_names  # reuse the same data root + ad-name record

CREATIVES_DIR = DATA_DIR / "creatives"
_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "video/ogg": ".ogv",
}


def _client_safe(client: str) -> str:
    s = Path(client or "unknown").name.strip().lstrip(".")
    return s or "unknown"


def normalize(name: str) -> str:
    """Match key: lowercase, collapse whitespace, strip the decorative ◤uplane◢
    brackets and surrounding punctuation so small name discrepancies still match."""
    s = (name or "").strip().lower()
    s = s.replace("◤", "").replace("◢", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -–—|")


def _client_dir(client: str) -> Path:
    d = CREATIVES_DIR / _client_safe(client)
    return d


def _manifest_path(client: str) -> Path:
    return _client_dir(client) / "_manifest.json"


def load_manifest(client: str) -> dict:
    p = _manifest_path(client)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Return (bytes, media_type) from a data: URL (or raw base64 → png)."""
    m = re.match(r"data:([^;]+);base64,(.*)$", data_url or "", re.DOTALL)
    if m:
        return base64.b64decode(m.group(2)), m.group(1)
    return base64.b64decode(data_url or ""), "image/png"


def save_creatives(client: str, items: list[dict]) -> dict:
    """items: [{name, image_base64 (data URL), media_type?}]. Returns {saved, total}."""
    d = _client_dir(client)
    d.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(client)
    saved = 0
    for it in items or []:
        name = (it.get("name") or "").strip()
        data_url = it.get("image_base64") or ""
        if not name or not data_url:
            continue
        try:
            raw, mtype = _decode_data_url(data_url)
        except Exception:
            continue
        if not raw:
            continue
        key = normalize(name)
        h = sha1(key.encode("utf-8")).hexdigest()[:16]
        store_mtype = mtype
        poster_name = None
        if mtype.startswith("video"):
            raw_path = d / (h + _EXT.get(mtype, ".mp4"))
            raw_path.write_bytes(raw)
            out_path = d / (h + ".mp4")
            # transcode to a clean, browser-safe H.264/AAC mp4; some valid exports
            # won't decode in browsers otherwise. Falls back to faststart if no ffmpeg.
            if videoweb.transcode_to_web(raw_path, out_path):
                if raw_path != out_path and raw_path.exists():
                    raw_path.unlink()
                fname = out_path.name
                store_mtype = "video/mp4"
            else:
                try:
                    faststart.fast_start(raw_path)
                except Exception:
                    pass
                fname = raw_path.name
            # a still poster so the library shows a real frame without decoding 7 videos at once
            try:
                pjpg = d / (h + ".poster.jpg")
                if videoweb.make_poster(d / fname, pjpg):
                    poster_name = pjpg.name
            except Exception:
                poster_name = None
        else:
            fname = h + _EXT.get(mtype, ".png")
            (d / fname).write_bytes(raw)
        entry = {
            "ad_name": name,
            "file": fname,
            "media_type": store_mtype,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }
        if poster_name:
            entry["poster"] = poster_name
        manifest[key] = entry
        saved += 1
    _manifest_path(client).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"saved": saved, "total": len(manifest)}


def creative_file(client: str, name: str) -> Optional[Path]:
    manifest = load_manifest(client)
    entry = manifest.get(normalize(name))
    if not entry:
        return None
    p = _client_dir(client) / Path(entry["file"]).name
    return p if p.is_file() else None


def creative_url(client: str, name: str) -> Optional[str]:
    if creative_file(client, name) is None:
        return None
    from urllib.parse import quote
    return f"/creative?client={quote(client)}&name={quote(name)}"


def poster_file(client: str, name: str) -> Optional[Path]:
    entry = load_manifest(client).get(normalize(name))
    if not entry or not entry.get("poster"):
        return None
    p = _client_dir(client) / Path(entry["poster"]).name
    return p if p.is_file() else None


def poster_url(client: str, name: str) -> Optional[str]:
    if poster_file(client, name) is None:
        return None
    from urllib.parse import quote
    return f"/creative?client={quote(client)}&name={quote(name)}&poster=1"


def has_metadata(client: str, name: str) -> bool:
    """True if this creative already has Creative-Studio metadata cached (analyzed)."""
    try:
        import engine  # lazy: engine imports creatives at module load
        return engine._meta_path(client, name).is_file()
    except Exception:
        return False


def _types_path(client: str) -> Path:
    """Manual image/video overrides set on the 'Add creatives' screen (keyed by ad name)
    — lets an ad count toward the image/video split even before a creative is uploaded."""
    return _client_dir(client) / "_types.json"


def get_manual_types(client: str) -> dict:
    p = _types_path(client)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def set_ad_type(client: str, ad_name: str, media_type: str) -> dict:
    """Set (image|video) or clear ("") a manual media type for one ad."""
    name = (ad_name or "").strip()
    if not name:
        return {"ok": False}
    d = _client_dir(client)
    d.mkdir(parents=True, exist_ok=True)
    types = get_manual_types(client)
    if media_type in ("image", "video"):
        types[name] = media_type
    else:
        types.pop(name, None)  # clear -> untyped again
    _types_path(client).write_text(json.dumps(types, indent=2), encoding="utf-8")
    return {"ok": True, "types": types}


def set_ad_types_bulk(client: str, updates: dict) -> dict:
    """Set/clear many manual types in ONE write. updates = {ad_name: 'image'|'video'|''}."""
    types = get_manual_types(client)
    for name, mt in (updates or {}).items():
        name = (name or "").strip()
        if not name:
            continue
        if mt in ("image", "video"):
            types[name] = mt
        else:
            types.pop(name, None)
    d = _client_dir(client)
    d.mkdir(parents=True, exist_ok=True)
    _types_path(client).write_text(json.dumps(types, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(types)}


def media_types_for(client: str) -> dict:
    """{ad_name: 'image'|'video'} — from each ad's stored creative, plus any MANUAL
    override set on the setup screen (manual wins, and adds ads that have no creative)."""
    out = {}
    for entry in load_manifest(client).values():
        name = entry.get("ad_name")
        if name:
            out[name] = "video" if (entry.get("media_type", "")).startswith("video") else "image"
    for name, mt in get_manual_types(client).items():
        if mt in ("image", "video"):
            out[name] = mt
    return out


def attach_to_result(result: dict, client: str) -> dict:
    """Set creative_url on every ad row that has a stored creative."""
    if not client or not result or "categories" not in result:
        return result
    manifest = load_manifest(client)
    if not manifest:
        return result
    from urllib.parse import quote
    have = 0
    for cat in result["categories"]:
        for ad in cat.get("ads", []):
            entry = manifest.get(normalize(ad.get("ad_name", "")))
            if entry and (_client_dir(client) / Path(entry["file"]).name).is_file():
                ad["creative_url"] = f"/creative?client={quote(client)}&name={quote(ad.get('ad_name', ''))}"
                ad["creative_type"] = "video" if (entry.get("media_type", "")).startswith("video") else "image"
                ad["creative_analyzed"] = has_metadata(client, ad.get("ad_name", ""))
                if entry.get("poster"):
                    ad["creative_poster"] = f"/creative?client={quote(client)}&name={quote(ad.get('ad_name', ''))}&poster=1"
                have += 1
    result["creatives_attached"] = have
    return result


def all_analyzed(exclude: tuple = ()) -> list[dict]:
    """Every creative across ALL clients that has cached metadata (analyzed),
    for the cross-client 'Individual creatives' picker. exclude = client names to skip."""
    from urllib.parse import quote
    out = []
    if not CREATIVES_DIR.is_dir():
        return out
    skip = {_client_safe(c) for c in exclude}
    for man_path in sorted(CREATIVES_DIR.glob("*/_manifest.json")):
        client = man_path.parent.name
        if client in skip:
            continue
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in man.values():
            nm = entry.get("ad_name")
            if not nm or not has_metadata(client, nm):
                continue
            typ = "video" if (entry.get("media_type", "")).startswith("video") else "image"
            item = {"client": client, "name": nm, "type": typ,
                    "creative_url": f"/creative?client={quote(client)}&name={quote(nm)}"}
            if entry.get("poster"):
                item["poster_url"] = f"/creative?client={quote(client)}&name={quote(nm)}&poster=1"
            out.append(item)
    return out


def status(client: str) -> dict:
    from urllib.parse import quote
    manifest = load_manifest(client)
    items = []
    for v in manifest.values():
        nm = v.get("ad_name")
        if not nm:
            continue
        it = {"name": nm, "type": "video" if (v.get("media_type", "")).startswith("video") else "image",
              "analyzed": has_metadata(client, nm)}
        if v.get("poster"):
            it["poster_url"] = f"/creative?client={quote(client)}&name={quote(nm)}&poster=1"
        items.append(it)
    return {"client": _client_safe(client), "count": len(items),
            "names": [it["name"] for it in items], "items": items}


def library(client: str) -> dict:
    """The full per-client sheet: every ad with a stored creative, PLUS ad names
    we've recorded (saved views + pull requests) that have no creative yet — so
    the library doubles as a place to fill the gaps."""
    from urllib.parse import quote
    manifest = load_manifest(client)
    items, seen = [], set()
    for v in manifest.values():
        nm = v.get("ad_name")
        if not nm:
            continue
        seen.add(normalize(nm))
        item = {
            "name": nm,
            "type": "video" if (v.get("media_type", "")).startswith("video") else "image",
            "has_creative": True,
            "analyzed": has_metadata(client, nm),
        }
        if v.get("poster"):
            item["poster_url"] = f"/creative?client={quote(client)}&name={quote(nm)}&poster=1"
        items.append(item)
    have = len(items)
    candidates = list(all_ad_names(client))
    candidates += (get_pull_request(client) or {}).get("ad_names", [])
    for nm in candidates:
        nm = (nm or "").strip()
        key = normalize(nm)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append({"name": nm, "type": "", "has_creative": False})
    return {
        "client": _client_safe(client),
        "count": have,
        "missing": len(items) - have,
        "total": len(items),
        "items": items,
    }


# --------------------------------------------------------------------------- #
# Pull request: the app records which client + ad names it wants creatives for;
# the extension (running on Ads Manager) reads this to know what to search.
# --------------------------------------------------------------------------- #
def _pull_path(client: str) -> Path:
    return _client_dir(client) / "_pull.json"


def set_pull_request(client: str, ad_names: list[str]) -> dict:
    d = _client_dir(client)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "client": _client_safe(client),
        "ad_names": [n for n in (ad_names or []) if n and n.strip()],
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    }
    _pull_path(client).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def get_pull_request(client: Optional[str]) -> dict:
    """Return the pull request for a client, or the most recent across clients."""
    if client:
        p = _pull_path(client)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}
    # newest across all clients
    best, best_time = {}, ""
    if CREATIVES_DIR.is_dir():
        for pp in CREATIVES_DIR.glob("*/_pull.json"):
            try:
                rec = json.loads(pp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.get("requested_at", "") > best_time:
                best, best_time = rec, rec.get("requested_at", "")
    return best

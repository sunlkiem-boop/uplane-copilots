"""Meta Ad Library VIDEO scraper — finds every active video ad on an Ad Library
page, pulls each video's real CDN URL, and downloads the mp4s. No third-party
downloader site, no manual clicking.

Browser pattern (stealth chromium, cookie dismissal, scroll-while-capturing,
dedup by CDN path) is carried over from the proven uplane-ads-scraper-meta
engine, self-contained here so the product ships standalone.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# A card is the smallest div that contains a "Library ID" marker; video cards
# additionally contain a <video>.
CARD_XPATH = (
    'xpath=//div[.//*[contains(text(),"Library ID")] and .//video]'
    '[not(.//div[.//*[contains(text(),"Library ID")] and .//video])]'
)

_LIB_ID_RE = re.compile(r"Library ID[:\s]*(\d+)")
_WS_RE = re.compile(r"\s+")

# --- structured fields out of the raw Ad Library card text -------------------
_STARTED_RE = re.compile(r"Started running on ([A-Z][a-z]+ \d{1,2}, \d{4})")
_BRAND_RE = re.compile(r"See ad details\s*(.*?)\s*Sponsored")
_TIME_RE = re.compile(r"\d+:\d{2}\s*/\s*\d+:\d{2}")
_DOMAIN_RE = re.compile(r"\b([A-Z0-9][A-Z0-9-]*(?:\.[A-Z0-9-]+)*\.[A-Z]{2,})\b")
_CTA_TAIL_RE = re.compile(
    r"\s*(Shop now|Shop Now|Learn more|Learn More|Sign up|Sign Up|Get offer|"
    r"Get Offer|Order now|Order Now|Buy now|Buy Now|Subscribe|Download|"
    r"Apply now|Apply Now|Book now|Book Now|Contact us|Get quote)\s*$")
_ZWSP_RE = re.compile(r"[​‌‍﻿]")


def parse_card_text(text: str) -> dict:
    """Turn the raw Ad Library card blob into reviewer-friendly fields:
    brand (page name), started (date the ad began running), body (the ad's
    primary text), and headline (the CTA-card line, usually the product)."""
    text = _WS_RE.sub(" ", _ZWSP_RE.sub("", text or "")).strip()
    out = {"brand": "", "started": "", "body": "", "headline": ""}
    if not text:
        return out
    m = _STARTED_RE.search(text)
    if m:
        out["started"] = m.group(1)
    m = _BRAND_RE.search(text)
    if m:
        out["brand"] = m.group(1).strip()
        rest = text[m.end():].strip()
    else:
        rest = text
    # body = between "Sponsored" and the player timestamp / CTA domain
    stop = len(rest)
    tm = _TIME_RE.search(rest)
    if tm:
        stop = min(stop, tm.start())
    dm = _DOMAIN_RE.search(rest)
    if dm:
        stop = min(stop, dm.start())
    out["body"] = rest[:stop].strip()
    # headline = what follows the destination domain, minus the CTA button word
    if dm:
        tail = rest[dm.end():]
        tail = _TIME_RE.sub("", tail).strip()
        out["headline"] = _CTA_TAIL_RE.sub("", tail).strip()
    return out

STEP_FRAC = 0.70          # scroll this fraction of the viewport per step
SCROLL_PAUSE_MS = 1100
STAGNANT_STOP = 14        # stop after this many bottom-steps with no new video


def ensure_video_filter(url: str) -> str:
    """Force media_type=video so the page lists only video ads."""
    parts = urlparse(url)
    q = parse_qs(parts.query, keep_blank_values=True)
    q["media_type"] = ["video"]
    if "active_status" not in q:
        q["active_status"] = ["active"]
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


def _cdn_key(url: str) -> str:
    try:
        return urlparse(url).path  # path is stable; query carries signatures
    except Exception:
        return url


def _download(url: str, dst: Path, timeout: int = 120) -> bool:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Referer": "https://www.facebook.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dst, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        return dst.exists() and dst.stat().st_size > 10_000
    except Exception:
        try:
            dst.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _dismiss_cookies(page) -> None:
    for sel in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'button:has-text("Allow all cookies")',
        'button:has-text("Accept all")',
        'div[aria-label="Allow all cookies"]',
        'button:has-text("Decline optional cookies")',
    ]:
        try:
            page.locator(sel).first.click(timeout=2000)
            return
        except Exception:
            continue


def _card_video_url(card, page) -> Optional[str]:
    """The direct CDN mp4 for this card's <video>. Handles blob: srcs by
    nudging playback and reading currentSrc."""
    try:
        vid = card.locator("video").first
        src = vid.get_attribute("src", timeout=1500) or ""
        if src.startswith("http"):
            return src
        # blob: / empty -> poke the element and read currentSrc
        try:
            vid.evaluate("v => { try { v.play().catch(()=>{}); } catch(e){} }")
            page.wait_for_timeout(800)
            cur = vid.evaluate("v => v.currentSrc || v.src || ''")
            if isinstance(cur, str) and cur.startswith("http"):
                return cur
        except Exception:
            pass
    except Exception:
        pass
    return None


def _card_meta(card) -> dict:
    meta = {"library_id": None, "card_text": ""}
    try:
        text = card.inner_text(timeout=2500)
        text = _WS_RE.sub(" ", text).strip()
        m = _LIB_ID_RE.search(text)
        if m:
            meta["library_id"] = m.group(1)
        meta["card_text"] = text[:1200]
    except Exception:
        pass
    try:
        href = card.locator('a[href*="l.facebook.com"]').first.get_attribute("href", timeout=1200)
        if href:
            q = parse_qs(urlparse(href).query)
            meta["dest_url"] = (q.get("u") or [""])[0]
    except Exception:
        pass
    try:
        poster = card.locator("video").first.get_attribute("poster", timeout=1200) or ""
        if poster.startswith("http"):
            meta["poster_url"] = poster
    except Exception:
        pass
    return meta


# The advertiser name shown in the Ad Library header. Meta sets every ad
# thumbnail's `alt` to the advertiser's name; only the round logo carries a
# generic alt, which we skip.
_GENERIC_ALTS = {"page profile picture", "profile picture", "ad", "image",
                 "video", "photo", ""}


def page_advertiser(page) -> str:
    """The competitor's name straight off the page — the most common non-generic
    image `alt` (that's the brand you see in the header). '' if none usable."""
    try:
        alts = page.evaluate(
            "() => Array.from(document.images).map(i => (i.getAttribute('alt')||'').trim())")
    except Exception:
        return ""
    names = [a for a in alts if a and a.lower() not in _GENERIC_ALTS]
    return Counter(names).most_common(1)[0][0] if names else ""


def scrape_videos(url: str, job_dir: Path, max_ads: int = 25,
                  max_seconds: int = 480,
                  log: Callable[[str], None] = print,
                  exclude_ids: "set | None" = None) -> dict:
    """Scroll the Ad Library page and download every unique active video ad.

    No content filtering happens at pull time — categorization (promo/bundle,
    faces, talking-heads) is left to the analyzer, which watches the whole
    video rather than guessing from the card text or a few keyframes. Returns
    {"ads": manifest, "skipped": []} ("skipped" retained for API shape); each
    ad is written to job_dir/ads/<library_id>/{video.mp4, ad.json} as it goes.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
    except Exception:
        stealth = None

    url = ensure_video_filter(url)
    ads_dir = Path(job_dir) / "ads"
    ads_dir.mkdir(parents=True, exist_ok=True)
    manifest, skipped, seen_cdn, seen_ids = [], [], set(), set()
    excluded = set(exclude_ids or ())   # library_ids already pulled — skip, don't recount
    advertiser = ""
    t0 = time.time()
    log(f"Opening Ad Library (videos only): {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1500}, locale="en-US",
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        if stealth:
            stealth.apply_stealth_sync(page)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        _dismiss_cookies(page)
        try:
            page.wait_for_selector("text=/Library ID/i", timeout=30000)
        except PWTimeoutError:
            browser.close()
            raise RuntimeError(
                "No ads found on the page (the brand may have no active video "
                "ads, or Meta blocked the request). Check the link in a browser.")

        vh = (page.viewport_size or {}).get("height", 1500)
        stagnant = 0
        while len(manifest) < max_ads and time.time() - t0 < max_seconds:
            cards = page.locator(CARD_XPATH)
            try:
                n = cards.count()
            except Exception:
                n = 0
            new_this_pass = 0
            for i in range(n):
                if len(manifest) >= max_ads:
                    break
                card = cards.nth(i)
                try:
                    if not card.is_visible():
                        continue
                except Exception:
                    continue
                vurl = _card_video_url(card, page)
                if not vurl:
                    continue
                key = _cdn_key(vurl)
                if key in seen_cdn:
                    continue
                seen_cdn.add(key)
                meta = _card_meta(card)
                lib_id = meta.get("library_id") or f"noid-{len(manifest) + 1:03d}"
                if lib_id in excluded:
                    # already pulled on an earlier run — skip without counting it,
                    # so "pull more" fetches genuinely NEW ads.
                    log(f"  already pulled {lib_id}, skipping")
                    continue
                if lib_id in seen_ids:
                    lib_id = f"{lib_id}-{len(manifest) + 1}"
                seen_ids.add(lib_id)

                ad_dir = ads_dir / lib_id
                ad_dir.mkdir(parents=True, exist_ok=True)
                log(f"Downloading video ad {len(manifest) + 1} (Library ID {lib_id})…")
                ok = _download(vurl, ad_dir / "video.mp4")
                if not ok:
                    log(f"  download failed for {lib_id}, skipping")
                    seen_cdn.discard(key)
                    continue
                if meta.get("poster_url"):
                    _download(meta["poster_url"], ad_dir / "poster.jpg", timeout=30)

                entry = {
                    "library_id": lib_id,
                    "video_url": vurl,
                    "video_cdn_key": key,
                    "card_text": meta.get("card_text", ""),
                    "dest_url": meta.get("dest_url", ""),
                    "source_page_url": url,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "status": "downloaded",
                }
                (ad_dir / "ad.json").write_text(json.dumps(entry, indent=2))
                manifest.append(entry)
                new_this_pass += 1

            # scroll one step; count stagnation only at the bottom
            at_bottom = page.evaluate(
                "(window.innerHeight + window.scrollY) >= document.body.scrollHeight - 60")
            if new_this_pass:
                stagnant = 0
            elif at_bottom:
                stagnant += 1
                if stagnant >= STAGNANT_STOP:
                    break
                # Meta sometimes needs a manual "See more" click at the tail
                try:
                    page.locator('div[role="button"]:has-text("See more")').last.click(timeout=1500)
                except Exception:
                    pass
            page.evaluate(f"window.scrollBy(0, {int(vh * STEP_FRAC)})")
            page.wait_for_timeout(SCROLL_PAUSE_MS)

        advertiser = page_advertiser(page)
        browser.close()

    hit_cap = len(manifest) >= max_ads
    log(f"Done: {len(manifest)} video ads pulled"
        + (f" (stopped at the {max_ads}-ad cap; the page may have more)" if hit_cap else ""))
    return {"ads": manifest, "skipped": skipped, "advertiser": advertiser}

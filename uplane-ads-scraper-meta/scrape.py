#!/usr/bin/env python3
"""
Minimal Meta Ad Library scraper.
Usage: scrape.py "<meta_ad_library_url>" [output_dir]
Saves one PNG per active ad into output_dir (default: ./screenshots).
"""
import sys, os, io, datetime, hashlib
from urllib.parse import urlparse, parse_qs, unquote, urlencode
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from playwright_stealth import Stealth

try:
    from PIL import Image  # for perceptual dedup hash
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def creative_dedup_hash(image_bytes: bytes) -> str:
    """
    Compute a 16-char hex dHash of image bytes. dHash (difference hash)
    survives JPEG re-encoding and minor compression shifts that byte-level
    hashing would miss — useful when Meta serves slightly different bytes
    for the same visual creative across audience-split-test ad IDs.

    Falls back to a sha256 byte-hash if Pillow isn't available, so the
    scraper still runs without the optional dep (you'll just dedupe a
    bit less aggressively).
    """
    if _HAS_PIL:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((9, 8), Image.LANCZOS)
            # tobytes() returns one byte per pixel in L mode — equivalent to
            # list(getdata()) but without the Pillow-14 deprecation warning.
            px = img.tobytes()
            bits = []
            for r in range(8):
                for c in range(8):
                    bits.append("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0")
            return "%016x" % int("".join(bits), 2)
        except Exception:
            pass
    return hashlib.sha256(image_bytes).hexdigest()[:16]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Pagination tuning. Meta's Ad Library mixes infinite scroll (auto-loads
# while you scroll near the bottom) with a manual "See more" button that
# appears every ~30-50 ads. The scraper has to do both, then stop cleanly
# when neither produces new ads.
SCROLL_PAUSE_MS = 1800
PAGINATION_MAX_LOOPS = 120            # safety cap on iterations
PAGINATION_STAGNANT_LIMIT = 4          # stop after this many no-new-ad loops
MAX_ADS = int(os.environ.get("META_SCRAPER_MAX_ADS", "40"))   # override via env
# Per-card scroll-into-view budget for the capture phase. The default was 4000ms,
# which times out on heavy DOMs / when the machine is under load; raised to 15s.
# Override via env for further tuning.
SCROLL_TIMEOUT_MS = int(os.environ.get("META_SCRAPER_SCROLL_TIMEOUT_MS", "15000"))


def normalize_url(url):
    """
    Build a canonical Ad Library URL.

    Accepts two URL shapes:
      - Single-brand Pages-result URL with ``view_all_page_id=...``
      - Keyword / category search URL with ``q=...`` (and optionally
        ``search_type``, ``sort_data[mode]``, ``sort_data[direction]``)
    At least one of those two parameters must be present.

    Forced: active_status=active, ad_type=all.
    Defaulted (preserved if caller specified): country=ALL,
    media_type=image_and_meme (statics only — the analysis workflow's
    payoff is per-ad brand-adapted generation prompts, which only make
    sense for static creatives).
    Preserved: any other filter the caller passed (sort_data, country,
    search_type, etc.).
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    page_id = qs.get("view_all_page_id", [None])[0]
    keyword = qs.get("q", [None])[0]
    if not page_id and not keyword:
        print(
            "ERROR: URL is missing both view_all_page_id and q. "
            "Provide either a Pages-result URL (single brand) or a "
            "keyword-search URL (category).",
            file=sys.stderr,
        )
        sys.exit(2)
    qs["active_status"] = ["active"]
    qs["ad_type"] = ["all"]
    qs.setdefault("country", ["ALL"])
    qs.setdefault("media_type", ["image_and_meme"])
    pairs = [(k, v) for k, vs in qs.items() for v in vs]
    return "https://www.facebook.com/ads/library/?" + urlencode(pairs)


def dismiss_cookies(page):
    for sel in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'button:has-text("Allow all cookies")',
        'button:has-text("Accept all")',
        'div[aria-label="Allow all cookies"]',
    ]:
        try:
            page.locator(sel).first.click(timeout=2000)
            return
        except Exception:
            continue


def count_loaded_ads(page) -> int:
    """Count visible "Library ID" instances. Proxy for the number of ad
    cards currently rendered. Cheap and reliable."""
    try:
        return page.locator('text=/Library ID/i').count()
    except Exception:
        return 0


def try_click_see_more(page) -> bool:
    """Click any visible "See more" / "Show more" button at the bottom of
    the Ad Library results. Returns True if a click landed."""
    selectors = [
        'div[role="button"]:has-text("See more")',
        'div[role="button"]:has-text("Show more")',
        'div[role="button"]:has-text("See More")',
        '[role="button"]:has-text("See more")',
        'text=/^See more$/i',
        'text=/^Show more$/i',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=300):
                try:
                    btn.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                btn.click(timeout=2500)
                return True
        except Exception:
            continue
    return False


def load_all_ads(page, max_ads: int) -> int:
    """Paginate until ad count plateaus or max_ads is reached.

    Each loop: scroll to bottom, try to click "See more", count ads.
    Stops when (a) the count >= max_ads, (b) the count hasn't grown for
    PAGINATION_STAGNANT_LIMIT consecutive loops AND no "See more" was
    visible, or (c) PAGINATION_MAX_LOOPS safety cap is hit.
    """
    last_count = 0
    stagnant = 0
    for loop in range(PAGINATION_MAX_LOOPS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        clicked = try_click_see_more(page)
        if clicked:
            page.wait_for_timeout(SCROLL_PAUSE_MS)
        n = count_loaded_ads(page)
        if n >= max_ads:
            print(f"  pagination: reached MAX_ADS cap ({max_ads}); stopping.")
            return n
        if n == last_count and not clicked:
            stagnant += 1
            if stagnant >= PAGINATION_STAGNANT_LIMIT:
                print(f"  pagination: no new ads after {stagnant} loops (total {n}); stopping.")
                return n
        else:
            if n > last_count or clicked:
                print(f"  pagination: loop {loop + 1}, {n} ads loaded{' (See more clicked)' if clicked else ''}")
            stagnant = 0
        last_count = n
    print(f"  pagination: hit safety cap of {PAGINATION_MAX_LOOPS} loops at {last_count} ads.")
    return last_count


def main():
    if len(sys.argv) < 2:
        print('usage: scrape.py "<url>" [output_dir]', file=sys.stderr)
        sys.exit(2)
    url = normalize_url(sys.argv[1])
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "./screenshots"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"Scraping: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        dismiss_cookies(page)
        try:
            page.wait_for_selector('text=/Library ID/i', timeout=30000)
        except PWTimeoutError:
            print("No ads found (or Meta blocked the page).", file=sys.stderr)
            browser.close()
            sys.exit(1)
        # Paginate: scroll + click "See more" until the ad count plateaus
        # or MAX_ADS is reached. This is the key loop that makes the
        # scraper grab more than the first ~30 ads Meta loads on entry.
        load_all_ads(page, MAX_ADS)
        cards = page.locator(
            'xpath=//div[.//*[contains(text(),"Library ID")] and .//img]'
            '[not(.//div[.//*[contains(text(),"Library ID")] and .//img])]'
        )
        total = cards.count()
        count = min(total, MAX_ADS)
        print(f"Found {total} ads, saving {count}.")
        # Dedup: Meta serves identical bytes when the same creative runs as
        # multiple ads (audience-split testing). We hash each downloaded
        # creative; if the hash recurs, we reuse the existing file on disk
        # and emit the same path so build_report.py can collapse the cards.
        seen_hashes: dict[str, str] = {}
        for i in range(count):
            card = cards.nth(i)
            path = os.path.join(out_dir, f"ad-{stamp}-{i:02d}.png")
            try:
                card.scroll_into_view_if_needed(timeout=SCROLL_TIMEOUT_MS)
                page.wait_for_timeout(200)
                card.screenshot(path=path, timeout=8000)
                dest = ""
                try:
                    href = card.locator('a[href*="l.facebook.com"]').first.get_attribute("href", timeout=1500)
                    if href:
                        real = parse_qs(urlparse(href).query).get("u", [None])[0]
                        dest = unquote(real) if real else href
                except Exception:
                    pass
                if not dest:
                    try:
                        for link in card.locator('a[href^="https://"]').all():
                            href = link.get_attribute("href")
                            if href and "facebook.com" not in href and "fb.me" not in href:
                                dest = href
                                break
                    except Exception:
                        pass
                print(f"  [{i:02d}] saved {path}" + (f"  ->  {dest}" if dest else ""))

                # Creative-image extraction: identify the largest <img> inside
                # this card (filters out profile pic / platform icons /
                # link-box thumbs), then DOWNLOAD the image bytes from Meta's
                # CDN using the page session. That gives us the real JPEG/PNG
                # at Meta's native resolution (typically 338×600), not a
                # viewport-rasterized screenshot. Falls back to element
                # screenshot if the URL fetch fails.
                try:
                    is_video = card.locator("video").count() > 0
                except Exception:
                    is_video = False
                # Initialize per-iteration so videos / failures don't inherit
                # the previous ad's hash — a real bug we hit during testing.
                creative_path = ""
                creative_hash = ""
                try:
                    best = None
                    best_area = 0.0
                    for img in card.locator("img").all():
                        try:
                            box = img.bounding_box()
                        except Exception:
                            continue
                        if not box:
                            continue
                        if box["width"] < 120 or box["height"] < 120:
                            continue
                        area = box["width"] * box["height"]
                        if area > best_area:
                            best_area = area
                            best = img
                    if best is not None:
                        # Pick the highest-resolution variant from srcset if
                        # present, else fall back to the plain src attribute.
                        url = ""
                        try:
                            srcset = best.get_attribute("srcset", timeout=1500) or ""
                            if srcset:
                                cands = []
                                for chunk in srcset.split(","):
                                    parts = chunk.strip().split(None, 1)
                                    if not parts:
                                        continue
                                    u = parts[0]
                                    weight = 1.0
                                    if len(parts) > 1:
                                        d = parts[1].strip()
                                        if d.endswith("w"):
                                            try: weight = float(d[:-1])
                                            except ValueError: pass
                                        elif d.endswith("x"):
                                            try: weight = float(d[:-1]) * 1000
                                            except ValueError: pass
                                    cands.append((weight, u))
                                if cands:
                                    cands.sort(reverse=True)
                                    url = cands[0][1]
                        except Exception:
                            pass
                        if not url:
                            try:
                                url = best.get_attribute("src", timeout=1500) or ""
                            except Exception:
                                url = ""
                        # Try direct download via the page's request context
                        # so cookies/headers/referrer match what the browser
                        # sent (avoids 403 from Meta's CDN signing).
                        creative_hash = ""
                        if url:
                            try:
                                resp = ctx.request.get(url, timeout=20000)
                                if resp.ok:
                                    body = resp.body()
                                    creative_hash = creative_dedup_hash(body)
                                    if creative_hash in seen_hashes:
                                        # Same bytes already on disk — reuse the
                                        # earlier file path. The manifest will
                                        # carry the duplicate group via the hash.
                                        creative_path = seen_hashes[creative_hash]
                                    else:
                                        # Sniff magic bytes for the right extension
                                        if body[:3] == b"\xff\xd8\xff":
                                            ext = "jpg"
                                        elif body[:8] == b"\x89PNG\r\n\x1a\n":
                                            ext = "png"
                                        elif body[:4] == b"RIFF" and body[8:12] == b"WEBP":
                                            ext = "webp"
                                        elif body[:6] in (b"GIF87a", b"GIF89a"):
                                            ext = "gif"
                                        else:
                                            ext = "bin"
                                        creative_path = os.path.join(out_dir, f"creative-{stamp}-{i:02d}.{ext}")
                                        with open(creative_path, "wb") as f:
                                            f.write(body)
                                        seen_hashes[creative_hash] = creative_path
                            except Exception:
                                creative_path = ""
                                creative_hash = ""
                        # Fallback: rasterize the rendered img element. Lower
                        # resolution but at least something for the report.
                        # We hash the rasterized bytes too so even fallback
                        # creatives dedupe across the run.
                        if not creative_path:
                            tmp = os.path.join(out_dir, f"creative-{stamp}-{i:02d}.png")
                            try:
                                best.screenshot(path=tmp, timeout=8000)
                                with open(tmp, "rb") as f:
                                    fb = f.read()
                                creative_hash = creative_dedup_hash(fb)
                                if creative_hash in seen_hashes:
                                    os.remove(tmp)
                                    creative_path = seen_hashes[creative_hash]
                                else:
                                    creative_path = tmp
                                    seen_hashes[creative_hash] = creative_path
                            except Exception:
                                creative_path = ""
                                creative_hash = ""
                except Exception:
                    creative_path = ""
                    creative_hash = ""
                print(f"  [{i:02d}] creative {creative_path or 'NONE'} is_video={'true' if is_video else 'false'} hash={creative_hash or 'NONE'}")
            except Exception as e:
                print(f"  [{i:02d}] FAILED: {e}", file=sys.stderr)
        browser.close()
    print("Done.")


if __name__ == "__main__":
    main()

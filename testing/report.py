# Generates the competitive intelligence HTML/PDF report. Scrapes logos and
# brand colors from each domain, queries the Vectorize index for intel, calls
# Gemini to structure the data, then renders a self-contained HTML file.
# Run directly: python testing/report.py

import io
import os
import re
import json
import time
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from google import genai

load_dotenv()

ACCOUNT_ID  = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN   = os.getenv("CLOUDFLARE_API_TOKEN")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY")
INDEX_NAME  = "riva-intel"
EMBED_MODEL = "@cf/baai/bge-base-en-v1.5"

CF_HEADERS   = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
EXTRACTS_DIR = Path(__file__).parent / "extracts"
REPORTS_DIR  = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}


def _parse_gemini_json(text: str) -> str:
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json").strip()
        text = text.split("```")[0].strip()
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return text


def embed(text: str) -> list:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{EMBED_MODEL}"
    for attempt in range(4):
        resp = requests.post(url, headers=CF_HEADERS, json={"text": [text]}, timeout=20)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  Workers AI rate limit — waiting {wait}s (attempt {attempt+1}/4)...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["result"]["data"][0]
    raise RuntimeError("embed() failed after 4 retries — rate limit persists.")


def embed_batch(texts: list[str]) -> list[list[float]]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{EMBED_MODEL}"
    for attempt in range(4):
        resp = requests.post(url, headers=CF_HEADERS, json={"text": texts}, timeout=30)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  Workers AI rate limit — waiting {wait}s (attempt {attempt+1}/4)...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["result"]["data"]
    raise RuntimeError("embed_batch() failed after 4 retries — rate limit persists.")


def vectorize_query_vec(vector: list[float], top_k: int = 20, domain: str = None) -> list:
    url     = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}/query"
    payload = {"vector": vector, "topK": top_k, "returnMetadata": "all"}
    if domain:
        payload["filter"] = {"domain": {"$eq": domain}}
    try:
        resp = requests.post(url, headers=CF_HEADERS, json=payload, timeout=20)
        resp.raise_for_status()
        result  = resp.json().get("result") or {}
        matches = result.get("matches", [])
        if domain and not matches:
            payload2 = {"vector": vector, "topK": 20, "returnMetadata": "all"}
            resp2    = requests.post(url, headers=CF_HEADERS, json=payload2, timeout=20)
            resp2.raise_for_status()
            all_matches = (resp2.json().get("result") or {}).get("matches", [])
            matches = [m for m in all_matches if m.get("metadata", {}).get("domain") == domain]
    except requests.HTTPError:
        if domain:
            payload2 = {"vector": vector, "topK": 20, "returnMetadata": "all"}
            resp2    = requests.post(url, headers=CF_HEADERS, json=payload2, timeout=20)
            resp2.raise_for_status()
            all_matches = (resp2.json().get("result") or {}).get("matches", [])
            matches = [m for m in all_matches if m.get("metadata", {}).get("domain") == domain]
        else:
            raise
    return matches


def vectorize_query(question: str, top_k: int = 20, domain: str = None) -> list:
    return vectorize_query_vec(embed(question), top_k=top_k, domain=domain)


def chunks_for(domain: str, topic: str, top_k: int = 12) -> str:
    matches = vectorize_query(topic, top_k=top_k, domain=domain)
    return "\n\n".join(m["metadata"].get("text", "") for m in matches)


def scrape_brand_assets(domain: str) -> dict:
    images_dir = EXTRACTS_DIR / domain / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"https://{domain}"
    assets   = {"logo": None, "og_image": None, "brand_color": None}

    try:
        resp = requests.get(base_url, headers=BROWSER_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Logo: try in priority order
        logo_url = _find_logo_url(soup, base_url)
        if logo_url:
            assets["logo"] = _fetch_image_b64(logo_url, images_dir, "logo")

        # Use OG image as hero background
        og_tag = (
            soup.find("meta", property="og:image") or
            soup.find("meta", attrs={"name": "og:image"})
        )
        if og_tag and og_tag.get("content"):
            og_url = urljoin(base_url, og_tag["content"])
            assets["og_image"] = _fetch_image_b64(og_url, images_dir, "og_image")

        # Brand color extraction
        assets["brand_color"] = _extract_brand_color(soup, assets.get("logo"))

    except Exception as e:
        print(f"  Image scrape warning for {domain}: {e}")

    return assets


def _find_logo_url(soup: BeautifulSoup, base_url: str) -> str:
    # priority order: header/nav img with logo signals, then SVG in header,
    # then apple-touch-icon, then any favicon
    # 1. header/nav img with logo in id/class/alt/src
    for container in ["header", "nav", '[role="banner"]']:
        section = soup.find(container)
        if not section:
            continue
        for img in section.find_all("img"):
            attrs = " ".join([
                img.get("id", ""),
                " ".join(img.get("class", [])),
                img.get("alt", ""),
                img.get("src", ""),
            ]).lower()
            if "logo" in attrs or "brand" in attrs:
                src = img.get("src") or img.get("data-src")
                if src:
                    return urljoin(base_url, src)
            src = img.get("src", "")
            if src.endswith(".svg"):
                return urljoin(base_url, src)

    # 2. any img with logo in attributes site-wide
    for img in soup.find_all("img"):
        attrs = " ".join([
            img.get("id", ""),
            " ".join(img.get("class", [])),
            img.get("alt", ""),
            img.get("src", ""),
        ]).lower()
        if "logo" in attrs:
            src = img.get("src") or img.get("data-src")
            if src:
                return urljoin(base_url, src)

    # 3. apple-touch-icon (usually 180x180, reliable brand image)
    for rel in ["apple-touch-icon", "apple-touch-icon-precomposed"]:
        tag = soup.find("link", rel=lambda r: r and rel in r)
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])

    # 4. fallback to any favicon
    for tag in soup.find_all("link", rel=lambda r: r and "icon" in r):
        if tag.get("href"):
            return urljoin(base_url, tag["href"])

    return None


def _fetch_image_b64(url: str, save_dir: Path, name: str) -> str:
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
        ext  = content_type.split("/")[-1].replace("jpeg", "jpg").replace("svg+xml", "svg")
        path = save_dir / f"{name}.{ext}"
        path.write_bytes(resp.content)
        b64 = base64.b64encode(resp.content).decode()
        return f"data:{content_type};base64,{b64}"
    except Exception:
        return None


def _extract_brand_color(soup: BeautifulSoup, logo_b64: str = None) -> str | None:
    # try in order: theme-color meta, CSS custom properties, then dominant logo color via Pillow
    # 1. theme-color meta tag
    for name in ("theme-color", "msapplication-TileColor"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            c = tag["content"].strip()
            if re.match(r"^#[0-9a-fA-F]{3,6}$", c):
                return c

    # 2. CSS custom properties in inline style tags
    for style_tag in soup.find_all("style"):
        css = style_tag.string or ""
        for var in ("--primary-color", "--brand-color", "--accent-color",
                    "--color-primary", "--primary", "--theme-color", "--color-accent"):
            m = re.search(re.escape(var) + r"\s*:\s*(#[0-9a-fA-F]{3,6})", css)
            if m:
                return m.group(1)

    # 3. dominant saturated pixel from logo (quantize to 8 colors, pick most saturated)
    if logo_b64 and "base64," in logo_b64:
        try:
            from PIL import Image
            data = base64.b64decode(logo_b64.split("base64,")[1])
            img  = Image.open(io.BytesIO(data)).convert("RGBA")
            bg   = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            small = bg.resize((40, 40), Image.LANCZOS)
            quantized = small.quantize(colors=8)
            pal = quantized.getpalette()
            for i in range(8):
                r, g, b = pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                sat = max(r, g, b) - min(r, g, b)
                if 25 < lum < 225 and sat > 40:
                    return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            pass

    return None


_SCORE_THRESHOLD = 0.45  # discard low-relevance chunks


def _chunks_from_matches(matches: list, threshold: float = _SCORE_THRESHOLD) -> str:
    good = [m for m in matches if m.get("score", 1.0) >= threshold]
    if not good:
        good = matches[:5]  # below threshold but still the best we have
    return "\n\n".join(m["metadata"].get("text", "") for m in good)


def _build_topic_queries(focus: str | None) -> dict[str, str]:
    queries: dict[str, str] = {
        "pricing":     "pricing tiers plans price per month annual cost enterprise starter pro",
        "features":    "core features product capabilities what you can do key functionality",
        "positioning": "company mission who it is for target customers use case value proposition",
        "integrations":"integrations API SDK third party connections supported platforms",
    }
    if focus:
        queries["focus_main"]     = focus
        queries["focus_pricing"]  = f"{focus} pricing cost plans tiers"
        queries["focus_features"] = f"{focus} features capabilities how it works"
    return queries


def generate_intel(domains: list, focus: str = None) -> dict:
    client   = genai.Client(api_key=GEMINI_KEY)
    sections = {d: {} for d in domains}

    topic_queries = _build_topic_queries(focus)
    topic_keys    = list(topic_queries.keys())

    # single embed call for all topics so we don't hit the rate limit with sequential calls
    all_vectors = embed_batch(list(topic_queries.values()))
    topic_vectors = dict(zip(topic_keys, all_vectors))

    def _fetch(domain, topic_key):
        vec     = topic_vectors[topic_key]
        matches = vectorize_query_vec(vec, top_k=20, domain=domain)
        return domain, topic_key, _chunks_from_matches(matches)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [
            ex.submit(_fetch, d, k)
            for d in domains
            for k in topic_keys
        ]
        for fut in as_completed(futures):
            d, k, text = fut.result()
            sections[d][k] = text

    context_block = ""
    for d, s in sections.items():
        focus_chunks = ""
        if focus:
            focus_chunks = "\n\n".join(filter(None, [
                s.get("focus_main", ""),
                s.get("focus_pricing", ""),
                s.get("focus_features", ""),
            ]))
        context_block += (
            f"\n\n=== {d} ===\n"
            f"PRICING:\n{s.get('pricing', '')}\n\n"
            f"FEATURES:\n{s.get('features', '')}\n\n"
            f"POSITIONING:\n{s.get('positioning', '')}\n\n"
            f"INTEGRATIONS:\n{s.get('integrations', '')}"
            + (f"\n\nFOCUS — {focus}:\n{focus_chunks}" if focus_chunks else "")
        )

    domain_schemas = []
    for d in domains:
        focus_insight_field = (
            f'      "focus_insight": "2-3 sentences specifically about {focus} for this product",\n'
            if focus else ""
        )
        domain_schemas.append(
            '    "' + d + '": {\n'
            '      "display_name": "Short brand name",\n'
            '      "tagline": "One sentence value proposition",\n'
            '      "target_audience": "Who they primarily serve",\n'
            '      "pricing_tiers": [\n'
            '        {"name": "tier name", "price": "$/mo or Free", "highlights": ["key feature 1","key feature 2","key feature 3"]}\n'
            '      ],\n'
            '      "top_features": ["f1","f2","f3","f4","f5","f6"],\n'
            '      "integrations": ["integration1","integration2","integration3"],\n'
            '      "strengths": ["s1","s2","s3"],\n'
            '      "weaknesses": ["w1","w2"]\n'
            + focus_insight_field +
            '    }'
        )
    domains_schema = ",\n".join(domain_schemas)

    only_fields = []
    for d in domains:
        key = "only_in_" + d.replace(".", "_")
        only_fields.append(f'    "{key}": ["unique differentiator 1","unique differentiator 2","unique differentiator 3"]')
    only_schema = ",\n".join(only_fields)

    focus_verdict_field = (
        f'    "focus_verdict": "Two sentences on how each product addresses {focus}",\n'
        if focus else ""
    )
    schema = (
        '{\n'
        '  "domains": {\n' + domains_schema + '\n  },\n'
        '  "comparison": {\n' +
        only_schema + ',\n' +
        focus_verdict_field +
        '    "pricing_verdict": "One sentence comparing pricing structures",\n'
        '    "positioning_verdict": "One sentence on market positioning differences",\n'
        '    "recommendation": "Two to three sentence GTM recommendation"\n'
        '  }\n'
        '}'
    )

    focus_instruction = (
        f"\n\nCRITICAL — The user's focus is: \"{focus}\"\n"
        f"Every field must be interpreted through this lens. "
        f"In pricing_tiers, highlight features relevant to {focus}. "
        f"In top_features, lead with {focus}-related capabilities. "
        f"In focus_insight, give a direct, specific answer about how this product handles {focus}. "
        f"The recommendation must give concrete GTM advice specifically about {focus}."
    ) if focus else ""

    prompt = (
        "You are a senior competitive intelligence analyst. "
        "Use ONLY the context below — do not invent facts not present in it.\n\n"
        "CONTEXT:\n" + context_block + "\n\n"
        + focus_instruction + "\n\n"
        "Return ONLY valid JSON (no markdown fences) matching this exact schema:\n" + schema
    )

    for attempt in range(3):
        res  = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = _parse_gemini_json(res.text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  generate_intel JSON parse error (attempt {attempt+1}/3): {e}")
            if attempt == 2:
                raise
            time.sleep(5)
    raise RuntimeError("generate_intel failed after 3 attempts")


def render_html(intel: dict, images: dict, focus: str = None) -> str:
    domains    = list(intel["domains"].keys())
    comparison = intel["comparison"]
    date_str   = datetime.now().strftime("%B %d, %Y")
    is_dual    = len(domains) == 2

    _fallbacks = ["#00aaaa", "#f3810f"]
    domain_colors = {}
    for i, d in enumerate(domains):
        c = (images.get(d) or {}).get("brand_color") or _fallbacks[i % len(_fallbacks)]
        domain_colors[d] = c

    def _css_vars():
        lines = []
        for i, d in enumerate(domains):
            slug = d.replace(".", "-")
            lines.append(f"  --c{i+1}: {domain_colors[d]};")
        return "\n".join(lines)

    def domain_card(d: str, idx: int) -> str:
        info   = intel["domains"][d]
        imgs   = images.get(d, {})
        logo   = imgs.get("logo")
        og_img = imgs.get("og_image")
        color  = domain_colors[d]

        logo_html = f'<img src="{logo}" class="logo-img" alt="{d} logo">' if logo else ""
        hero_html = (
            f'<img src="{og_img}" class="hero-img" alt="{d} hero">'
            if og_img else
            f'<div class="hero-placeholder" style="background:linear-gradient(135deg,{color}22,{color}44);color:{color};">{info["display_name"]}</div>'
        )

        tiers_html = ""
        for tier in info.get("pricing_tiers", [])[:5]:
            highlights = "".join(
                f"<li>{h[:80]}</li>" for h in (tier.get("highlights") or [])[:3]
            )
            tier_name  = str(tier.get("name", ""))[:30]
            tier_price = str(tier.get("price", ""))[:25]
            tiers_html += (
                f'<div class="tier-card" style="border-top:2px solid {color}30;">'
                f'<div class="tier-name" style="color:{color};">{tier_name}</div>'
                f'<div class="tier-price">{tier_price}</div>'
                f'<ul class="tier-features">{highlights}</ul>'
                f'</div>'
            )

        features     = info.get("top_features", [])[:6]
        integrations = info.get("integrations", [])[:5]
        strengths    = info.get("strengths", [])[:3]
        weakness     = info.get("weaknesses", [])[:3]
        focus_insight = str(info.get("focus_insight", "")).strip()

        features_html     = "".join(f"<li>{str(f)[:100]}</li>" for f in features)
        integrations_html = "".join(f"<span class='integ-tag'>{str(i)[:40]}</span>" for i in integrations)
        strengths_html    = "".join(f"<li>{str(s)[:100]}</li>" for s in strengths)
        weakness_html     = "".join(f"<li>{str(w)[:100]}</li>" for w in weakness)

        focus_block_html = ""
        if focus and focus_insight:
            focus_block_html = (
                f'<div class="focus-insight" style="border-left:3px solid {color};background:{color}0d;">'
                f'<div class="section-label" style="color:{color};">RE: {focus[:60]}</div>'
                f'<p class="focus-text">{focus_insight[:400]}</p>'
                f'</div>'
            )

        integrations_section = ""
        if integrations_html:
            integrations_section = (
                f'<div class="section-label">Integrations</div>'
                f'<div class="integ-row">{integrations_html}</div>'
            )

        return (
            f'<div class="domain-col">'
            f'<div class="domain-accent-bar" style="background:{color};"></div>'
            f'<div class="domain-inner">'
            f'<div class="domain-header">'
            + logo_html +
            f'<div class="domain-meta">'
            f'<h2>{info["display_name"]}</h2>'
            f'<p class="tagline">{str(info["tagline"])[:120]}</p>'
            f'<span class="audience-badge" style="background:{color}18;color:{color};">{str(info["target_audience"])[:80]}</span>'
            f'</div></div>'
            + hero_html
            + focus_block_html +
            f'<div class="section-label">Pricing</div>'
            f'<div class="tiers-row">{tiers_html}</div>'
            f'<div class="section-label">Top Features</div>'
            f'<ul class="feature-list clamp-list">{features_html}</ul>'
            + integrations_section +
            f'<div class="two-col">'
            f'<div><div class="section-label green">Strengths</div>'
            f'<ul class="feature-list">{strengths_html}</ul></div>'
            f'<div><div class="section-label red">Weaknesses</div>'
            f'<ul class="feature-list">{weakness_html}</ul></div>'
            f'</div>'
            f'</div></div>'
        )

    unique_sections = ""
    for d in domains:
        key   = "only_in_" + d.replace(".", "_")
        items = comparison.get(key, [])
        if items:
            name  = intel["domains"][d]["display_name"]
            color = domain_colors[d]
            items_html = "".join(
                f'<div class="diff-item">{str(i)}</div>'
                for i in items[:8]
            )
            unique_sections += (
                f'<div class="unique-col">'
                f'<div class="section-label">Only in {name}</div>'
                f'<div class="diff-list">{items_html}</div>'
                f'</div>'
            )

    grid_cols        = "1fr 1fr" if is_dual else "1fr"
    unique_grid_cols = "1fr 1fr" if is_dual else "1fr"
    cards_html       = "".join(domain_card(d, i) for i, d in enumerate(domains))
    title_str        = " vs ".join(intel["domains"][d]["display_name"] for d in domains)
    c1 = domain_colors[domains[0]] if domains else "#00aaaa"
    c2 = domain_colors[domains[1]] if len(domains) > 1 else "#f3810f"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<base target="_blank">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Riva Intel — {title_str}</title>
<style>
  :root {{ {_css_vars()} }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f0f3f8; color: #1a1f2e; font-size: 13px; line-height: 1.5;
  }}
  .page {{ max-width: 1200px; margin: 0 auto; padding: 28px 20px; }}
  .report-header {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px; padding-bottom: 14px;
    border-bottom: 2px solid transparent;
    border-image: linear-gradient(to right, {c1}, {c2}) 1;
  }}
  .report-title {{ font-size: 20px; font-weight: 800; letter-spacing: 1px; color: #0a0f1e; }}
  .report-title span {{ color: {c1}; }}
  .report-meta {{ font-size: 11px; color: #999; text-align: right; }}
  .domain-grid {{
    display: grid; grid-template-columns: {grid_cols};
    gap: 18px; margin-bottom: 18px;
  }}
  .domain-col {{
    background: white; border-radius: 12px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }}
  .domain-accent-bar {{ height: 4px; width: 100%; }}
  .domain-inner {{ padding: 18px; }}
  .domain-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
  .logo-img {{ width: 36px; height: 36px; object-fit: contain; border-radius: 6px; flex-shrink: 0; }}
  .domain-meta h2 {{ font-size: 16px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .tagline {{ font-size: 11px; color: #666; margin-top: 2px; line-height: 1.5; }}
  .audience-badge {{
    display: inline-block; border-radius: 20px; padding: 2px 8px;
    font-size: 10px; font-weight: 600; margin-top: 4px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%;
  }}
  .hero-img {{ width: 100%; height: 110px; object-fit: cover; border-radius: 8px; margin-bottom: 12px; }}
  .hero-placeholder {{
    width: 100%; height: 44px; border-radius: 8px; display: flex; align-items: center;
    justify-content: center; font-weight: 700; font-size: 14px; margin-bottom: 12px; letter-spacing: 2px;
  }}
  .section-label {{
    font-size: 9px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase;
    color: #aaa; margin: 12px 0 6px;
  }}
  .section-label.green {{ color: #00a86b; }}
  .section-label.red   {{ color: #e05555; }}
  .tiers-row {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .tier-card {{
    flex: 1; min-width: 90px; max-width: 160px; background: #f8fafc;
    border-radius: 8px; padding: 8px; border: 1px solid #e5eaf2;
  }}
  .tier-name  {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }}
  .tier-price {{ font-size: 14px; font-weight: 800; margin: 3px 0; color: #1a1f2e; }}
  .tier-features {{ padding-left: 10px; font-size: 10px; color: #666; }}
  .tier-features li {{ margin-bottom: 2px; line-height: 1.4; }}
  .feature-list {{ padding-left: 12px; }}
  .feature-list li {{ margin-bottom: 3px; font-size: 11.5px; color: #333; line-height: 1.45; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 4px; }}
  .focus-insight {{ border-radius: 8px; padding: 10px 12px; margin: 10px 0; }}
  .focus-text {{ font-size: 11.5px; color: #2a2f3e; line-height: 1.6; margin-top: 4px; }}
  .integ-row {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 4px; }}
  .integ-tag {{
    background: #f0f4ff; color: #3355cc; border-radius: 12px;
    padding: 2px 8px; font-size: 10px; font-weight: 600; border: 1px solid #d0d8f0;
  }}
  .focus-banner {{
    background: linear-gradient(90deg, {c1}18, {c2}18);
    border: 1px solid {c1}44; border-radius: 10px;
    padding: 10px 16px; margin-bottom: 18px;
    display: flex; align-items: center; gap: 10px;
  }}
  .focus-banner-label {{ font-size: 9px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; color: {c1}; white-space: nowrap; }}
  .focus-banner-text  {{ font-size: 12px; color: #1a1f2e; font-weight: 500; }}
  .comparison-section {{
    background: white; border-radius: 12px; padding: 18px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 18px;
  }}
  .comp-header {{
    font-size: 13px; font-weight: 700; color: #1a1f2e;
    margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
  }}
  .comp-header::before {{
    content: ''; display: inline-block; width: 3px; height: 16px;
    background: linear-gradient(to bottom, {c1}, {c2}); border-radius: 2px;
  }}
  .unique-grid {{ display: grid; grid-template-columns: {unique_grid_cols}; gap: 18px; margin-bottom: 14px; }}
  .diff-list {{ display: flex; flex-direction: column; gap: 4px; }}
  .diff-item {{ font-size: 11px; font-weight: 500; color: #1a1f2e; line-height: 1.5; }}
  .verdict-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; }}
  .verdict-box {{
    background: #f8fafc; border-left: 3px solid #ddd;
    border-radius: 0 8px 8px 0; padding: 10px 12px;
  }}
  .verdict-label {{ font-size: 9px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; color: #aaa; margin-bottom: 4px; }}
  .verdict-text  {{ font-size: 12px; color: #333; line-height: 1.6; }}
  .recommendation {{
    background: linear-gradient(135deg, #0a1628, #0d2040);
    color: white; border-radius: 12px; padding: 18px 22px; margin-top: 4px;
    border-top: 3px solid transparent;
    border-image: linear-gradient(to right, {c1}, {c2}) 1;
  }}
  .rec-label {{ font-size: 9px; font-weight: 800; letter-spacing: 3px; text-transform: uppercase; color: {c1}; margin-bottom: 8px; }}
  .rec-text  {{ font-size: 13px; line-height: 1.7; color: #cde; }}
  .report-footer {{ margin-top: 18px; text-align: center; font-size: 10px; color: #bbb; letter-spacing: 1px; }}
  @media print {{
    body {{ background: white; }}
    .page {{ padding: 12px; max-width: 100%; }}
    .domain-col, .comparison-section {{ box-shadow: none; border: 1px solid #e5eaf2; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="report-header">
    <div>
      <div class="report-title">RIVA <span>INTELLIGENCE</span></div>
      <div style="font-size:11px;color:#666;margin-top:2px;">Competitive Analysis &mdash; {title_str}</div>
    </div>
    <div class="report-meta">Generated by Riva<br>{date_str}{"<br><span style='color:" + c1 + ";font-weight:700;'>Focus: " + focus + "</span>" if focus else ""}</div>
  </div>
  {"<div class='focus-banner'><span class='focus-banner-label'>Analysis Focus</span><span class='focus-banner-text'>" + focus + "</span></div>" if focus else ""}
  <div class="domain-grid">{cards_html}</div>
  <div class="comparison-section">
    <div class="comp-header">Competitive Differentiators</div>
    <div class="unique-grid">{unique_sections}</div>
    {"<div class='verdict-box' style='border-left-color:" + c1 + ";margin-bottom:12px;'><div class='verdict-label' style='color:" + c1 + ";'>Focus Verdict — " + focus + "</div><div class='verdict-text'>" + comparison.get("focus_verdict", "") + "</div></div>" if focus and comparison.get("focus_verdict") else ""}
    <div class="verdict-row">
      <div class="verdict-box" style="border-left-color:{c1};">
        <div class="verdict-label">Pricing Verdict</div>
        <div class="verdict-text">{comparison.get("pricing_verdict", "&mdash;")}</div>
      </div>
      <div class="verdict-box" style="border-left-color:{c2};">
        <div class="verdict-label">Positioning Verdict</div>
        <div class="verdict-text">{comparison.get("positioning_verdict", "&mdash;")}</div>
      </div>
    </div>
  </div>
  <div class="recommendation">
    <div class="rec-label">{"GTM Recommendation — " + focus if focus else "GTM Recommendation"}</div>
    <div class="rec-text">{comparison.get("recommendation", "&mdash;")}</div>
  </div>
  <div class="report-footer">RIVA COMPETITIVE INTELLIGENCE &middot; {date_str} &middot; CONFIDENTIAL</div>
</div>
</body>
</html>"""


def get_available_domains() -> list:
    if not EXTRACTS_DIR.exists():
        return []
    return sorted(
        d.name for d in EXTRACTS_DIR.iterdir()
        if d.is_dir() and any(d.rglob("*.txt"))
    )


def main():
    missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "GEMINI_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        return

    domains = get_available_domains()
    if not domains:
        print("No domains found in testing/extracts/ — run the browser agent first.")
        return

    print("\nRiva Report Generator")
    print("=" * 40)
    print("Available domains:")
    for i, d in enumerate(domains, 1):
        print(f"  {i}. {d}")

    print()
    selected = []
    for slot in ["First domain", "Second domain (optional, press Enter to skip)"]:
        raw = input(f"{slot}: ").strip()
        if not raw:
            break
        domain = domains[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(domains) else raw
        if domain not in domains:
            print(f"  '{domain}' not found.")
            break
        selected.append(domain)

    if not selected:
        print("No domains selected.")
        return

    print(f"\nGenerating report for: {', '.join(selected)}")

    print("  Scraping brand assets...", end=" ", flush=True)
    images = {d: scrape_brand_assets(d) for d in selected}
    for d, imgs in images.items():
        color = imgs.get("brand_color") or "no color found"
        found = [k for k, v in imgs.items() if v and k != "brand_color"]
        print(f"\n    {d}: color={color}, images={found if found else 'none'}", end="")
    print()

    print("  Querying Vectorize + generating intel...", end=" ", flush=True)
    intel = generate_intel(selected)
    print("done")

    print("  Rendering HTML...", end=" ", flush=True)
    html = render_html(intel, images, focus=None)
    print("done")

    slug     = "_vs_".join(intel["domains"][d]["display_name"].lower().replace(" ", "-") for d in selected)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"riva_report_{slug}_{ts}.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"\n  Report saved: {out_path}")
    print("  Open in Chrome and Ctrl+P -> Save as PDF for a print-ready one-pager.")


if __name__ == "__main__":
    main()

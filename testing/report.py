"""
testing/report.py
Generates a competitive intelligence one-pager HTML report from Vectorize data.
Logos are scraped from each site's homepage and embedded as base64.

Usage:
    python testing/report.py
"""

import os
import json
import base64
import requests
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


# ---------------------------------------------------------------------------
# Vectorize helpers
# ---------------------------------------------------------------------------
def embed(text: str) -> list:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{EMBED_MODEL}"
    resp = requests.post(url, headers=CF_HEADERS, json={"text": [text]}, timeout=20)
    resp.raise_for_status()
    return resp.json()["result"]["data"][0]


def vectorize_query(question: str, top_k: int = 20, domain: str = None) -> list:
    url     = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}/query"
    payload = {"vector": embed(question), "topK": top_k, "returnMetadata": "all"}
    resp    = requests.post(url, headers=CF_HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    matches = resp.json()["result"].get("matches", [])
    if domain:
        matches = [m for m in matches if m.get("metadata", {}).get("domain") == domain]
    return matches


def chunks_for(domain: str, topic: str, top_k: int = 12) -> str:
    matches = vectorize_query(topic, top_k=top_k, domain=domain)
    return "\n\n".join(m["metadata"].get("text", "") for m in matches)


# ---------------------------------------------------------------------------
# Image scraping — logos only, targeted
# ---------------------------------------------------------------------------
def scrape_brand_assets(domain: str) -> dict:
    images_dir = EXTRACTS_DIR / domain / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"https://{domain}"
    assets   = {"logo": None, "og_image": None}

    try:
        resp = requests.get(base_url, headers=BROWSER_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        # --- Logo: try in priority order ---
        logo_url = _find_logo_url(soup, base_url)
        if logo_url:
            assets["logo"] = _fetch_image_b64(logo_url, images_dir, "logo")

        # --- OG image as hero background ---
        og_tag = (
            soup.find("meta", property="og:image") or
            soup.find("meta", attrs={"name": "og:image"})
        )
        if og_tag and og_tag.get("content"):
            og_url = urljoin(base_url, og_tag["content"])
            assets["og_image"] = _fetch_image_b64(og_url, images_dir, "og_image")

    except Exception as e:
        print(f"  Image scrape warning for {domain}: {e}")

    return assets


def _find_logo_url(soup: BeautifulSoup, base_url: str) -> str:
    """
    Try to find the actual brand logo in priority order:
    1. <img> tag with 'logo' in id/class/alt/src (in header/nav)
    2. SVG <img> anywhere in header/nav
    3. apple-touch-icon (high quality, always the brand icon)
    4. Largest favicon
    """
    # 1. Look in header/nav for an img with 'logo' signals
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
            # SVG in header is almost always the logo
            src = img.get("src", "")
            if src.endswith(".svg"):
                return urljoin(base_url, src)

    # 2. Any img with logo in attributes site-wide
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

    # 3. apple-touch-icon (reliable brand icon, usually 180x180)
    for rel in ["apple-touch-icon", "apple-touch-icon-precomposed"]:
        tag = soup.find("link", rel=lambda r: r and rel in r)
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])

    # 4. Largest favicon
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


# ---------------------------------------------------------------------------
# Intel generation via Gemini
# ---------------------------------------------------------------------------
def generate_intel(domains: list) -> dict:
    client   = genai.Client(api_key=GEMINI_KEY)
    sections = {}

    for d in domains:
        sections[d] = {
            "pricing":     chunks_for(d, "pricing tiers cost plans price per month"),
            "features":    chunks_for(d, "features capabilities product functionality"),
            "positioning": chunks_for(d, "about company product description target audience"),
        }

    context_block = ""
    for d, s in sections.items():
        context_block += (
            f"\n\n=== {d} ===\n"
            f"PRICING:\n{s['pricing']}\n\n"
            f"FEATURES:\n{s['features']}\n\n"
            f"POSITIONING:\n{s['positioning']}"
        )

    # Build the schema string outside the f-string to avoid backslash issues
    domain_schemas = []
    for d in domains:
        domain_schemas.append(
            '    "' + d + '": {\n'
            '      "display_name": "Short brand name",\n'
            '      "tagline": "One sentence description",\n'
            '      "target_audience": "Who they serve",\n'
            '      "pricing_tiers": [\n'
            '        {"name": "tier", "price": "price or Free", "highlights": ["f1","f2","f3"]}\n'
            '      ],\n'
            '      "top_features": ["f1","f2","f3","f4","f5"],\n'
            '      "strengths": ["s1","s2","s3"],\n'
            '      "weaknesses": ["w1","w2"]\n'
            '    }'
        )
    domains_schema = ",\n".join(domain_schemas)

    only_fields = []
    for d in domains:
        key = "only_in_" + d.replace(".", "_")
        only_fields.append(f'    "{key}": ["feature1","feature2","feature3"]')
    only_schema = ",\n".join(only_fields)

    schema = (
        '{\n'
        '  "domains": {\n' +
        domains_schema + '\n'
        '  },\n'
        '  "comparison": {\n' +
        only_schema + ',\n'
        '    "pricing_verdict": "One sentence on pricing differences",\n'
        '    "positioning_verdict": "One sentence on market positioning differences",\n'
        '    "recommendation": "Two sentence GTM recommendation"\n'
        '  }\n'
        '}'
    )

    prompt = (
        "You are a competitive intelligence analyst. Based solely on the context below, "
        "produce a structured JSON report.\n\n"
        "CONTEXT:\n" + context_block + "\n\n"
        "Return ONLY valid JSON (no markdown fences) matching this exact schema:\n" + schema
    )

    res  = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    text = res.text.strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json").strip()
        text = text.split("```")[0].strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# HTML one-pager renderer
# ---------------------------------------------------------------------------
def render_html(intel: dict, images: dict) -> str:
    domains    = list(intel["domains"].keys())
    comparison = intel["comparison"]
    date_str   = datetime.now().strftime("%B %d, %Y")
    is_dual    = len(domains) == 2
    grid_cols  = "1fr 1fr" if is_dual else "1fr"

    def domain_card(d: str) -> str:
        info   = intel["domains"][d]
        imgs   = images.get(d, {})
        logo   = imgs.get("logo")
        og_img = imgs.get("og_image")

        logo_html = f'<img src="{logo}" class="logo-img" alt="{d} logo">' if logo else ""
        hero_html = (
            f'<img src="{og_img}" class="hero-img" alt="{d} hero">'
            if og_img else
            '<div class="hero-placeholder">' + info["display_name"] + '</div>'
        )

        tiers_html = ""
        for tier in info.get("pricing_tiers", []):
            highlights = "".join("<li>" + h + "</li>" for h in tier.get("highlights", []))
            tiers_html += (
                '<div class="tier-card">'
                '<div class="tier-name">' + tier["name"] + "</div>"
                '<div class="tier-price">' + tier["price"] + "</div>"
                '<ul class="tier-features">' + highlights + "</ul>"
                "</div>"
            )

        features_html  = "".join("<li>" + f + "</li>" for f in info.get("top_features", []))
        strengths_html = "".join("<li>" + s + "</li>" for s in info.get("strengths", []))
        weakness_html  = "".join("<li>" + w + "</li>" for w in info.get("weaknesses", []))

        return (
            '<div class="domain-col">'
            '<div class="domain-header">'
            + logo_html +
            '<div class="domain-meta">'
            '<h2>' + info["display_name"] + "</h2>"
            '<p class="tagline">' + info["tagline"] + "</p>"
            '<span class="audience-badge">' + info["target_audience"] + "</span>"
            "</div></div>"
            + hero_html +
            '<div class="section-label">Pricing</div>'
            '<div class="tiers-row">' + tiers_html + "</div>"
            '<div class="section-label">Top Features</div>'
            '<ul class="feature-list">' + features_html + "</ul>"
            '<div class="two-col">'
            '<div><div class="section-label green">Strengths</div>'
            '<ul class="feature-list">' + strengths_html + "</ul></div>"
            '<div><div class="section-label red">Weaknesses</div>'
            '<ul class="feature-list">' + weakness_html + "</ul></div>"
            "</div></div>"
        )

    unique_sections = ""
    for d in domains:
        key   = "only_in_" + d.replace(".", "_")
        items = comparison.get(key, [])
        if items:
            name  = intel["domains"][d]["display_name"]
            pills = "".join('<span class="pill">' + i + "</span>" for i in items)
            unique_sections += (
                '<div class="unique-col">'
                '<div class="section-label">Only in ' + name + "</div>"
                '<div class="pills">' + pills + "</div>"
                "</div>"
            )

    unique_grid_cols = "1fr 1fr" if is_dual else "1fr"
    cards_html       = "".join(domain_card(d) for d in domains)
    title_str        = " vs ".join(intel["domains"][d]["display_name"] for d in domains)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Riva Intel &mdash; """ + title_str + """</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f4f6fa; color: #1a1f2e; font-size: 13px; line-height: 1.5;
  }
  .page { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }
  .report-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 28px; padding-bottom: 16px; border-bottom: 2px solid #00cccc;
  }
  .report-title { font-size: 22px; font-weight: 700; letter-spacing: 1px; color: #0a0f1e; }
  .report-title span { color: #00aaaa; }
  .report-meta { font-size: 11px; color: #888; text-align: right; }
  .domain-grid {
    display: grid; grid-template-columns: """ + grid_cols + """;
    gap: 20px; margin-bottom: 20px;
  }
  .domain-col {
    background: white; border-radius: 12px; padding: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
  }
  .domain-header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
  .logo-img { width: 40px; height: 40px; object-fit: contain; border-radius: 8px; }
  .domain-meta h2 { font-size: 17px; font-weight: 700; }
  .tagline { font-size: 11px; color: #666; margin-top: 2px; }
  .audience-badge {
    display: inline-block; background: #e8f7f7; color: #007a7a;
    border-radius: 20px; padding: 2px 10px; font-size: 10px;
    font-weight: 600; margin-top: 4px; letter-spacing: 0.3px;
  }
  .hero-img { width: 100%; height: 130px; object-fit: cover; border-radius: 8px; margin-bottom: 14px; }
  .hero-placeholder {
    width: 100%; height: 50px; background: linear-gradient(135deg, #e8f7f7, #d0eaea);
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
    font-weight: 700; color: #007a7a; font-size: 16px; margin-bottom: 14px; letter-spacing: 2px;
  }
  .section-label {
    font-size: 9px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase;
    color: #aaa; margin: 14px 0 8px;
  }
  .section-label.green { color: #00a86b; }
  .section-label.red   { color: #e05555; }
  .tiers-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .tier-card {
    flex: 1; min-width: 80px; background: #f8fafc;
    border: 1px solid #e5eaf2; border-radius: 8px; padding: 10px;
  }
  .tier-name  { font-size: 10px; font-weight: 700; color: #007a7a; text-transform: uppercase; letter-spacing: 0.5px; }
  .tier-price { font-size: 15px; font-weight: 800; margin: 4px 0; color: #1a1f2e; }
  .tier-features { padding-left: 12px; font-size: 10px; color: #666; }
  .tier-features li { margin-bottom: 2px; }
  .feature-list { padding-left: 14px; }
  .feature-list li { margin-bottom: 3px; font-size: 12px; color: #333; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .comparison-section {
    background: white; border-radius: 12px; padding: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07); margin-bottom: 20px;
  }
  .comp-header {
    font-size: 13px; font-weight: 700; color: #1a1f2e;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
  }
  .comp-header::before {
    content: ''; display: inline-block; width: 3px; height: 16px;
    background: #00cccc; border-radius: 2px;
  }
  .unique-grid { display: grid; grid-template-columns: """ + unique_grid_cols + """; gap: 20px; margin-bottom: 16px; }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill {
    background: #e8f7f7; color: #007a7a; border-radius: 20px;
    padding: 3px 10px; font-size: 11px; font-weight: 500;
  }
  .verdict-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 14px; }
  .verdict-box {
    background: #f8fafc; border-left: 3px solid #00cccc;
    border-radius: 0 8px 8px 0; padding: 10px 14px;
  }
  .verdict-label { font-size: 9px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; color: #aaa; margin-bottom: 4px; }
  .verdict-text  { font-size: 12px; color: #333; }
  .recommendation {
    background: linear-gradient(135deg, #0a1628, #0d2040);
    color: white; border-radius: 12px; padding: 20px 24px; margin-top: 4px;
  }
  .rec-label { font-size: 9px; font-weight: 800; letter-spacing: 3px; text-transform: uppercase; color: #00cccc; margin-bottom: 8px; }
  .rec-text  { font-size: 13px; line-height: 1.7; color: #cde; }
  .report-footer { margin-top: 20px; text-align: center; font-size: 10px; color: #bbb; letter-spacing: 1px; }
  @media print { body { background: white; } .page { padding: 16px; } }
</style>
</head>
<body>
<div class="page">
  <div class="report-header">
    <div>
      <div class="report-title">RIVA <span>INTELLIGENCE</span></div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Competitive Analysis &mdash; """ + title_str + """</div>
    </div>
    <div class="report-meta">Generated by Riva<br>""" + date_str + """</div>
  </div>
  <div class="domain-grid">""" + cards_html + """</div>
  <div class="comparison-section">
    <div class="comp-header">Competitive Differentiators</div>
    <div class="unique-grid">""" + unique_sections + """</div>
    <div class="verdict-row">
      <div class="verdict-box">
        <div class="verdict-label">Pricing Verdict</div>
        <div class="verdict-text">""" + comparison.get("pricing_verdict", "&mdash;") + """</div>
      </div>
      <div class="verdict-box">
        <div class="verdict-label">Positioning Verdict</div>
        <div class="verdict-text">""" + comparison.get("positioning_verdict", "&mdash;") + """</div>
      </div>
    </div>
  </div>
  <div class="recommendation">
    <div class="rec-label">GTM Recommendation</div>
    <div class="rec-text">""" + comparison.get("recommendation", "&mdash;") + """</div>
  </div>
  <div class="report-footer">RIVA COMPETITIVE INTELLIGENCE &middot; """ + date_str + """ &middot; CONFIDENTIAL</div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
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
        found = [k for k, v in imgs.items() if v]
        print(f"\n    {d}: {found if found else 'no images found'}", end="")
    print()

    print("  Querying Vectorize + generating intel...", end=" ", flush=True)
    intel = generate_intel(selected)
    print("done")

    print("  Rendering HTML...", end=" ", flush=True)
    html = render_html(intel, images)
    print("done")

    slug     = "_vs_".join(intel["domains"][d]["display_name"].lower().replace(" ", "-") for d in selected)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"riva_report_{slug}_{ts}.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"\n  Report saved: {out_path}")
    print("  Open in Chrome and Ctrl+P -> Save as PDF for a print-ready one-pager.")


if __name__ == "__main__":
    main()

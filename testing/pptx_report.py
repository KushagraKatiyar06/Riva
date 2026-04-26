"""
testing/pptx_report.py
Generates a competitive intelligence PowerPoint deck from Vectorize data.
Reuses the same data pipeline as report.py.

Usage:
    python testing/pptx_report.py

Requires:
    pip install python-pptx Pillow
"""

import os
import io
import time
import base64
import json
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from lxml import etree

# Reuse shared helpers from report.py
from report import (
    generate_intel,
    scrape_brand_assets,
    get_available_domains,
    chunks_for,
)

load_dotenv()

GEMINI_KEY  = os.getenv("GEMINI_API_KEY")
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
DARK_BG     = RGBColor(0x0A, 0x16, 0x28)
MID_BG      = RGBColor(0x0D, 0x20, 0x40)
ACCENT      = RGBColor(0x00, 0xCC, 0xCC)
ACCENT_DARK = RGBColor(0x00, 0x88, 0x88)
WHITE       = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_TEXT  = RGBColor(0xCC, 0xDD, 0xEE)
DIM_TEXT    = RGBColor(0x66, 0x88, 0xAA)
GREEN       = RGBColor(0x00, 0xA8, 0x6B)
RED         = RGBColor(0xE0, 0x55, 0x55)
CARD_BG     = RGBColor(0x11, 0x22, 0x3A)

W  = Inches(13.333)   # widescreen width
H  = Inches(7.5)      # widescreen height


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
def new_prs() -> Presentation:
    prs = Presentation()
    prs.slide_width  = W
    prs.slide_height = H
    return prs


def blank_slide(prs: Presentation):
    layout = prs.slide_layouts[6]  # completely blank
    return prs.slides.add_slide(layout)


def set_bg(slide, color: RGBColor):
    bg   = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_rect(slide, x, y, w, h, fill: RGBColor = None, line: RGBColor = None, line_w: int = 0):
    shape = slide.shapes.add_shape(1, x, y, w, h)   # MSO_SHAPE_TYPE.RECTANGLE = 1
    shape.line.fill.background()
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    else:
        shape.fill.background()
    if line and line_w:
        shape.line.color.rgb = line
        shape.line.width     = line_w
    else:
        shape.line.fill.background()
    return shape


def add_text(slide, text: str, x, y, w, h,
             size: int = 18, bold: bool = False, color: RGBColor = WHITE,
             align=PP_ALIGN.LEFT, wrap: bool = True) -> None:
    txBox = slide.shapes.add_textbox(x, y, w, h)
    tf    = txBox.text_frame
    tf.word_wrap = wrap
    p  = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text if isinstance(text, str) else str(text)
    run.font.size  = Pt(size)
    run.font.bold  = bold
    run.font.color.rgb = color


def add_text_lines(slide, lines: list, x, y, w, h,
                   size: int = 14, color: RGBColor = LIGHT_TEXT,
                   bullet: bool = True) -> None:
    txBox = slide.shapes.add_textbox(x, y, w, h)
    tf    = txBox.text_frame
    tf.word_wrap = True
    first = True
    for line in lines:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_before = Pt(4)
        run = p.add_run()
        run.text = ("• " if bullet else "") + (line if isinstance(line, str) else " — ".join(str(v) for v in line.values() if v) if isinstance(line, dict) else str(line))
        run.font.size  = Pt(size)
        run.font.color.rgb = color


def image_b64_to_stream(b64_data_uri: str):
    """Convert a base64 data-URI to a BytesIO stream for python-pptx."""
    if not b64_data_uri or "base64," not in b64_data_uri:
        return None
    try:
        data = base64.b64decode(b64_data_uri.split("base64,")[1])
        return io.BytesIO(data)
    except Exception:
        return None


def add_logo(slide, b64_uri: str, x, y, size=Inches(0.55)):
    stream = image_b64_to_stream(b64_uri)
    if not stream:
        return
    try:
        slide.shapes.add_picture(stream, x, y, width=size, height=size)
    except Exception:
        pass


def accent_bar(slide, y=Inches(0.55), width=W, height=Inches(0.04)):
    add_rect(slide, Inches(0), y, width, height, fill=ACCENT)


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------
def slide_title(prs, intel: dict, images: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)

    # Gradient-ish overlay strip
    add_rect(slide, Inches(0), Inches(0), W, Inches(2.2), fill=MID_BG)
    accent_bar(slide, y=Inches(2.2), height=Inches(0.05))

    # RIVA label
    add_text(slide, "RIVA INTELLIGENCE", Inches(0.6), Inches(0.22), Inches(8), Inches(0.5),
             size=11, bold=True, color=ACCENT)

    # Main title
    domains = list(intel["domains"].keys())
    names   = " vs ".join(intel["domains"][d]["display_name"] for d in domains)
    add_text(slide, "Competitive Analysis", Inches(0.6), Inches(0.72), Inches(10), Inches(0.7),
             size=36, bold=True, color=WHITE)
    add_text(slide, names, Inches(0.6), Inches(1.45), Inches(10), Inches(0.55),
             size=22, bold=False, color=ACCENT)

    # Date bottom right
    add_text(slide, datetime.now().strftime("%B %d, %Y"),
             Inches(10), Inches(6.9), Inches(3), Inches(0.4),
             size=11, color=DIM_TEXT, align=PP_ALIGN.RIGHT)

    # Logos bottom area
    x_pos = Inches(0.6)
    for d in domains:
        logo = images.get(d, {}).get("logo")
        add_logo(slide, logo, x_pos, Inches(2.6), size=Inches(0.7))
        add_text(slide, intel["domains"][d]["display_name"],
                 x_pos + Inches(0.85), Inches(2.7), Inches(3), Inches(0.4),
                 size=16, bold=True, color=WHITE)
        add_text(slide, intel["domains"][d]["tagline"],
                 x_pos + Inches(0.85), Inches(3.1), Inches(5), Inches(0.5),
                 size=12, color=LIGHT_TEXT)
        x_pos += Inches(6.5)

    # Confidential footer
    add_text(slide, "CONFIDENTIAL", Inches(0), Inches(7.1), W, Inches(0.35),
             size=9, color=DIM_TEXT, align=PP_ALIGN.CENTER)


def slide_exec_summary(prs, intel: dict, images: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)
    accent_bar(slide)

    add_text(slide, "EXECUTIVE SUMMARY", Inches(0.6), Inches(0.12), Inches(10), Inches(0.4),
             size=10, bold=True, color=ACCENT)

    domains = list(intel["domains"].keys())
    col_w   = Inches(5.8)
    x_positions = [Inches(0.4), Inches(7.0)]

    for i, d in enumerate(domains):
        info = intel["domains"][d]
        x    = x_positions[i]

        # Card background
        add_rect(slide, x, Inches(0.65), col_w, Inches(6.5), fill=CARD_BG)
        add_rect(slide, x, Inches(0.65), col_w, Inches(0.06), fill=ACCENT if i == 0 else GREEN)

        # Logo + name
        logo = images.get(d, {}).get("logo")
        add_logo(slide, logo, x + Inches(0.25), Inches(0.85), size=Inches(0.5))
        add_text(slide, info["display_name"],
                 x + Inches(0.9), Inches(0.9), Inches(4), Inches(0.45),
                 size=20, bold=True, color=WHITE)
        add_text(slide, info["target_audience"],
                 x + Inches(0.25), Inches(1.45), col_w - Inches(0.5), Inches(0.35),
                 size=11, color=ACCENT)

        add_text(slide, info["tagline"],
                 x + Inches(0.25), Inches(1.85), col_w - Inches(0.5), Inches(0.7),
                 size=13, color=LIGHT_TEXT)

        # Top features
        add_text(slide, "TOP FEATURES", x + Inches(0.25), Inches(2.65),
                 col_w - Inches(0.5), Inches(0.3), size=9, bold=True, color=DIM_TEXT)
        add_text_lines(slide, info.get("top_features", [])[:5],
                       x + Inches(0.25), Inches(2.95), col_w - Inches(0.5), Inches(1.8),
                       size=12, color=LIGHT_TEXT)

        # Strengths
        add_text(slide, "STRENGTHS", x + Inches(0.25), Inches(4.85),
                 col_w - Inches(0.5), Inches(0.3), size=9, bold=True, color=GREEN)
        add_text_lines(slide, info.get("strengths", []),
                       x + Inches(0.25), Inches(5.15), col_w - Inches(0.5), Inches(1.0),
                       size=11, color=LIGHT_TEXT)


def slide_pricing(prs, intel: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)
    accent_bar(slide)

    add_text(slide, "PRICING COMPARISON", Inches(0.6), Inches(0.12), Inches(10), Inches(0.4),
             size=10, bold=True, color=ACCENT)

    domains = list(intel["domains"].keys())
    col_w   = Inches(5.8)
    x_positions = [Inches(0.4), Inches(7.0)]

    for i, d in enumerate(domains):
        info  = intel["domains"][d]
        x     = x_positions[i]
        tiers = info.get("pricing_tiers", [])

        add_text(slide, info["display_name"],
                 x, Inches(0.65), col_w, Inches(0.45),
                 size=18, bold=True, color=WHITE)

        y = Inches(1.2)
        tier_h = Inches(1.55)
        for tier in tiers[:4]:
            add_rect(slide, x, y, col_w - Inches(0.1), tier_h, fill=CARD_BG)
            add_rect(slide, x, y, Inches(0.04), tier_h,
                     fill=ACCENT if i == 0 else GREEN)

            add_text(slide, tier["name"].upper(),
                     x + Inches(0.18), y + Inches(0.1), Inches(2.5), Inches(0.3),
                     size=9, bold=True, color=ACCENT if i == 0 else GREEN)
            add_text(slide, tier["price"],
                     x + Inches(0.18), y + Inches(0.38), Inches(2.5), Inches(0.45),
                     size=18, bold=True, color=WHITE)

            highlights = tier.get("highlights", [])[:3]
            add_text_lines(slide, highlights,
                           x + Inches(0.18), y + Inches(0.88), col_w - Inches(0.4), Inches(0.75),
                           size=10, color=DIM_TEXT)
            y += tier_h + Inches(0.12)


def slide_differentiators(prs, intel: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)
    accent_bar(slide)

    add_text(slide, "COMPETITIVE DIFFERENTIATORS", Inches(0.6), Inches(0.12), Inches(10), Inches(0.4),
             size=10, bold=True, color=ACCENT)

    domains    = list(intel["domains"].keys())
    comparison = intel["comparison"]
    col_w      = Inches(5.8)
    x_positions = [Inches(0.4), Inches(7.0)]

    for i, d in enumerate(domains):
        info  = intel["domains"][d]
        x     = x_positions[i]
        key   = "only_in_" + d.replace(".", "_")
        items = comparison.get(key, [])

        add_text(slide, "ONLY IN " + info["display_name"].upper(),
                 x, Inches(0.65), col_w, Inches(0.4),
                 size=14, bold=True, color=ACCENT if i == 0 else GREEN)

        y = Inches(1.15)
        for item in items[:8]:
            add_rect(slide, x, y, col_w - Inches(0.15), Inches(0.48), fill=CARD_BG)
            add_rect(slide, x, y, Inches(0.04), Inches(0.48),
                     fill=ACCENT if i == 0 else GREEN)
            add_text(slide, item,
                     x + Inches(0.18), y + Inches(0.08),
                     col_w - Inches(0.4), Inches(0.35),
                     size=12, color=WHITE)
            y += Inches(0.55)

        # Weaknesses at bottom
        add_text(slide, "GAPS TO ADDRESS",
                 x, Inches(5.55), col_w, Inches(0.3),
                 size=9, bold=True, color=RED)
        add_text_lines(slide, info.get("weaknesses", []),
                       x, Inches(5.85), col_w, Inches(1.3),
                       size=11, color=LIGHT_TEXT)


def slide_gtm(prs, intel: dict, gtm: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)
    accent_bar(slide)

    add_text(slide, "GTM STRATEGY & RECOMMENDATION", Inches(0.6), Inches(0.12), Inches(10), Inches(0.4),
             size=10, bold=True, color=ACCENT)

    comparison = intel["comparison"]

    # Pricing verdict
    add_rect(slide, Inches(0.4), Inches(0.7), Inches(5.8), Inches(1.4), fill=CARD_BG)
    add_rect(slide, Inches(0.4), Inches(0.7), Inches(0.04), Inches(1.4), fill=ACCENT)
    add_text(slide, "PRICING VERDICT", Inches(0.6), Inches(0.78), Inches(5.5), Inches(0.3),
             size=9, bold=True, color=DIM_TEXT)
    add_text(slide, comparison.get("pricing_verdict", ""),
             Inches(0.6), Inches(1.08), Inches(5.5), Inches(0.85),
             size=13, color=WHITE)

    # Positioning verdict
    add_rect(slide, Inches(7.0), Inches(0.7), Inches(5.8), Inches(1.4), fill=CARD_BG)
    add_rect(slide, Inches(7.0), Inches(0.7), Inches(0.04), Inches(1.4), fill=GREEN)
    add_text(slide, "POSITIONING VERDICT", Inches(7.2), Inches(0.78), Inches(5.5), Inches(0.3),
             size=9, bold=True, color=DIM_TEXT)
    add_text(slide, comparison.get("positioning_verdict", ""),
             Inches(7.2), Inches(1.08), Inches(5.5), Inches(0.85),
             size=13, color=WHITE)

    # Key messages
    add_text(slide, "KEY MESSAGES", Inches(0.6), Inches(2.3), Inches(6), Inches(0.35),
             size=9, bold=True, color=DIM_TEXT)
    add_text_lines(slide, gtm.get("key_messages", []),
                   Inches(0.4), Inches(2.65), Inches(6), Inches(1.8),
                   size=13, color=LIGHT_TEXT)

    # Objection handling
    add_text(slide, "OBJECTION HANDLING", Inches(7.0), Inches(2.3), Inches(6), Inches(0.35),
             size=9, bold=True, color=DIM_TEXT)
    add_text_lines(slide, gtm.get("objections", []),
                   Inches(7.0), Inches(2.65), Inches(6), Inches(1.8),
                   size=13, color=LIGHT_TEXT)

    # Recommendation banner
    add_rect(slide, Inches(0), Inches(4.65), W, Inches(2.6),
             fill=RGBColor(0x0D, 0x20, 0x40))
    add_rect(slide, Inches(0), Inches(4.65), W, Inches(0.05), fill=ACCENT)

    add_text(slide, "RECOMMENDATION", Inches(0.6), Inches(4.82), Inches(12), Inches(0.3),
             size=9, bold=True, color=ACCENT)
    add_text(slide, comparison.get("recommendation", ""),
             Inches(0.6), Inches(5.15), Inches(12), Inches(1.8),
             size=15, color=WHITE)


def slide_action_items(prs, gtm: dict):
    slide = blank_slide(prs)
    set_bg(slide, DARK_BG)
    accent_bar(slide)

    add_text(slide, "ACTION ITEMS", Inches(0.6), Inches(0.12), Inches(10), Inches(0.4),
             size=10, bold=True, color=ACCENT)

    actions = gtm.get("action_items", [])
    cols    = [actions[:len(actions)//2 + 1], actions[len(actions)//2 + 1:]]
    x_positions = [Inches(0.4), Inches(7.0)]

    for col_items, x in zip(cols, x_positions):
        y = Inches(0.9)
        for j, action in enumerate(col_items):
            add_rect(slide, x, y, Inches(5.8), Inches(0.85), fill=CARD_BG)
            # Number bubble
            add_rect(slide, x, y, Inches(0.5), Inches(0.85),
                     fill=ACCENT if x == x_positions[0] else GREEN)
            add_text(slide, str(j + 1 + (len(cols[0]) if x == x_positions[1] else 0)),
                     x + Inches(0.08), y + Inches(0.18), Inches(0.35), Inches(0.4),
                     size=14, bold=True, color=DARK_BG, align=PP_ALIGN.CENTER)
            add_text(slide, action,
                     x + Inches(0.6), y + Inches(0.18), Inches(5.1), Inches(0.55),
                     size=12, color=WHITE)
            y += Inches(0.98)

    add_text(slide, "RIVA COMPETITIVE INTELLIGENCE  ·  CONFIDENTIAL",
             Inches(0), Inches(7.1), W, Inches(0.3),
             size=8, color=DIM_TEXT, align=PP_ALIGN.CENTER)


# ---------------------------------------------------------------------------
# GTM content generation (separate from intel — marketing-specific)
# ---------------------------------------------------------------------------
def generate_gtm(domains: list, intel: dict, focus: str = None) -> dict:
    client = genai.Client(api_key=GEMINI_KEY)

    context = ""
    for d in domains:
        context += (
            f"\n=== {d} ===\n"
            f"{chunks_for(d, 'pricing features positioning target audience competitors')}\n"
        )

    names = [intel["domains"][d]["display_name"] for d in domains]
    focus_instruction = (
        f"\n\nFOCUS: The user wants the GTM strategy to emphasize **{focus}**. "
        f"Tailor key_messages, objections, and action_items specifically around {focus}."
    ) if focus else ""
    prompt = (
        "You are a B2B marketing strategist. Based on the competitive context below, "
        "generate actionable GTM (go-to-market) content.\n\n"
        "CONTEXT:\n" + context + "\n\n"
        "Companies being compared: " + " vs ".join(names) + "\n\n"
        "Return ONLY valid JSON with this schema:\n"
        "{\n"
        '  "key_messages": ["3-4 key selling messages to use against the competitor"],\n'
        '  "objections": ["3-4 common objections and one-line rebuttals"],\n'
        '  "action_items": ["6-8 specific next steps for the GTM team"]\n'
        "}"
        + focus_instruction
    )

    for attempt in range(3):
        res  = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = res.text.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
            text = text.split("```")[0].strip()
        start = text.find('{')
        end   = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end + 1]
        try:
            result = json.loads(text)
            # Normalize list fields — Gemini sometimes returns dicts instead of strings
            def _to_str(item):
                if isinstance(item, str):
                    return item
                if isinstance(item, dict):
                    return " — ".join(str(v) for v in item.values() if v)
                return str(item)
            for key in ("key_messages", "objections", "action_items"):
                if key in result and isinstance(result[key], list):
                    result[key] = [_to_str(i) for i in result[key]]
            return result
        except json.JSONDecodeError as e:
            print(f"  generate_gtm JSON parse error (attempt {attempt+1}/3): {e}")
            if attempt == 2:
                raise
            time.sleep(5)
    raise RuntimeError("generate_gtm failed after 3 attempts")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "GEMINI_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        return

    domains = get_available_domains()
    if not domains:
        print("No domains found in testing/extracts/ — run the browser agent first.")
        return

    print("\nRiva PowerPoint Generator")
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

    print(f"\nGenerating deck for: {', '.join(selected)}")

    print("  Scraping brand assets...", end=" ", flush=True)
    images = {d: scrape_brand_assets(d) for d in selected}
    print("done")

    print("  Querying Vectorize + generating intel...", end=" ", flush=True)
    intel = generate_intel(selected)
    print("done")

    print("  Generating GTM strategy content...", end=" ", flush=True)
    gtm = generate_gtm(selected, intel)
    print("done")

    print("  Building slides...", end=" ", flush=True)
    prs = new_prs()
    slide_title(prs, intel, images)
    slide_exec_summary(prs, intel, images)
    slide_pricing(prs, intel)
    slide_differentiators(prs, intel)
    slide_gtm(prs, intel, gtm)
    slide_action_items(prs, gtm)
    print("done")

    names    = "_vs_".join(intel["domains"][d]["display_name"].lower().replace(" ", "-") for d in selected)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"riva_deck_{names}_{ts}.pptx"
    prs.save(out_path)

    print(f"\n  Deck saved: {out_path}")
    print(f"  {len(prs.slides)} slides — open in PowerPoint or Google Slides.")


if __name__ == "__main__":
    main()

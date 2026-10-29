"""Render a 1200x630 social preview image for the site.

Opens an inline HTML card page (dark palette, title + tagline + a hint of the
rendering), screenshots it headlessly, saves to web/og.png. Rerun any time the
hero copy or branding changes.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "og.png"

CARD_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600&family=Archivo+Narrow:wght@400;500&display=swap">
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; width: 1200px; height: 630px; overflow: hidden; }
    body {
      background: radial-gradient(ellipse at 70% 45%, #3a2126 0%, #1d1719 55%, #120e10 100%);
      font-family: 'Archivo', system-ui, sans-serif;
      color: #efe7e2;
      position: relative;
    }
    .layer {
      position: absolute; inset: 0;
      display: flex; flex-direction: column; justify-content: center;
      padding: 72px 88px;
    }
    .kicker {
      font-family: 'Archivo Narrow', 'Archivo', sans-serif;
      font-size: 20px; letter-spacing: 0.2em; text-transform: uppercase;
      color: #a69a97; margin-bottom: 28px;
    }
    h1 {
      font-size: 92px; font-weight: 500; line-height: 1.0; letter-spacing: -0.015em;
      margin: 0 0 36px; max-width: 820px;
    }
    .tagline {
      font-family: 'Archivo Narrow', 'Archivo', sans-serif;
      font-size: 28px; line-height: 1.4; color: #d2c7c2; max-width: 760px;
      margin: 0;
    }
    .url {
      position: absolute; bottom: 56px; left: 88px;
      font-family: 'Archivo Narrow', 'Archivo', sans-serif;
      font-size: 20px; color: #a69a97; letter-spacing: 0.08em;
    }
    .accent {
      position: absolute; right: -200px; top: -200px;
      width: 1000px; height: 1000px; border-radius: 50%;
      background: radial-gradient(circle, rgba(245,184,76,0.14) 0%, rgba(245,184,76,0) 70%);
      pointer-events: none;
    }
    .noodles {
      position: absolute; right: 60px; top: 60px; width: 520px; height: 520px;
      opacity: 0.9; pointer-events: none;
    }
    .noodles svg { width: 100%; height: 100%; }
  </style>
</head>
<body>
  <div class="accent"></div>
  <div class="noodles">
    <svg viewBox="0 0 520 520" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="hub" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#f5b84c" stop-opacity="0.95"/>
          <stop offset="100%" stop-color="#8a5f1f" stop-opacity="0.65"/>
        </radialGradient>
      </defs>
      <!-- outer ring nodes + edges -->
      <g stroke="#efe7e2" stroke-width="1.5" fill="none" opacity="0.55">
        <path d="M260 260 C 160 180, 90 150, 55 115"/>
        <path d="M260 260 C 310 160, 350 110, 420 70"/>
        <path d="M260 260 C 190 310, 110 360, 60 440"/>
        <path d="M260 260 C 340 320, 420 350, 475 420"/>
        <path d="M260 260 C 390 240, 460 230, 495 220"/>
        <path d="M260 260 C 150 280, 70 290, 25 300"/>
        <path d="M260 260 C 230 170, 220 100, 220 40"/>
        <path d="M260 260 C 280 350, 300 420, 310 495"/>
      </g>
      <g fill="#efe7e2">
        <circle cx="55" cy="115" r="7"/>
        <circle cx="420" cy="70" r="7"/>
        <circle cx="60" cy="440" r="7"/>
        <circle cx="475" cy="420" r="7"/>
        <circle cx="495" cy="220" r="7"/>
        <circle cx="25" cy="300" r="7"/>
        <circle cx="220" cy="40" r="7"/>
        <circle cx="310" cy="495" r="7"/>
      </g>
      <!-- central hub -->
      <circle cx="260" cy="260" r="38" fill="url(#hub)" />
      <circle cx="260" cy="260" r="38" fill="none" stroke="#f5b84c" stroke-width="1" opacity="0.6"/>
    </svg>
  </div>
  <div class="layer">
    <div class="kicker">A rendering by craftnano</div>
    <h1>Where the Data Goes</h1>
    <p class="tagline">A map of sub-processor relationships across the major tech and corporate ecosystems. Every edge is a company publicly disclosing that a third party processes personal data on its behalf.</p>
  </div>
  <div class="url">data.craftnano.com</div>
</body>
</html>"""


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1200, "height": 630}, device_scale_factor=2)
        page = await ctx.new_page()
        await page.set_content(CARD_HTML)
        # Give web fonts a moment to load.
        await page.wait_for_timeout(1500)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUT), full_page=False, clip={"x": 0, "y": 0, "width": 1200, "height": 630})
        await browser.close()
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    asyncio.run(main())

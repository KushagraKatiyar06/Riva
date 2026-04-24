import os
import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from google import genai
from rich.console import Console
from rich.markdown import Markdown

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
console = Console()

async def analyze_with_gemini(content: str, url: str):
    """Uses Gemini to analyze the pricing data."""
    print(f"\n[Riva Brain] Analyzing data from {url}...")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"Analyze this pricing page content from {url}. Extract Plan Names, Costs, and Key Features:\n\n{content}"
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text
    except Exception as e:
        return f"Error: {e}"

async def run_smart_agent():
    console.rule("[bold cyan]Riva Smart GTM Agent[/bold cyan]")
    url = input("🔗 Enter URL: ")
    if not url.startswith('http'): url = 'https://' + url

    async with async_playwright() as p:
        # slow_mo=1000 to keep it visible
        browser = await p.chromium.launch(headless=False, slow_mo=1000)
        page = await browser.new_page()
        
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # 1. Look for Pricing
        print("Searching for Pricing/Plans...")
        # We try a few variations
        targets = [
            page.get_by_role("link", name="Pricing", exact=False),
            page.get_by_role("button", name="Pricing", exact=False),
            page.get_by_role("link", name="Plans", exact=False)
        ]

        success = False
        for target in targets:
            if await target.first.is_visible():
                print("Target found! Highlighting...")
                await target.first.evaluate("(el) => { el.style.border = '5px solid red'; el.style.backgroundColor = 'yellow'; }")
                await asyncio.sleep(2)
                
                # Some sites require a hover first if it's a menu
                print("Hovering to check for dropdowns...")
                await target.first.hover()
                await asyncio.sleep(1)
                
                print("Clicking target...")
                await target.first.click()
                await asyncio.sleep(4)
                
                print(f"Current URL: {page.url}")
                success = True
                break

        # 2. Extract and Analyze
        print("Extracting content...")
        await page.evaluate("window.scrollTo(0, 500)")
        content = await page.evaluate("() => document.body.innerText")
        
        analysis = await analyze_with_gemini(content, page.url)
        
        console.rule("[bold green]Final GTM Analysis[/bold green]")
        console.print(Markdown(analysis))
        
        print("\nClosing in 10 seconds...")
        await asyncio.sleep(10)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_smart_agent())

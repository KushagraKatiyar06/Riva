# Prototype GTM scraper using Firecrawl and Gemini. Not part of the main pipeline.
# Scrapes a URL to markdown via Firecrawl then asks Gemini to summarise GTM strategy.

import os
import asyncio
from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from google import genai
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.spinner import Spinner
from rich.markdown import Markdown

load_dotenv()
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

console = Console()

def check_api_keys():
    if not FIRECRAWL_API_KEY or not GEMINI_API_KEY:
        console.print(
            Panel(
                "[bold red]API Key Error:[/bold red] Check your `.env` file for FIRECRAWL_API_KEY and GEMINI_API_KEY.",
                title="Error",
                border_style="red"
            )
        )
        return False
    return True

async def scrape_website(url: str):
    with Live(Spinner("dots", text="[cyan]Scraping website with FireCrawl..."), refresh_per_second=10, transient=True):
        try:
            app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
            loop = asyncio.get_event_loop()
            
            scraped_data = await loop.run_in_executor(None, lambda: app.scrape(url))
            
            markdown_content = None
            if hasattr(scraped_data, 'markdown'):
                markdown_content = scraped_data.markdown
            elif isinstance(scraped_data, dict) and 'markdown' in scraped_data:
                markdown_content = scraped_data['markdown']

            if markdown_content:
                console.print(f"[green]✔[/green] Scraped [bold]{len(markdown_content)}[/bold] characters.")
                return markdown_content
            else:
                console.print(f"[bold red]Error:[/bold red] No markdown found. Response type: {type(scraped_data)}")
                return None
        except Exception as e:
            console.print(f"[bold red]FireCrawl Error:[/bold red] {e}")
            return None

async def analyze_with_gemini(content: str):
    with Live(Spinner("dots", text="[magenta]Analyzing content with Gemini..."), refresh_per_second=10, transient=True):
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            prompt = """
            You are a GTM strategy analyst. Analyze the following content and provide a condensed summary:
            1. **Core Product/Service**
            2. **Target Audience**
            3. **Key Features** (top 3-5)
            4. **Pricing Model** (Subscription, Free Tier, etc. - include numbers if found)
            5. **Value Proposition**

            Content:
            ---
            {website_content}
            ---
            """
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt.format(website_content=content)
            )
            return response.text
        except Exception as e:
            console.print(f"[bold red]Gemini Error:[/bold red] {e}")
            return None

async def main():
    console.rule("[bold cyan]Riva GTM Scraper (FireCrawl Edition)[/bold cyan]")
    
    if not check_api_keys():
        return

    url = console.input("🔗 [bold]URL to analyze:[/bold] ")
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    markdown_content = await scrape_website(url)
    
    if markdown_content:
        gtm_summary = await analyze_with_gemini(markdown_content)
        if gtm_summary:
            console.rule("[bold green]GTM Analysis Report[/bold green]")
            console.print(Markdown(gtm_summary))
            console.rule("[bold green]End of Report[/bold green]")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[bold yellow]Stopped by user.[/bold yellow]")

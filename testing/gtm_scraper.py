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

# --- Configuration ---
load_dotenv()
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Initialize Rich Console for better UI ---
console = Console()

def check_api_keys():
    """Checks if the required API keys are present in the .env file."""
    if not FIRECRAWL_API_KEY or not GEMINI_API_KEY:
        console.print(
            Panel(
                "[bold red]API Key Error:[/bold red] Make sure your `.env` file exists in the project root and contains both `FIRECRAWL_API_KEY` and `GEMINI_API_KEY`.",
                title="Error",
                border_style="red"
            )
        )
        return False
    return True

async def scrape_website(url: str):
    """Uses Firecrawl to scrape the website and returns the markdown content."""
    with Live(Spinner("dots", text="[cyan]Scraping website with FireCrawl..."), refresh_per_second=10, transient=True):
        try:
            app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
            
            # Using loop.run_in_executor for the synchronous scrape call
            loop = asyncio.get_event_loop()
            
            # The v2 SDK returns a Document object, not a dict
            scraped_data = await loop.run_in_executor(None, lambda: app.scrape(url))
            
            # Check if it's a Document object (v2) or dict (v1)
            markdown_content = None
            if hasattr(scraped_data, 'markdown'):
                markdown_content = scraped_data.markdown
            elif isinstance(scraped_data, dict) and 'markdown' in scraped_data:
                markdown_content = scraped_data['markdown']

            if markdown_content:
                console.print(f"[green]✔[/green] Scraped [bold]{len(markdown_content)}[/bold] characters of markdown content.")
                return markdown_content
            else:
                console.print(f"[bold red]Error:[/bold red] Markdown content not found in response. Response type: {type(scraped_data)}")
                return None
        except Exception as e:
            console.print(f"[bold red]FireCrawl Error:[/bold red] {e}")
            return None

async def analyze_with_gemini(content: str):
    """Uses Gemini to analyze the scraped content and generate a GTM summary."""
    with Live(Spinner("dots", text="[magenta]Analyzing content with Gemini..."), refresh_per_second=10, transient=True):
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            prompt = """
            You are a Go-To-Market (GTM) strategy analyst. Your task is to analyze the following website content and extract key information relevant for a competitive analysis. 
            
            Based on the text provided, please provide a condensed summary covering the following points. If a point is not mentioned, state 'Not Found'.
            
            1.  **Core Product/Service:** What is the main offering?
            2.  **Target Audience:** Who is this product for? (e.g., developers, enterprises, small businesses)
            3.  **Key Features:** List the top 3-5 most prominent features mentioned.
            4.  **Pricing Model:** How do they charge for their service? (e.g., Subscription, Usage-based, Free Tier, Enterprise-only). Provide specific numbers if available.
            5.  **Value Proposition:** What is their primary selling point or marketing angle?
            
            Here is the website content:
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
    """Main function to run the GTM scraper."""
    console.rule("[bold cyan]Riva GTM Scraper Test[/bold cyan]")
    
    if not check_api_keys():
        return

    url = console.input("🔗 [bold]Please paste the URL to analyze:[/bold] ")

    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    console.print(f"\n[cyan]Starting analysis for:[/cyan] {url}\n")
    
    markdown_content = await scrape_website(url)
    
    if markdown_content:
        gtm_summary = await analyze_with_gemini(markdown_content)
        if gtm_summary:
            console.rule("[bold green]Go-To-Market Analysis[/bold green]")
            console.print(Markdown(gtm_summary))
            console.rule("[bold green]End of Report[/bold green]")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[bold yellow]Analysis stopped by user.[/bold yellow]")

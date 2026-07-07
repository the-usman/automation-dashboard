"""
Scrape hh.kz vacancies — ALL results from the last 24 hours (paginated).

``scrape_vacancies()`` is the reusable, importable core (used by the
``/jobs/scrape-hh`` API). Running this file directly keeps the original
CLI behavior: scrape, print a summary, write ``hh_vacancies.json``, upload to
Google Sheets.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Awaitable, Callable

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from undetected_playwright import Malenia

SERVICE_ACCOUNT_FILE = "service_account.json"
SPREADSHEET_ID = "1NYFu5hhAmQDDaxelNEZGS2CkW6DsjG32cYHq3oRD2Mk"
SHEET_HEADER = ["Name", "Type", "Salary", "Employer", "Address", "URL"]

SEARCH_URL = (
    "https://hh.ru/search/vacancy?"
    "text=intern+OR+%D1%81%D1%82%D0%B0%D0%B6%D0%B8%D1%80%D0%BE%D0%B2%D0%BA%D0%B0+OR+%D1%81%D1%82%D0%B0%D0%B6%D0%B5%D1%80"
    "+OR+trainee+OR+junior+OR+%D0%BC%D0%BB%D0%B0%D0%B4%D1%88%D0%B8%D0%B9"
    "+OR+%D0%BD%D0%B0%D1%87%D0%B8%D0%BD%D0%B0%D1%8E%D1%89%D0%B8%D0%B9"
    "&area=40&search_field=name"
    "&order_by=publication_time&items_on_page=100"
    "&period=1"
)

INTERN_KEYWORDS = re.compile(
    r'\b(?:intern|trainee|стаж[иё]р|стажировк|практик)\b', re.IGNORECASE
)

JUNIOR_KEYWORDS = re.compile(
    r'\b(?:junior|младш|начинающ|entry.level)\b', re.IGNORECASE
)


def strip_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", "\"")
    text = text.replace("&#39;", "'")
    return text.strip()


ALLOWED_CITIES = ["Алматы", "Астана", "Нур-Султан"]

def classify_role(name):
    if INTERN_KEYWORDS.search(name):
        return "internship"
    if JUNIOR_KEYWORDS.search(name):
        return "junior"
    return "full-time"

def extract_city(address):
    """Return the recognized city name, or empty string."""
    for city in ALLOWED_CITIES:
        if city in address:
            return city
    return ""

def is_allowed_city(address):
    return any(c in address for c in ALLOWED_CITIES)


def parse_page(html):
    names = re.findall(
        r'data-qa="serp-item__title-text"[^>]*>\s*(.*?)\s*</span', html, re.DOTALL
    )
    salaries = re.findall(
        r'compensation-labels--[^>]*>.*?<span[^>]*>(.*?)</span>', html, re.DOTALL
    )
    employers = re.findall(
        r'data-qa="vacancy-serp__vacancy-employer"[^>]*>(.*?)</(?:a|div|span)',
        html,
        re.DOTALL,
    )
    addresses = re.findall(
        r'data-qa="vacancy-serp__vacancy-address"[^>]*>(.*?)</(?:span|div)',
        html,
        re.DOTALL,
    )
    links = re.findall(r'serp-item__title[^>]*href="(https?://[^"]+)"', html)

    results = []
    for i in range(len(names)):
        name = strip_html(names[i])
        results.append({
            "name": name,
            "type": classify_role(name),
            "salary": strip_html(salaries[i]) if i < len(salaries) else "",
            "employer": strip_html(employers[i]) if i < len(employers) else "",
            "address": strip_html(addresses[i]) if i < len(addresses) else "",
            "url": links[i] if i < len(links) else "",
        })
    return results


def get_max_page(html):
    """Find the last page number from pagination."""
    pages = re.findall(r'data-qa="pager-page"[^>]*>(\d+)</a>', html)
    if pages:
        return max(int(p) for p in pages)
    return 1


def upload_to_google_sheets(results):
    """Write results to a Google Sheet tab named after today's date (YYYY-MM-DD)."""
    with open(SERVICE_ACCOUNT_FILE, encoding="utf-8") as f:
        key_data = json.load(f)
    if key_data.get("type") != "service_account":
        raise SystemExit(
            f"{SERVICE_ACCOUNT_FILE} is not a service account key "
            f"(found keys: {list(key_data.keys())}). "
            "Google Cloud Console -> IAM & Admin -> Service Accounts -> "
            "your service account -> Keys tab -> Add Key -> Create new key -> JSON."
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    sheet_name = datetime.now().strftime("%Y-%m-%d")
    try:
        worksheet = sh.worksheet(sheet_name)
        worksheet.clear()
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(
            title=sheet_name, rows=str(len(results) + 10), cols=str(len(SHEET_HEADER))
        )

    rows = [SHEET_HEADER] + [
        [v["name"], v["type"], v["salary"], v["employer"], v["address"], v["url"]]
        for v in results
    ]
    worksheet.update(rows)
    print(f"Uploaded {len(results)} vacancies to Google Sheet tab '{sheet_name}'")


async def scrape_vacancies(
    search_url: str = SEARCH_URL,
    on_progress: Callable[[dict], Awaitable[None]] | None = None,
) -> list[dict]:
    """Scrape every hh.ru vacancy page for ``search_url``, deduped and city-filtered.

    ``on_progress``, if given, is awaited after each page with
    ``{"page", "total_pages", "found_so_far"}`` — used by the background API job
    to report progress without duplicating the pagination loop.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ru-RU")
        await Malenia.apply_stealth(ctx)
        page = await ctx.new_page()

        all_results = []
        seen_urls = set()
        page_num = 0
        total_pages = None

        while True:
            url = search_url + f"&page={page_num}"
            await page.goto(url, wait_until="load", timeout=60000)
            await asyncio.sleep(5)

            html = await page.content()

            if page_num == 0:
                total_pages = get_max_page(html)

            results = parse_page(html)

            for v in results:
                if v["url"] and v["url"] not in seen_urls and is_allowed_city(v["address"]):
                    seen_urls.add(v["url"])
                    all_results.append(v)

            page_num += 1

            if on_progress:
                await on_progress({
                    "page": page_num,
                    "total_pages": total_pages,
                    "found_so_far": len(all_results),
                })

            if total_pages and page_num >= total_pages:
                break

            # Safety valve — stop if we got an empty page
            if len(results) == 0:
                break

        await browser.close()

    all_results.sort(key=lambda v: v["name"])
    return all_results


async def _print_progress(info: dict) -> None:
    print(f"Scraped page {info['page']}/{info['total_pages']} — {info['found_so_far']} found so far")


async def scrape():
    all_results = await scrape_vacancies(SEARCH_URL, on_progress=_print_progress)

    internships = [v for v in all_results if v["type"] == "internship"]
    juniors = [v for v in all_results if v["type"] == "junior"]
    fulltime = [v for v in all_results if v["type"] == "full-time"]

    print(f"\n=== {len(all_results)} vacancies from the last 24 hours ===")
    print(f"   Internships: {len(internships)}")
    print(f"   Junior: {len(juniors)}")
    print(f"   Full-time (entry-level): {len(fulltime)}\n")

    json.dump(
        all_results,
        open("hh_vacancies.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    print(f"Saved {len(all_results)} vacancies to hh_vacancies.json")

    upload_to_google_sheets(all_results)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(scrape())

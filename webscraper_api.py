import os
import time
import base64
import shutil
import uuid
import io
import logging
import requests
from io import BytesIO
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse, StreamingResponse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
from pypdf import PdfWriter
from fastapi.middleware.cors import CORSMiddleware
import threading
import gc
from rembg import remove
from PIL import Image


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
os.makedirs("sessions", exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health():
    return {"message": "Backend is running"}

chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-software-rasterizer")
chrome_options.add_argument("--blink-settings=imagesEnabled=false")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--disable-infobars")
chrome_options.add_argument("--disable-notifications")

@app.post("/scrape")
async def scrape_website(url: str = Form(...), threads: int = Form(5)):
    parsed_url = urlparse(url)
    if not parsed_url.scheme.startswith("http"):
        return JSONResponse(content={"error": "Invalid URL scheme"}, status_code=400)

    session_id = str(uuid.uuid4())
    output_folder = os.path.join("sessions", session_id)
    os.makedirs(output_folder, exist_ok=True)
    output_pdf_path = os.path.join(output_folder, "scraped_output.pdf")

    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = {
            urljoin(url, a['href'])
            for a in soup.find_all('a', href=True)
            if not a['href'].startswith(('mailto:', 'javascript:')) and (
            a['href'].startswith(('/', './')) or url in urljoin(url, a['href'])
            )
        }

        links = {link for link in links if link.endswith('.html') or link.rstrip('/').count('.') <= 1}
    except Exception as e:
        logger.exception("Failed to fetch base URL")
        return JSONResponse(content={"error": f"Failed to fetch base URL: {str(e)}"}, status_code=400)

    links = sorted(links)
    if not links:
        return JSONResponse(content={"error": "No valid links found on the page."}, status_code=404)

    def process_page(index, link):
        driver = None
        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.get(link)
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "transferMode": "ReturnAsBase64",
                "preferCSSPageSize": True,
                "scale": 1.0
            })

            return BytesIO(base64.b64decode(pdf_data['data']))
        except Exception as e:
            logger.error(f"Error processing {link}: {e}")
            return None
        finally:
            if driver:
                driver.quit()

    pdf_buffers = []
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(process_page, i + 1, link) for i, link in enumerate(links)]
        for future in futures:
            result = future.result()
            if result:
                pdf_buffers.append(result)

    if not pdf_buffers:
        return JSONResponse(content={"error": "No valid PDFs were generated."}, status_code=500)

    try:
        writer = PdfWriter()
        for buffer in pdf_buffers:
            writer.append(buffer)
        with open(output_pdf_path, 'wb') as f:
            writer.write(f)
        writer.close()
    except Exception as e:
        logger.exception("Failed to merge PDFs")
        return JSONResponse(content={"error": f"Failed to merge PDFs: {e}"}, status_code=500)

    # async cleanup thread
    def delayed_cleanup():
        time.sleep(10)  # wait 10 seconds before cleanup
        try:
            shutil.rmtree(output_folder)
            logger.info(f"✅ Auto-cleaned session: {session_id}")
        except Exception as e:
            logger.warning(f"⚠️ Auto-cleanup failed: {e}")

    threading.Thread(target=delayed_cleanup, daemon=True).start()
    gc.collect()

    return StreamingResponse(
        open(output_pdf_path, "rb"),
        media_type="application/pdf",
        headers={
        "Content-Disposition": "attachment; filename=scraped_output.pdf",
        "x-page-count": str(len(pdf_buffers))  # 👈 this line enables frontend count
    }
    )

from fastapi import UploadFile, File
from fastapi.responses import FileResponse

@app.post("/merge")
async def merge_pdfs(files: list[UploadFile] = File(...)):
    session_id = str(uuid.uuid4())
    folder = f"sessions/{session_id}"
    os.makedirs(folder, exist_ok=True)

    pdf_paths = []

    for i, file in enumerate(files):
        path = os.path.join(folder, f"{i + 1}.pdf")
        with open(path, "wb") as f:
            f.write(await file.read())
        pdf_paths.append(path)

    output_path = os.path.join(folder, "merged.pdf")
    writer = PdfWriter()

    for pdf in pdf_paths:
        writer.append(pdf)
    writer.write(output_path)
    writer.close()

    return FileResponse(output_path, media_type="application/pdf", filename="merged.pdf")

@app.post("/remove-bg")
async def remove_background(file: UploadFile = File(...)):
    input_bytes = await file.read()
    result = remove(input_bytes)
    img = Image.open(io.BytesIO(result)).convert("RGBA")

    session_id = str(uuid.uuid4())
    output_path = f"sessions/{session_id}.png"
    os.makedirs("sessions", exist_ok=True)
    img.save(output_path)

    return FileResponse(output_path, media_type="image/png", filename="output.png")
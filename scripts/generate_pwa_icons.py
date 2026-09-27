from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parent.parent
out=root/'static/icons';out.mkdir(exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=b.new_page(viewport={'width':512,'height':512},device_scale_factor=1)
 for name,size in [('icon-192',192),('icon-512',512),('icon-maskable-512',512),('apple-touch-icon',180)]:
  page.set_viewport_size({'width':size,'height':size})
  page.set_content(f'''<style>@font-face{{font-family:brand;src:url(data:font/woff2;base64,{__import__('base64').b64encode((root/'static/fonts/figtree-latin.woff2').read_bytes()).decode()})}}body{{margin:0;background:#F4F4F6;display:flex;align-items:center;justify-content:center;height:100vh}}div{{font:800 {size*.16}px brand;color:#18181B;letter-spacing:-{size*.006}px}}span{{display:inline-block;background:#6D4AE8;border-radius:50%;width:{size*.04}px;height:{size*.04}px;margin-left:{size*.012}px}}</style><div>nndrei<span></span></div>''')
  page.evaluate('document.fonts.ready')
  page.screenshot(path=str(out/f'{name}.png'))
 b.close()

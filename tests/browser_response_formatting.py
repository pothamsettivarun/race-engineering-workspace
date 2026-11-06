"""Check Markdown rendering in the running local workspace, without changing runs."""
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1500, 'height': 1050})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    page.wait_for_selector('#workspace:visible')
    page.click('[data-tab=engineer]')
    page.wait_for_selector('.answer-text h3')
    assert page.locator('.answer-text strong').count() > 0
    assert page.locator('.answer-text table th').count() == 3
    assert page.locator('.answer-text table tbody tr').count() >= 5
    assert page.locator('.answer-text ul li').count() > 0
    assert '## Baseline assessment' not in page.locator('.answer-text').inner_text()
    page.evaluate(r'''() => {
      const probe=document.createElement('div');probe.id='markdown-probe';
      probe.innerHTML=renderMarkdown('# Test\n\n**bold** and *italic* and `code`\n\n1. First\n2. Second\n\n- Outer\n  - Inner\n\n[Manual](https://example.com/manual)\n\n[Bad](javascript:alert(1))\n\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n\n```\n<b>code</b>\n```');
      document.body.appendChild(probe);
    }''')
    probe = page.locator('#markdown-probe')
    assert probe.locator('h2').inner_text() == 'Test'
    assert probe.locator('strong').inner_text() == 'bold'
    assert probe.locator('em').inner_text() == 'italic'
    assert probe.locator('ol li').count() == 2
    assert probe.locator('ul ul li').count() == 1
    assert probe.locator('a').count() == 1
    assert probe.locator('a').get_attribute('rel') == 'noopener noreferrer'
    assert probe.locator('script,img').count() == 0
    assert '<b>code</b>' == probe.locator('pre code').inner_text()
    page.evaluate("document.getElementById('markdown-probe').remove()")
    output = Path(__file__).resolve().parents[1] / 'test-results'
    output.mkdir(exist_ok=True)
    page.locator('.answer').first.scroll_into_view_if_needed()
    page.screenshot(path=str(output / 'formatted-response.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('.answer').first.scroll_into_view_if_needed()
    page.screenshot(path=str(output / 'formatted-response-mobile.png'))
    assert not errors, errors
    browser.close()
    print('Passed: saved response headings, emphasis, lists, table, safe links, nested lists, escaped HTML, code blocks, mobile layout; no JavaScript errors.')

from __future__ import annotations

from typing import Any

from playwright.async_api import Error as PlaywrightError

from app.browser.session import Session

UNREACHABLE_JS = """
() => {
  const INTERACTIVE_ROLES = new Set([
    'button', 'link', 'checkbox', 'radio', 'textbox', 'combobox', 'slider', 'tab', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'option', 'switch', 'searchbox', 'spinbutton', 'gridcell',
  ]);
  const NATIVE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY']);

  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility !== 'visible') return false;
    return el.getClientRects().length > 0;
  };

  const whyInteractive = (el) => {
    if (el.tagName === 'LABEL' && el.control && !visible(el.control)) {
      return 'label for hidden ' + (el.control.type || el.control.tagName.toLowerCase());
    }
    if (NATIVE.has(el.tagName)) {
      if (el.tagName === 'A' && !el.hasAttribute('href')) return null;
      if (el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'hidden') return null;
      return 'native ' + el.tagName.toLowerCase();
    }
    const role = (el.getAttribute('role') || '').trim().split(' ')[0];
    if (INTERACTIVE_ROLES.has(role)) return 'role=' + role;
    if (el.hasAttribute('onclick')) return 'onclick';
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) >= 0) return 'tabindex=' + tabindex;
    const cursor = getComputedStyle(el).cursor;
    const parent = el.parentElement;
    if (cursor === 'pointer' && (!parent || getComputedStyle(parent).cursor !== 'pointer')) {
      return 'cursor:pointer';
    }
    return null;
  };

  const hiddenAncestor = (el) => {
    for (let node = el; node; node = node.parentElement) {
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') {
        return node.tagName.toLowerCase() + '[aria-hidden]';
      }
    }
    return null;
  };

  const covered = (el) => {
    for (let node = el; node; node = node.parentElement) {
      if (node.hasAttribute && node.hasAttribute('data-cb-ref')) return true;
    }
    return false;
  };

  const out = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot);
      if (!visible(el)) continue;
      const why = whyInteractive(el);
      if (!why || covered(el)) continue;
      out.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role'),
        why,
        text: (el.innerText || el.getAttribute('aria-label') || el.value || '')
          .replace(/\\s+/g, ' ').trim().slice(0, 60),
        hidden_by: hiddenAncestor(el),
      });
    }
  };
  walk(document);
  return out;
}
"""


async def find_unreachable(session: Session) -> list[dict[str, Any]]:
    """Visible, interactive-looking elements in the active tab that the snapshot gave no ref."""
    await session.snapshot(mark_new=False)
    page = await session.page()
    try:
        return list(await page.evaluate(UNREACHABLE_JS))
    except PlaywrightError:
        return []

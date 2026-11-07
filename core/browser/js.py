"""JavaScript injected into the live page.

These snippets are written as self-contained strings so they can be evaluated by
Playwright exactly as-is, and are syntax-checked by the test suite with Node.

`PICKER_JS` is a real element picker: hover highlights the element, a click
resolves a stable CSS path, works out the repeated row that contains it, and
stores the result on `window.__b2bPicker.result`. It is retained for
Playwright-side selection experiments and as a reference.

The operator-facing visual picker is now a Streamlit custom component in
`ui/visual_picker/`; it implements the component protocol directly and returns a
position path to Python. See `docs/BROWSER_ENGINE.md` §7.
"""
from __future__ import annotations

PICKER_JS = r"""
(() => {
  if (window.__b2bPicker && window.__b2bPicker.installed) {
    window.__b2bPicker.active = true;
    return true;
  }

  const state = { installed: true, active: true, result: null, hovered: null };
  window.__b2bPicker = state;

  const style = document.createElement('style');
  style.id = '__b2b_picker_style';
  style.textContent = `
    .__b2b_highlight {
      outline: 3px solid #7c3aed !important;
      outline-offset: 1px !important;
      background-color: rgba(124, 58, 237, 0.08) !important;
      cursor: crosshair !important;
    }
    .__b2b_group {
      outline: 2px dashed #10b981 !important;
      outline-offset: 2px !important;
    }
  `;
  document.head.appendChild(style);

  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'HTML', 'HEAD', 'BODY']);

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      const cls = Array.from(node.classList || []).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.map(c => CSS.escape(c)).join('.');
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
      if (parts.length >= 6) break;
    }
    return parts.join(' > ');
  }

  function shapeOf(el) {
    if (!el || el.nodeType !== 1) return '';
    return el.tagName + '|' + Array.from(el.classList || []).sort().join('.');
  }

  // Walk up while the parent holds several siblings of the same shape: that is
  // the repeated listing row the clicked element belongs to.
  function findRowGroup(el) {
    let node = el;
    let best = null;
    for (let depth = 0; depth < 6 && node && node.parentElement; depth++) {
      const parent = node.parentElement;
      const shape = shapeOf(node);
      const peers = Array.from(parent.children).filter(c => shapeOf(c) === shape);
      if (peers.length >= 2) {
        best = { node: node, parent: parent, peers: peers };
      }
      node = parent;
    }
    return best;
  }

  function clearHighlight() {
    document.querySelectorAll('.__b2b_highlight, .__b2b_group').forEach(el => {
      el.classList.remove('__b2b_highlight', '__b2b_group');
    });
  }

  function onMove(event) {
    if (!state.active) return;
    const target = event.target;
    if (!target || target.nodeType !== 1 || SKIP.has(target.tagName)) return;
    if (target === state.hovered) return;
    clearHighlight();
    state.hovered = target;
    target.classList.add('__b2b_highlight');
    const group = findRowGroup(target);
    if (group) group.parent.classList.add('__b2b_group');
  }

  function onClick(event) {
    if (!state.active) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.target;
    if (!target || target.nodeType !== 1) return;

    const group = findRowGroup(target);
    let groupSelector = '';
    let groupCount = 0;
    let rowSelector = '';
    if (group) {
      groupSelector = cssPath(group.parent);
      groupCount = group.peers.length;
      rowSelector = cssPath(group.node) + ':nth-of-type(1)';
    }

    state.result = {
      selector: cssPath(target),
      groupSelector: groupSelector,
      groupCount: groupCount,
      rowSelector: rowSelector,
      text: (target.innerText || '').trim().slice(0, 200),
      tag: target.tagName.toLowerCase()
    };
    state.active = false;
    clearHighlight();
    return false;
  }

  document.addEventListener('mousemove', onMove, true);
  document.addEventListener('click', onClick, true);
  return true;
})()
"""


READ_PICKER_JS = "window.__b2bPicker && window.__b2bPicker.result ? window.__b2bPicker.result : null"

STOP_PICKER_JS = """
(() => {
  if (window.__b2bPicker) { window.__b2bPicker.active = false; }
  document.querySelectorAll('.__b2b_highlight, .__b2b_group').forEach(el => {
    el.classList.remove('__b2b_highlight', '__b2b_group');
  });
  return true;
})()
"""


SCROLL_BOTTOM_JS = """
(() => {
  const doc = document.scrollingElement || document.documentElement;
  window.scrollTo(0, doc.scrollHeight);
  return { height: doc.scrollHeight, y: window.scrollY };
})()
"""


SCROLL_TOP_JS = "(() => { window.scrollTo(0, 0); return true; })()"


PAGE_METRICS_JS = """
(() => {
  const doc = document.scrollingElement || document.documentElement;
  return {
    height: doc.scrollHeight,
    viewport: window.innerHeight,
    y: window.scrollY,
    title: document.title || ''
  };
})()
"""


# Clicks the most common cookie/consent controls. Best effort only: a failure is
# never fatal, and nothing is clicked that could be an action other than dismissal.
DISMISS_OVERLAYS_JS = r"""
(() => {
  const patterns = [
    /^accept all$/i, /^accept cookies$/i, /^accept$/i, /^i agree$/i,
    /^got it$/i, /^ok$/i, /^close$/i, /^dismiss$/i, /^no thanks$/i
  ];
  const candidates = Array.from(document.querySelectorAll(
    'button, [role="button"], a.button, .cookie-accept, #onetrust-accept-btn-handler'
  ));
  let clicked = 0;
  for (const el of candidates) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!text || text.length > 40) continue;
    if (patterns.some(p => p.test(text))) {
      try {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        el.click();
        clicked += 1;
        if (clicked >= 3) break;
      } catch (e) { /* ignore */ }
    }
  }
  return clicked;
})()
"""


# Returns a signature of the visible row texts, used to decide whether a click or
# scroll actually changed the content.
CONTENT_SIGNATURE_JS = r"""
(selector) => {
  let rows = [];
  try {
    rows = Array.from(document.querySelectorAll(selector));
  } catch (e) {
    return '';
  }
  const texts = rows.slice(0, 12).map(el => (el.innerText || '').trim().slice(0, 120));
  return rows.length + '|' + texts.join('||');
}
"""


# Click ordinary "show phone / show email / contact details" controls. This is
# not a bypass: it only operates controls a visitor can operate, and never fills a
# form, accepts terms, or opens a login wall. Returns the number clicked.
REVEAL_CONTACT_JS = r"""
(() => {
  const patterns = [
    /^show (phone|number|email|contact|details)$/i,
    /^view (phone|number|email|contact|details|profile)$/i,
    /^contact (agent|details|info)$/i,
    /^agent (details|contact)$/i,
    /^reveal (phone|email|contact)$/i,
    /^see (phone|email|contact)$/i,
    /^more (details|info)$/i
  ];
  const candidates = Array.from(document.querySelectorAll(
    'button, [role="button"], a[href="#"], a[href^="javascript:"], summary, .accordion-title'
  ));
  let clicked = 0;
  for (const el of candidates) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!text || text.length > 36) continue;
    if (!patterns.some(p => p.test(text))) continue;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      el.click();
      clicked += 1;
      if (clicked >= 4) break;
    } catch (e) { /* ignore */ }
  }
  return clicked;
})()
"""


COUNT_ROWS_JS = r"""
(selector) => {
  try { return document.querySelectorAll(selector).length; } catch (e) { return 0; }
}
"""


ALL_JS_SNIPPETS = {
    "PICKER_JS": PICKER_JS,
    "READ_PICKER_JS": READ_PICKER_JS,
    "STOP_PICKER_JS": STOP_PICKER_JS,
    "SCROLL_BOTTOM_JS": SCROLL_BOTTOM_JS,
    "SCROLL_TOP_JS": SCROLL_TOP_JS,
    "PAGE_METRICS_JS": PAGE_METRICS_JS,
    "DISMISS_OVERLAYS_JS": DISMISS_OVERLAYS_JS,
    "CONTENT_SIGNATURE_JS": CONTENT_SIGNATURE_JS,
    "REVEAL_CONTACT_JS": REVEAL_CONTACT_JS,
    "COUNT_ROWS_JS": COUNT_ROWS_JS,
}

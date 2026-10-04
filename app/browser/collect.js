(args) => {
  const startIndex = args.start;
  const sameDocument = document.__cbCollected === true;
  document.__cbCollected = true;
  const previous = sameDocument ? args.previous || {} : {};
  const MAX_NAME = 120;
  const MAX_TEXT = 300;
  const MAX_NODES = 4000;

  let refCounter = startIndex;
  let frameCounter = 0;
  let nodeCount = 0;
  const usedRefs = new Set();

  const healedRef = (signature) => {
    for (const candidate of previous[signature] || []) {
      if (usedRefs.has(candidate)) continue;
      if (document.querySelector(`[data-cb-ref="${candidate}"]`)) continue;
      return candidate;
    }
    return null;
  };

  const INTERACTIVE_ROLES = new Set([
    'button', 'link', 'checkbox', 'radio', 'textbox', 'combobox', 'slider',
    'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'switch',
    'searchbox', 'spinbutton',
  ]);

  const LANDMARK_TAGS = {
    NAV: 'navigation', MAIN: 'main', HEADER: 'banner', FOOTER: 'contentinfo',
    ASIDE: 'complementary', FORM: 'form', SECTION: 'section', ARTICLE: 'article',
    DIALOG: 'dialog', UL: 'list', OL: 'list', LI: 'listitem', TABLE: 'table',
    TR: 'row', TD: 'cell', TH: 'columnheader', FIGURE: 'figure', DETAILS: 'group',
    SUMMARY: 'button', FIELDSET: 'group', LEGEND: 'legend',
  };

  const TEXT_NAMED_ROLES = new Set([
    'link', 'button', 'heading', 'option', 'tab', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'switch', 'cell', 'columnheader', 'legend', 'summary',
  ]);

  const TEXT_INPUT_TYPES = new Set([
    'text', 'email', 'password', 'search', 'tel', 'url', 'number', 'date',
    'datetime-local', 'month', 'week', 'time', '',
  ]);

  const collapse = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  const visibilityOf = (el) => {
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    if (!style) return 'show';
    if (style.display === 'none') return 'prune';
    if (el.getAttribute('aria-hidden') === 'true') return 'prune';
    if (style.display === 'contents') return 'skip';
    if (style.visibility === 'hidden' || style.visibility === 'collapse') return 'skip';
    if (el.tagName !== 'OPTION' && el.getClientRects().length === 0) return 'skip';
    return 'show';
  };

  const childElementsOf = (el) => {
    if (el.tagName === 'SLOT') {
      const assigned = el.assignedElements ? el.assignedElements({ flatten: true }) : [];
      return assigned.length ? assigned : Array.from(el.children);
    }
    if (el.shadowRoot) return Array.from(el.shadowRoot.children);
    return Array.from(el.children);
  };

  const visitChildren = (el) => {
    const out = [];
    for (const child of childElementsOf(el)) out.push(...visit(child));
    return out;
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return collapse(explicit).split(' ')[0];
    const tag = el.tagName;
    if (tag === 'A') return el.hasAttribute('href') ? 'link' : 'generic';
    if (tag === 'BUTTON') return 'button';
    if (tag === 'SELECT') return el.multiple ? 'listbox' : 'combobox';
    if (tag === 'TEXTAREA') return 'textbox';
    if (tag === 'IFRAME' || tag === 'FRAME') return 'iframe';
    if (tag === 'IMG') return 'img';
    if (tag === 'INPUT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button' || type === 'reset' || type === 'image') {
        return 'button';
      }
      if (type === 'range') return 'slider';
      if (type === 'file') return 'button';
      if (type === 'hidden') return 'none';
      if (type === 'search') return 'searchbox';
      if (TEXT_INPUT_TYPES.has(type)) return 'textbox';
      return 'textbox';
    }
    if (/^H[1-6]$/.test(tag)) return 'heading';
    if (LANDMARK_TAGS[tag]) return LANDMARK_TAGS[tag];
    if (el.hasAttribute('onclick') || el.getAttribute('tabindex') === '0') return 'button';
    return 'generic';
  };

  const ownText = (el) => {
    let out = '';
    for (const node of el.childNodes) {
      if (node.nodeType === 3) out += node.nodeValue;
    }
    return collapse(out);
  };

  const labelFor = (el) => {
    const doc = el.ownerDocument;
    if (el.id) {
      const lbl = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return collapse(lbl.textContent);
    }
    const parent = el.closest('label');
    if (parent) return collapse(parent.textContent);
    return '';
  };

  const nameOf = (el, role) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clip(collapse(aria), MAX_NAME);
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map((id) => {
        const target = el.ownerDocument.getElementById(id);
        return target ? collapse(target.textContent) : '';
      });
      const joined = collapse(parts.join(' '));
      if (joined) return clip(joined, MAX_NAME);
    }
    if (role === 'textbox' || role === 'searchbox' || role === 'combobox' ||
        role === 'checkbox' || role === 'radio' || role === 'listbox' || role === 'slider') {
      const lbl = labelFor(el);
      if (lbl) return clip(lbl, MAX_NAME);
      const placeholder = el.getAttribute('placeholder');
      if (placeholder) return clip(collapse(placeholder), MAX_NAME);
      const nameAttr = el.getAttribute('name');
      if (nameAttr) return clip(collapse(nameAttr), MAX_NAME);
    }
    if (el.tagName === 'INPUT') {
      const value = el.getAttribute('value');
      if (value && role === 'button') return clip(collapse(value), MAX_NAME);
    }
    if (role === 'img') {
      const alt = el.getAttribute('alt');
      if (alt !== null) return clip(collapse(alt), MAX_NAME);
    }
    if (role === 'iframe') {
      const title = el.getAttribute('title') || el.getAttribute('name') || '';
      return clip(collapse(title), MAX_NAME);
    }
    const title = el.getAttribute('title');
    if (TEXT_NAMED_ROLES.has(role)) {
      const text = collapse(el.textContent);
      if (text) return clip(text, MAX_NAME);
    }
    if (title) return clip(collapse(title), MAX_NAME);
    return '';
  };

  const statesOf = (el, role) => {
    const states = [];
    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') {
      states.push('disabled');
    }
    if (role === 'checkbox' || role === 'radio' || role === 'switch') {
      const checked = el.checked === true || el.getAttribute('aria-checked') === 'true';
      states.push(checked ? 'checked' : 'unchecked');
    }
    const expanded = el.getAttribute('aria-expanded');
    if (expanded) states.push(`expanded=${expanded}`);
    const selected = el.getAttribute('aria-selected');
    if (selected === 'true') states.push('selected');
    if (el.required === true) states.push('required');
    if (el.readOnly === true) states.push('readonly');
    return states;
  };

  const VALUELESS_INPUTS = new Set(['checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file']);

  const valueOf = (el) => {
    if (el.tagName === 'SELECT') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      return opt ? clip(collapse(opt.textContent || opt.value), MAX_NAME) : '';
    }
    if (el.tagName === 'TEXTAREA') return clip(collapse(el.value || ''), MAX_NAME);
    if (el.tagName !== 'INPUT') return '';
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (VALUELESS_INPUTS.has(type) || !el.value) return '';
    if (type === 'password') return '\u2022'.repeat(Math.min(el.value.length, 8));
    return clip(collapse(el.value), MAX_NAME);
  };

  const assignRef = (el, role, name) => {
    let ref = el.getAttribute('data-cb-ref');
    if (!ref || usedRefs.has(ref)) {
      ref = healedRef(`${role}|${name}`) || `e${refCounter++}`;
      el.setAttribute('data-cb-ref', ref);
    }
    usedRefs.add(ref);
    return ref;
  };

  const descriptionOf = (el, name) => {
    const source = el.getAttribute('aria-description') || el.getAttribute('title');
    const text = collapse(source || '');
    if (!text || text === name || name.includes(text)) return '';
    return clip(text, MAX_NAME);
  };

  const labelStandingInForControl = (el) => {
    if (el.tagName !== 'LABEL') return null;
    const control = el.control;
    if (!control || control.tagName !== 'INPUT') return null;
    if ((control.getAttribute('type') || '').toLowerCase() === 'hidden') return null;
    if (visibilityOf(control) === 'show') return null;
    const role = roleOf(control);
    if (!INTERACTIVE_ROLES.has(role)) return null;
    nodeCount += 1;
    const name = clip(collapse(el.textContent), MAX_NAME);
    const node = { role, name, children: [] };
    node.ref = assignRef(el, role, name);
    const states = statesOf(control, role);
    if (states.length) node.states = states;
    return node;
  };

  const visit = (el) => {
    if (nodeCount >= MAX_NODES) return [];
    const tag = el.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TEMPLATE') {
      return [];
    }
    const visibility = visibilityOf(el);
    if (visibility === 'prune') return [];
    if (visibility === 'skip') return visitChildren(el);

    if (tag === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'hidden') return [];
    const proxied = labelStandingInForControl(el);
    if (proxied) return [proxied];
    let role = roleOf(el);
    if (role === 'none' || role === 'presentation') role = 'generic';
    const interactive = INTERACTIVE_ROLES.has(role);

    const node = { role, children: [] };
    nodeCount += 1;

    if (role === 'iframe') {
      const key = `f${frameCounter++}`;
      el.setAttribute('data-cb-frame', key);
      node.frameKey = key;
    }

    node.name = nameOf(el, role);
    if (interactive) node.ref = assignRef(el, role, node.name);
    const description = descriptionOf(el, node.name);
    if (description) node.description = description;
    if (role === 'heading') node.level = Number(tag.slice(1)) || Number(el.getAttribute('aria-level')) || 0;
    const value = valueOf(el);
    if (value) node.value = value;
    if (tag === 'SELECT') {
      node.options = Array.from(el.options || []).slice(0, 30).map((o) => o.value);
    }
    const states = statesOf(el, role);
    if (states.length) node.states = states;

    const leaf = interactive || role === 'img' || role === 'iframe' || role === 'heading';
    if (!leaf) {
      const direct = tag === 'LABEL' ? '' : ownText(el);
      if (direct) node.text = clip(direct, MAX_TEXT);
      node.children = visitChildren(el);
      if (role === 'generic' && !node.text && node.children.length <= 1) return node.children;
    }
    return [node];
  };

  const roots = [];
  const body = document.body;
  if (body) {
    roots.push(...visitChildren(body));
  }

  const doc = document.documentElement;
  const scrollMax = Math.max(0, (doc ? doc.scrollHeight : 0) - window.innerHeight);
  const scrollPercent = scrollMax > 0 ? Math.round((window.scrollY / scrollMax) * 100) : 0;

  return {
    url: location.href,
    title: document.title,
    nodes: roots,
    nextRef: refCounter,
    scrollPercent,
    scrollable: scrollMax > 0,
  };
}

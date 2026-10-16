// In-page observation: returns every visible element a user can act on or read as a label,
// with role, accessible name, context and state. Tags each one with data-tri-id so Python can
// act on exactly the element it chose. Pure DOM reads apart from that attribute.
(() => {
  const ACTIONABLE = 'button, a[href], input, textarea, select, [role=button], [role=tab], [role=menuitem], ' +
    '[role=menuitemradio], [role=menuitemcheckbox], [role=checkbox], [role=switch], [role=radio], [role=option], ' +
    '[role=link], [role=combobox], [role=textbox], [contenteditable=true]'
  const LABELS = 'h1, h2, h3, h4, [role=heading], [role=dialog] [class*=title], label'
  const LANDMARKS = { HEADER: 'banner', NAV: 'navigation', MAIN: 'main', ASIDE: 'complementary', FOOTER: 'contentinfo' }
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim()
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    if (r.width < 2 || r.height < 2) return false
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight * 3 || r.left > innerWidth) return false
    const cs = getComputedStyle(el)
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false
    if (el.closest('[aria-hidden=true], [inert]')) return false
    return true
  }
  const implicitRole = (el) => {
    const t = el.tagName
    if (el.getAttribute('role')) return el.getAttribute('role')
    if (t === 'BUTTON') return 'button'
    if (t === 'A') return 'link'
    if (t === 'SELECT') return 'combobox'
    if (t === 'TEXTAREA') return 'textbox'
    if (t === 'INPUT') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase()
      return { checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button', range: 'slider', file: 'button' }[ty] || 'textbox'
    }
    if (/^H[1-6]$/.test(t)) return 'heading'
    if (t === 'LABEL') return 'label'
    if (el.isContentEditable) return 'textbox'
    return 'generic'
  }
  // Hoppscotch's button component sets aria-label="button"; such placeholders carry no meaning.
  const PLACEHOLDER_ARIA = new Set(['button', 'link', 'icon', ''])
  const tooltipOf = (el) => {
    const t = el._tippy || (el.parentElement && el.parentElement._tippy)
    if (!t || !t.props) return ''
    const c = t.props.content
    const raw = typeof c === 'string' ? c : (c && c.textContent) || ''
    const div = document.createElement('div')
    div.innerHTML = raw.replace(/<kbd>.*?<\/kbd>/g, ' ')
    return clean(div.textContent)
  }
  const nameOf = (el) => {
    const aria = clean(el.getAttribute('aria-label'))
    if (aria && !PLACEHOLDER_ARIA.has(aria.toLowerCase())) return aria
    const lb = el.getAttribute('aria-labelledby')
    if (lb) {
      const txt = lb.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText).join(' ')
      if (clean(txt)) return clean(txt)
    }
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      if (el.id) {
        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
        if (l && clean(l.innerText)) return clean(l.innerText)
      }
      if (clean(el.getAttribute('placeholder'))) return clean(el.getAttribute('placeholder'))
    }
    if (el.classList && el.classList.contains('cm-content')) {
      // A code editor is named by its placeholder, never by its content (content is data, not UI).
      const ph = el.querySelector('.cm-placeholder')
      if (ph && clean(ph.innerText)) return clean(ph.innerText)
      return 'Code editor'
    }
    const text = clean(el.innerText).slice(0, 120)
    if (text) return text
    const tip = tooltipOf(el)
    if (tip) return tip
    if (clean(el.getAttribute('title'))) return clean(el.getAttribute('title'))
    const img = el.querySelector && el.querySelector('img[alt]')
    if (img && clean(img.alt)) return clean(img.alt)
    if (clean(el.getAttribute('data-tippy-content'))) return clean(el.getAttribute('data-tippy-content'))
    return ''
  }
  const contextOf = (el) => {
    const parts = []
    let n = el.parentElement
    while (n && n !== document.body) {
      const role = n.getAttribute && n.getAttribute('role')
      if (role === 'dialog' || n.tagName === 'DIALOG' || (n.classList && n.classList.contains('modal'))) {
        const h = n.querySelector('h1, h2, h3, h4, [class*=title]')
        parts.push('dialog:' + clean(n.getAttribute('aria-label') || (h && h.innerText) || '').slice(0, 40))
      } else if (n.classList && n.classList.contains('tippy-box')) {
        parts.push('popover')
      } else if (role === 'menu' || role === 'tablist' || role === 'listbox') {
        parts.push(role)
      } else if (LANDMARKS[n.tagName]) {
        parts.push(LANDMARKS[n.tagName])
      } else if (role && ['banner', 'navigation', 'main', 'complementary', 'contentinfo', 'region'].includes(role)) {
        parts.push(role)
      }
      n = n.parentElement
    }
    return parts.reverse().join('>')
  }
  document.querySelectorAll('[data-tri-id]').forEach((n) => n.removeAttribute('data-tri-id'))
  const seen = new Set()
  const out = []
  let i = 0
  const collect = (el, kind) => {
    if (seen.has(el) || !visible(el)) return
    // An actionable element nested inside another actionable element is the same control.
    if (kind === 'action' && el.parentElement && el.parentElement.closest(ACTIONABLE) && el.getAttribute('role') !== 'textbox') return
    if (el.closest('.cm-editor') && !(el.classList && el.classList.contains('cm-content'))) return
    seen.add(el)
    const role = implicitRole(el)
    const name = nameOf(el)
    if (!name && kind === 'label') return
    const r = el.getBoundingClientRect()
    const href = el.getAttribute('href')
    const id = String(i++)
    el.setAttribute('data-tri-id', id)
    out.push({
      tri: id, kind, role, name, tag: el.tagName.toLowerCase(), context: contextOf(el),
      text: (el.classList && el.classList.contains('cm-content')) ? '' : clean(el.innerText).slice(0, 120),
      placeholder: clean(el.getAttribute('placeholder')),
      title: clean(el.getAttribute('title')), aria_label: clean(el.getAttribute('aria-label')), tooltip: tooltipOf(el),
      testid: el.getAttribute('data-testid') || '', href: href || '',
      external: !!(href && /^https?:/i.test(href) && new URL(href, location.href).origin !== location.origin),
      target_blank: el.getAttribute('target') === '_blank',
      selected: el.getAttribute('aria-selected') === 'true' || el.classList.contains('active') || el.getAttribute('aria-current') === 'page',
      expanded: el.getAttribute('aria-expanded') === 'true',
      checked: el.getAttribute('aria-checked') === 'true' || !!el.checked,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      editor: !!(el.classList && el.classList.contains('cm-content')),
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
    })
  }
  document.querySelectorAll(ACTIONABLE).forEach((el) => collect(el, 'action'))
  document.querySelectorAll(LABELS).forEach((el) => collect(el, 'label'))
  return { url: location.href, path: location.pathname, title: document.title, viewport: [innerWidth, innerHeight], elements: out }
})()

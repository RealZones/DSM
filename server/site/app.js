'use strict'
// The whole site in one script. Plain JS over a small API, so
// nothing here needs a build step on the VPS.

const base = location.pathname.replace(/\/(index\.html)?$/, '')
const linkedSite = base === '/market/link'
const page = document.getElementById('page')
// The status rides on the error, so a page that needs a linked account can show how to link instead of failing.
const failed = (path, res) => Object.assign(new Error(`${path}: ${res.status}`), { status: res.status })
const api = async path => {
  const res = await fetch(`${base}/api/${path}`)
  if (!res.ok) throw failed(path, res)
  return res.json()
}
const post = async (path, body) => {
  const res = await fetch(`${base}/api/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw failed(path, res)
  return res.json()
}
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const url = text => encodeURIComponent(text)

// Minecraft's own rarity colours, so a name reads the same here as in game
// except Rare and Epic, lifted a little: the game draws them on a near-black tooltip, and on these
// cards #5555FF and #AA00AA all but vanished (Wither Shield Scroll's name was hard to read).
const RARITY = { 1: '#FFFFFF', 2: '#55FF55', 3: '#7373FF', 4: '#C13DC1', 5: '#FFAA00', 6: '#FF55FF', 7: '#55FFFF', 8: '#FF5555' }
const TIER = { COMMON: 1, UNCOMMON: 2, RARE: 3, EPIC: 4, LEGENDARY: 5, MYTHIC: 6, DIVINE: 7, SPECIAL: 8 }
const tierStyle = rarity => RARITY[rarity] ? `style="color:${RARITY[rarity]}"` : ''

const num = n => Math.round(n ?? 0).toLocaleString('en-US')
const coins = n => `${num(n)} Coins`
function brief (n) {
  const a = Math.abs(n ?? 0)
  const trim = (v, p) => v.toFixed(p).replace(/\.?0+$/, '')
  return (n < 0 ? '-' : '') + (a >= 1e9 ? trim(a / 1e9, 2) + 'b' : a >= 1e6 ? trim(a / 1e6, 1) + 'm' : a >= 1e3 ? Math.round(a / 1e3) + 'k' : Math.round(a))
}
function span (ms) {
  const m = Math.abs(ms) / 60000
  const say = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  if (m < 1) return 'a few seconds'
  if (m < 60) return say(Math.round(m), 'minute')
  const h = m / 60
  if (h < 36) return say(Math.round(h), 'hour')
  return say(Math.round(h / 24), 'day')
}
const ago = ms => `${span(Date.now() - ms)} ago`
const when = ms => new Date(ms).toLocaleString('en-US')
const signed = n => n >= 0 ? `<span class="lime">+${num(n)} Coins</span>` : `<span class="red">${num(n)} Coins</span>`

const EMOJI = {}
const emojiReady = fetch(`${base}/item-emoji-ids.json`).then(r => r.ok ? r.json() : {}).then(m => Object.assign(EMOJI, m)).catch(() => {})
function icon (row, size) {
  const it = row.icon
  const letter = esc(((row.name || row.label || row.entry || '?').replace(/^\[[^\]]*\]\s*/, ''))[0] || '?')
  const fallback = `<span class=&quot;noicon&quot;>${letter}</span>`
  const letterOut = `this.outerHTML='${fallback}'`
  // A new item's picture can be its base item drawn flat or as a block, and when dragsim.co has
  // never drawn that base item the server cannot tell which, so it sends both to try in turn.
  const img = (src, alt) => alt
    ? `<img class="item-img" loading="lazy" alt="" src="${esc(src)}" data-alt="${esc(alt)}" onerror="if(this.dataset.alt){this.src=this.dataset.alt;this.dataset.alt=''}else{${letterOut}}">`
    : `<img class="item-img" loading="lazy" alt="" src="${esc(src)}" onerror="${letterOut}">`
  if (it && it.url) return img(it.url, it.alt)
  if (it && it.head) return img(`https://mc-heads.net/head/${it.head}/48`)
  // The market has no picture for some common items (Warden Catalyst showed a grey "W"), but the
  // Endsight bot has an emoji drawn for ~1,000 of them. item-emoji-ids.json links to the bot's own
  // map on the VPS, so items its daily job adds show here without a deploy.
  if (EMOJI[row.entry]) return img(`https://cdn.discordapp.com/emojis/${EMOJI[row.entry]}.png`)
  return `<span class="noicon">${letter}</span>`
}
const head = name => `<img class="head-img" loading="lazy" alt="" src="https://mc-heads.net/avatar/${url(name)}/32">`
const playerLink = name => name ? `<a class="link" href="#/player/${url(name)}">${esc(name)}</a>` : 'someone'

// ---------------------------------------------------------------- navigation

const ICONS = {
  home: 'M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z',
  chart: 'M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z',
  store: 'M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z',
  user: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z',
  flips: 'M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z',
  build: 'M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z',
  gem: 'M19 3H5L2 9l10 12L22 9l-3-6zM9.62 8l1.5-3h1.76l1.5 3H9.62zM11 10v6.68L5.44 10H11zm2 0h5.56L13 16.68V10zm6.26-2h-2.65l-1.5-3h2.65l1.5 3zM6.24 5h2.65l-1.5 3H4.74l1.5-3z',
  bell: 'M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z',
  trash: 'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z',
  star: 'M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z',
  starBorder: 'M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z',
  bellOutline: 'M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z',
  more: 'M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z',
  download: 'M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z'
}
const svg = (name, size = 24, colour = 'currentColor') => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="${colour}"><path d="${ICONS[name]}"/></svg>`
// The menu's outline icons (Material's outlined set); download is a line icon already.
Object.assign(ICONS, {
  homeLine: 'M12 5.69l5 4.5V18h-2v-6H9v6H7v-7.81l5-4.5M12 3L2 12h3v8h6v-6h2v6h6v-8h3L12 3z',
  storeLine: 'M18.36 9l.6 3H5.04l.6-3h12.72M20 4H4v2h16V4zm0 3H4l-1 5v2h1v6h10v-6h4v6h2v-6h1v-2l-1-5zM6 18v-4h6v4H6z',
  userLine: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM7.07 18.28c.43-.9 3.05-1.78 4.93-1.78s4.51.88 4.93 1.78C15.57 19.36 13.86 20 12 20s-3.57-.64-4.93-1.72zm11.29-1.45c-1.43-1.74-4.9-2.33-6.36-2.33s-4.93.59-6.36 2.33C4.62 15.49 4 13.82 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8c0 1.82-.62 3.49-1.64 4.83zM12 6c-1.94 0-3.5 1.56-3.5 3.5S10.06 13 12 13s3.5-1.56 3.5-3.5S13.94 6 12 6zm0 5c-.83 0-1.5-.67-1.5-1.5S11.17 8 12 8s1.5.67 1.5 1.5S12.83 11 12 11z'
})
const LINE = { home: 'homeLine', store: 'storeLine', bell: 'bellOutline', user: 'userLine' }
const NAV = [['', 'home', 'Home'], ['bazaar', 'store', 'Bazaar'],
  ['notifier', 'bell', 'Notifier'], ['me', 'user', 'My Profile'], ['mod', 'download', 'Mod']]
// An end stone block with a violet glow: the old gold cube, moved to the End.
const CUBE = '<svg viewBox="0 0 36 36" width="36" height="36"><defs><radialGradient id="endGlow" cx="50%" cy="50%" r="60%"><stop offset="0" stop-color="#9a7fd1" stop-opacity=".55"/><stop offset="1" stop-color="#9a7fd1" stop-opacity="0"/></radialGradient></defs><circle cx="18" cy="18" r="18" fill="url(#endGlow)"/><path fill="#e6dfae" d="M18 5 30 11.5v13L18 31 6 24.5v-13z"/><path fill="#bfb781" d="M18 18v13L6 24.5v-13z"/><path fill="#f3eecb" d="M18 18 6 11.5 18 5l12 6.5z"/><circle cx="12" cy="22" r="1.3" fill="#a59d6b"/><circle cx="23" cy="24" r="1.1" fill="#d3cb98"/><circle cx="19" cy="11" r="1.1" fill="#d6cf9f"/></svg>'
document.getElementById('rail').innerHTML = `<a class="logo" href="#/">${CUBE}<span>DSM</span></a>` +
  // Both icons are drawn; the stylesheet shows the outline one, and the filled one on the page you're on.
  NAV.map(([path, ic, label]) => `<a class="item" href="#/${path}" data-nav="${path}"><i class="ic line">${svg(LINE[ic] ?? ic)}</i><i class="ic fill">${svg(ic)}</i><span>${label}</span>${path === 'notifier' ? '<i class="dot" id="alertDot" hidden></i>' : ''}</a>`).join('')
document.getElementById('burger').addEventListener('click', () => document.getElementById('rail').classList.toggle('open'))
// The public site's account button, once the status says whether this browser is linked.
function accountButton () {
  if (!linkedSite) return
  if (!state.signedIn) {
    document.getElementById('bar').insertAdjacentHTML('beforeend', '<a class="btn btn-primary btn-sm" href="#/me">Sign in</a>')
    return
  }
  const signOut = document.createElement('button')
  signOut.className = 'btn btn-secondary btn-sm'
  signOut.textContent = 'Sign out'
  signOut.addEventListener('click', async () => {
    signOut.disabled = true
    try {
      await post('logout', {})
      location.replace(`${base}/`)
    } catch {
      signOut.disabled = false
    }
  })
  document.getElementById('bar').append(signOut)
}

// ---------------------------------------------------------------- start page

async function home () {
  const d = await api('home')
  page.innerHTML = `
    <section class="fav-bar" id="favorites" aria-label="Favorite items summary" hidden></section>
    <div class="hero" id="hero"><h1>DSM - Dragsim Auction House &amp; Bazaar</h1><p>Prices, tracked flips and deals for Dragsim, worked out from every sale.</p></div>
    ${d.day || d.deals.length ? `<div class="news"><h3>Latest</h3>
      ${d.day ? `<p>Last 24 hours: ${signed(d.day.profit)} from ${d.day.flips} flip${d.day.flips === 1 ? '' : 's'}. Last 7 days: ${signed(d.week.profit)}.</p>` : ''}
      ${d.deals.length ? `<p>Biggest deal open: <a class="link" href="#/item/${url(d.deals[0].entry)}">${esc(d.deals[0].name)}</a>, about ${signed(d.deals[0].profit)} after the fee.</p>` : ''}
    </div>` : ''}
    <h2 class="section">Flip Tools</h2>
    <div class="tiles">
      ${[['bazaar', 'store', '#e6dfae', 'Bazaar', 'Every product, and what really traded.'],
        ['notifier', 'bell', '#b9a2ec', 'Notifier', 'Track items and hear when they hit your price.']]
        .map(([path, ic, colour, title, text]) => `<a class="tile" href="#/${path}">${svg(ic, 40, colour)}<b>${title}</b><span>${text}</span></a>`).join('')}
    </div>
    <h2 class="section">Deals</h2>
    <div class="grid-deals">${d.deals.slice(0, 4).map(dealCard).join('') || '<p class="text-muted">Nothing right now.</p>'}</div>`
  wireCopy()
  wireFavorites()
  drawFavorites()
}

// ---------------------------------------------------------------- favorites

// Favorites. The star beside an item's name keeps it in this
// browser, and the start page then leads with a card per item: what one costs now against what it
// has been selling for, the month in the tooltip, Notify, and the star to drop it again. Kept per
// linked player like the search history, so two accounts on one browser keep their own lists.
const FAVORITE_REFRESH = 5 * 60 * 1000      // prices move slower than this, so more often only costs requests
const fav = { data: {}, failed: false, loading: false, seq: 0, timer: null }
const favoriteKey = () => linkedSite ? `linkedFavorites:${state.player}` : 'favoriteItems'

function favorites () {
  try { return JSON.parse(localStorage.getItem(favoriteKey())) || [] } catch { return [] }
}
const isFavorite = entry => favorites().some(f => f.entry === entry)
function toggleFavorite (row) {
  const rows = favorites()
  const next = rows.some(f => f.entry === row.entry) ? rows.filter(f => f.entry !== row.entry) : [...rows, { ...row, addedAt: new Date().toISOString() }]
  try { localStorage.setItem(favoriteKey(), JSON.stringify(next)) } catch {}
  return isFavorite(row.entry)      // read back, so a browser that refuses storage shows the truth
}
function paintStar (button, on) {
  const label = on ? 'Remove from favorites' : 'Add to favorites'
  button.classList.toggle('active', on)
  button.setAttribute('aria-pressed', String(on))
  button.setAttribute('aria-label', label)
  button.title = label
  button.innerHTML = svg(on ? 'star' : 'starBorder', '1em')
}

function favoriteCard (f, m, loading) {
  const has = v => v !== undefined && v !== null
  const now = m ? m.now : undefined
  const recent = m ? m.recent : undefined
  // The change is now minus recent. An item that has never sold has no median, and it would
  // show its whole price as a rise, so it gets only its Now.
  const delta = has(now) && has(recent) ? now - recent : null
  const pct = delta !== null && Math.abs(recent) > 1 ? delta / recent * 100 : null
  const month = has(now) && m && has(m.monthly) ? now - m.monthly : null
  const tip = month !== null ? `Monthly change: ${month >= 0 ? '+' : ''}${num(month)} coins` : loading ? 'Loading monthly comparison…' : 'Monthly comparison unavailable'
  const up = (delta ?? 0) >= 0
  const move = delta === null ? '' : delta === 0 ? 'No change' : `${up ? '+' : ''}${num(delta)} coins`
  return `<article class="fav-card" data-fav-entry="${esc(f.entry)}">
    <a class="fav-link" href="#/item/${url(f.entry)}">
      <span class="fav-icon">${icon(f)}</span>
      <span class="fav-details"><span class="fav-name">${esc(f.name || f.entry)}</span>
        <span class="fav-stats"><span class="fav-delta${delta === null ? '' : up ? ' up' : ' down'}${loading ? ' skeleton' : ''}" title="${esc(tip)}">${move}${pct !== null && delta !== 0 ? `<span class="fav-pct"> (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)</span>` : ''}</span>
          ${has(now) ? `<span class="badge bg-secondary">Now: ${num(now)}</span>` : loading ? '<span class="skeleton badge-skeleton" aria-hidden="true"></span>' : ''}</span></span>
    </a>
    <div class="fav-actions">
      <a class="btn btn-primary" href="#/notifier" data-notify>${svg('bellOutline', 18)}<span>Notify</span></a>
      <button type="button" class="fav-toggle small active" data-unfav aria-pressed="true" aria-label="Remove from favorites" title="Remove from favorites">${svg('star', '1em')}</button>
    </div>
    <div class="fav-more">
      <button type="button" class="fav-more-btn" data-more aria-label="More actions" aria-expanded="false">${svg('more', 20)}</button>
      <div class="fav-menu" hidden>
        <a class="fav-menu-item" href="#/notifier" data-notify>${svg('bellOutline', 18)}Notify</a>
        <button type="button" class="fav-menu-item" data-unfav>${svg('star', 18, '#facc15')}Remove</button>
      </div>
    </div>
  </article>`
}

function paintFavorites () {
  const box = document.getElementById('favorites')
  if (!box) return
  const rows = favorites()
  // The big title goes once there is something of yours to show instead.
  document.getElementById('hero').hidden = rows.length > 0
  box.hidden = !rows.length
  if (!rows.length) { box.innerHTML = ''; return }
  const stamps = rows.map(f => fav.data[f.entry]?.lastUpdated).filter(Boolean)
  box.innerHTML = `<div class="fav-head"><h2>Favorites</h2><div class="fav-meta">
      ${fav.loading ? '<span class="spinner-border spinner-border-sm" role="status" aria-label="Loading"></span>' : ''}
      ${stamps.length ? `<span>Last updated ${new Date(Math.max(...stamps)).toLocaleTimeString('en-US')}</span>` : ''}</div></div>
    ${fav.failed ? '<p class="fav-error">Failed to load price movement data. Please try again later.</p>' : ''}
    <div class="fav-list">${rows.map(f => favoriteCard(f, fav.data[f.entry], fav.loading && !fav.data[f.entry])).join('')}</div>`
}

async function drawFavorites () {
  clearTimeout(fav.timer)
  const rows = favorites()
  if (!document.getElementById('favorites')) return
  if (!rows.length) { paintFavorites(); return }
  const seq = ++fav.seq
  fav.loading = true
  paintFavorites()
  let data = null
  try {
    data = await api(`prices/change?itemTags=${url(rows.map(f => f.entry).join(','))}`)
  } catch {}
  if (seq !== fav.seq) return
  fav.loading = false
  fav.failed = !data
  if (data) fav.data = data
  paintFavorites()
  if (document.getElementById('favorites')) fav.timer = setTimeout(drawFavorites, FAVORITE_REFRESH)
}

function closeFavoriteMenus (except) {
  document.querySelectorAll('.fav-menu').forEach(menu => {
    if (menu === except) return
    menu.hidden = true
    menu.previousElementSibling.setAttribute('aria-expanded', 'false')
  })
}
function wireFavorites () {
  document.getElementById('favorites').addEventListener('click', e => {
    const card = e.target.closest('[data-fav-entry]')
    if (!card) return
    const entry = card.dataset.favEntry
    if (e.target.closest('[data-notify]')) {
      const row = favorites().find(f => f.entry === entry)
      notify.picked = { entry, name: row?.name || entry, filters: {} }
    } else if (e.target.closest('[data-unfav]')) {
      toggleFavorite({ entry })
      paintFavorites()
    } else if (e.target.closest('[data-more]')) {
      const menu = card.querySelector('.fav-menu')
      closeFavoriteMenus(menu)
      menu.hidden = !menu.hidden
      menu.previousElementSibling.setAttribute('aria-expanded', String(!menu.hidden))
    }
  })
}
document.addEventListener('mousedown', e => { if (!e.target.closest('.fav-more')) closeFavoriteMenus() })
// Another tab starring or dropping an item shows here too, through the storage event.
window.addEventListener('storage', e => {
  if (e.key !== null && e.key !== favoriteKey()) return
  if (document.getElementById('favorites')) drawFavorites()
  const star = document.getElementById('favToggle')
  if (star) paintStar(star, isFavorite(item.entry))
})

// ---------------------------------------------------------------- deals

function dealCard (d) {
  const pct = Math.round(d.roi * 100)
  return `<div class="deal">
    <div class="top">${icon(d)}<a class="nm" href="#/item/${url(d.entry)}" ${tierStyle(d.rarity)}>${esc(d.name)}${d.count > 1 ? ` x${d.count}` : ''}</a><span class="badge bg-success">BIN</span></div>
    <div class="body">
      <p>Cost:<b>${coins(d.unit * d.count)}</b></p>
      <p>Target price:<b>${coins(d.target * d.count)}</b></p>
      <p>Estimated Profit:<b class="lime">+${coins(d.profit)} (${pct}%)</b></p>
      <p>Lowest BIN after it:<b>${coins(d.next_bin)}</b></p>
      <p>Volume:<b>${d.volume ? `~${d.volume.toFixed(1)} per day` : 'unknown'}</b></p>
      <p>Seller:<b>${playerLink(d.seller)}</b></p>
      <p class="text-muted" style="font-size:13px">Listed ${ago(d.at)}${d.time_to_sell ? `, usually sells in ${span(d.time_to_sell * 60000)}` : ''}</p>
    </div>
    <div class="foot"><button class="btn btn-primary copy" data-copy="/ah ${esc(d.seller)}">Copy /ah ${esc(d.seller)}</button></div>
  </div>`
}
function wireCopy () {
  page.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', () => {
    navigator.clipboard?.writeText(b.dataset.copy)
    const text = b.textContent
    b.textContent = 'Copied'
    setTimeout(() => { b.textContent = text }, 900)
  }))
}
// ---------------------------------------------------------------- crafts

const KIND = { craft: 'Craft', forge: 'Forge', upgrade: 'Upgrade' }
const craft = { count: 1 }
function amount (text) {
  const m = /^\s*([\d.,]+)\s*([kmb]?)\s*$/i.exec(text || '')
  return m ? parseFloat(m[1].replace(/,/g, '')) * { '': 1, k: 1e3, m: 1e6, b: 1e9 }[m[2].toLowerCase()] : 0
}
const SOURCE = { bazaar: 'Bazaar', order: 'Buy order', ah: 'Auction', shop: 'Shop', wait: 'new listings over a day' }
function sourceLine (rows) {
  return rows.map(r => r.source === 'estimate' ? `${num(r.count)} not listed, about ${brief(r.coins / r.count)} each`
    : `${num(r.count)} from ${SOURCE[r.source] || r.source}${r.count ? ` at ${brief(r.coins / r.count)}` : ''}${r.sellers.length ? ` (${r.sellers.slice(0, 3).map(playerLink).join(', ')})` : ''}`).join(', ')
}
// A part you make opens its own craft, one you buy opens where to buy it.
function partLink (part) {
  if (part.method === 'craft') return `#/craft/${url(part.entry)}`
  return `#/item/${url(part.entry)}${part.sources.some(r => r.source === 'ah' || r.source === 'wait') ? '/active' : ''}`
}
function planRows (part, depth) {
  const made = part.method === 'craft'
  const making = `${KIND[part.kind] || 'Craft'}${part.hours ? `, ${span(part.hours * 3600000)}` : ''}${part.buy_cost ? `, cheaper than buying at ${brief(part.buy_cost)}` : ''}`
  // A part made only in part: the cheap copies are bought and the rest made, as the planner does.
  const how = !made ? sourceLine(part.sources) : part.sources.length ? `${sourceLine(part.sources)} and ${num(part.made)} made: ${making}` : making
  return `<div class="step" style="padding-left:${depth * 20}px"><span class="what"><a href="${partLink(part)}">${icon(part)}${num(part.count)}x ${esc(part.name)}</a><span class="how">${how}</span></span><span class="num">${coins(part.cost)}</span></div>` +
    part.parts.map(p => planRows(p, depth + 1)).join('')
}
async function craftPage (entry) {
  const d = await api(`crafts/${url(entry)}?n=${craft.count}`)
  const pick = [1, 2, 4, 8, 16, 32, 64]
  page.innerHTML = `
    <div class="item-head"><a class="item-title" href="#/item/${url(entry)}">${icon(d)}<h1 ${tierStyle(TIER[d.tier])}>${esc(d.name)}</h1></a>${d.tested === false ? '<span class="badge bg-secondary">Rough</span>' : ''}</div>
    <div class="controls"><label>Quantity: <select id="cCount">${pick.map(n => `<option ${craft.count === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label></div>
    ${d.estimated ? '<div class="panel"><p class="mb-0">Nobody is selling some of these parts and they cannot be made, so they are priced at their last sale, or our estimate if they never sold.</p></div>' : ''}
    ${d.plan ? `<div class="summary">
      <div><span>Selected input cost</span><strong>${coins(d.cost)}</strong></div>
      <div><span>Sells for</span><strong>${d.price ? coins(d.price * d.count) : 'unknown'}</strong></div>
      <div><span>Potential profit</span><strong>${d.price ? signed(d.profit) : 'unknown'}</strong></div>
      ${d.hours ? `<div><span>Forge time</span><strong>${span(d.hours * 3600000)}</strong></div>` : ''}
    </div>
    <div class="panel"><h3>Combined shopping list</h3><p class="text-muted">Everything you'd buy, added up. What's in the list has been taken off the market once, so nothing is counted twice.</p>
      ${d.shopping.map(l => `<div class="step"><span class="what"><a href="${partLink(l)}">${icon(l)}${num(l.count)}x ${esc(l.name)}</a><span class="how">${sourceLine(l.sources)}</span></span><span class="num">${coins(l.cost)}</span></div>`).join('')}</div>
    <div class="panel"><h3>Recipe breakdown</h3>${d.plan.parts.map(p => planRows(p, 0)).join('')}${d.plan.coins ? `<div class="step"><span class="what">Coins</span><span class="num">${coins(d.plan.coins)}</span></div>` : ''}</div>`
    : `<div class="panel"><p class="mb-0">${d.short.length ? `Can't be made right now, nobody is selling ${esc(d.short.join(', '))}.` : 'Not all of this recipe is known.'}</p><p class="mb-0 text-muted">${esc((d.recipes[0] || {}).text || '')}</p></div>`}`
  document.getElementById('cCount').addEventListener('change', e => { craft.count = +e.target.value; craftPage(entry) })
}

// ---------------------------------------------------------------- notifier

const notify = { picked: null, names: {}, timer: null }
const showAlerts = n => { const dot = document.getElementById('alertDot'); dot.textContent = n; dot.hidden = !n }
setInterval(() => api('status').then(s => showAlerts(s.alerts)).catch(() => {}), 60000)

function spark (w) {
  const days = w.days
  if (days.length < 2) return ''
  const low = Math.min(...days)
  const high = Math.max(...days)
  // A target miles from the market would flatten the line, so it only shows when it is near.
  const target = w.price && w.price > low / 3 && w.price < high * 3 ? w.price : 0
  const bottom = Math.min(low, target || low)
  const top = Math.max(high, target)
  const y = price => (46 - (price - bottom) / (top - bottom || 1) * 42).toFixed(1)
  const line = days.map((price, i) => `${(i / (days.length - 1) * 300).toFixed(1)},${y(price)}`).join(' ')
  return `<svg class="spark" viewBox="0 0 300 50" preserveAspectRatio="none" role="img" aria-label="Daily price over 30 days"><polyline points="${line}"/>${target ? `<line x1="0" x2="300" y1="${y(target)}" y2="${y(target)}"/>` : ''}</svg>`
}
function watchCard (w) {
  const change = w.change === null ? '-' : `${w.change >= 0 ? '+' : ''}${Math.round(w.change * 100)}%`
  const side = (value, label) => `<option value="${value}" ${w.side === value ? 'selected' : ''}>${label}</option>`
  return `<div class="flip watch ${w.matches.length ? 'hit' : ''}" data-watch="${w.id}" data-entry="${esc(w.entry)}">
    <div class="flip-title"><a class="it" href="#/item/${url(w.entry)}">${icon(w)}<span ${tierStyle(TIER[w.tier])}>${esc(w.name)}</span></a>
      <button class="icon-btn" data-drop aria-label="Stop tracking ${esc(w.name)}" title="Stop tracking">${svg('trash', 20)}</button></div>
    ${w.filters.length ? `<p class="hits">${esc(w.filters.map(([k, v]) => `${k}: ${v}`).join(', '))}</p>` : ''}
    <div class="stats" style="margin-top:10px">
      <div><b>${w.lowest ? brief(w.lowest) : '-'}</b><span>Lowest BIN${w.listed ? ` (${num(w.listed)} up)` : ''}</span></div>
      <div><b>${w.last ? brief(w.last) : '-'}</b><span>${w.last ? `Last sold ${ago(w.last_at)}` : 'Last sold'}</span></div>
      <div><b>${w.worth ? brief(w.worth) : '-'}</b><span>Worth</span></div>
      <div><b>${w.week_low ? `${brief(w.week_low)} - ${brief(w.week_high)}` : '-'}</b><span>7 days, ${w.sold_week} sold</span></div>
      <div><b class="${w.change > 0 ? 'lime' : w.change < 0 ? 'red' : ''}">${change}</b><span>Against the week before</span></div>
    </div>
    ${spark(w)}
    <div class="controls" style="margin:6px 0 0">
      <label>Tell me when <select data-side>${side('below', 'one lists at or under')}${side('above', 'one sells at or over')}</select></label>
      <label>Price: <input data-price value="${w.price ? brief(w.price) : ''}" placeholder="Set a price" size="10"></label>
      <button class="btn btn-primary btn-sm" data-save>Save</button>
      <span class="text-muted" data-error></span>
    </div>
    ${w.matches.length ? `<p class="mb-1 mt-2 lime">At your price now:</p>${w.matches.slice(0, 6).map(m => `<div class="step"><span class="what"><b>${coins(m.unit)}</b>${m.count > 1 ? ` x${num(m.count)}` : ''} <span class="how">${m.seller ? `from ${playerLink(m.seller)}` : 'on the bazaar'}</span></span>${m.seller ? `<button class="btn btn-primary btn-sm" data-copy="/ah ${esc(m.seller)}">Copy /ah ${esc(m.seller)}</button>` : ''}</div>`).join('')}` : ''}
    ${w.hits.length ? `<p class="hits">${w.hits.map(h => `${brief(h.unit)} ${h.sold ? 'sold' : 'listed'} ${ago(h.at)}`).join(', ')}</p>` : ''}
  </div>`
}
async function drawWatches () {
  const d = await api('watches')
  const box = document.getElementById('watchList')
  if (!box) return
  box.innerHTML = d.watches.map(watchCard).join('') || '<p class="text-muted">Nothing tracked yet.</p>'
  showAlerts(d.watches.filter(w => w.matches.length).length)
  wireCopy()
}
function readPrice (text) {
  const price = amount(text)
  return text.trim() && !price ? null : price
}
async function notifier () {
  const picked = notify.picked
  notify.picked = null
  page.innerHTML = `
    <h1 class="page">Notifier</h1>
    <div class="panel" id="notifyDelivery"><p class="mb-0 text-muted">Loading alert delivery settings…</p></div>
    <div class="panel"><h3>Track an item</h3>
      <p class="text-muted">Leave the price empty to just follow it. A matching BIN includes the seller.</p>
      <div class="controls" style="margin:0">
        <label>Item: <input id="nItem" list="nItems" placeholder="Name" autocomplete="off" value="${esc(picked ? picked.name : '')}"></label><datalist id="nItems"></datalist>
        <label>Tell me when <select id="nSide"><option value="below">one lists at or under</option><option value="above">one sells at or over</option></select></label>
        <label>Price: <input id="nPrice" placeholder="200m" size="10"></label>
        <button class="btn btn-primary" id="nAdd">Track</button>
        <span class="text-muted" id="nError"></span>
      </div></div>
    <div id="watchList"><p class="text-muted">Loading Data...</p></div>`
  await Promise.all([drawWatches(), drawNotificationSettings()])
  const name = document.getElementById('nItem')
  const say = text => { document.getElementById('nError').textContent = text }
  name.addEventListener('input', async () => {
    const text = name.value.trim()
    if (text.length < 2) return
    const d = await api(`search?q=${url(text)}`)
    for (const row of d.items) notify.names[row.name] = row.entry
    document.getElementById('nItems').innerHTML = d.items.map(row => `<option value="${esc(row.name)}"></option>`).join('')
  })
  document.getElementById('nAdd').addEventListener('click', async () => {
    const entry = picked && name.value === picked.name ? picked.entry : notify.names[name.value.trim()]
    const price = readPrice(document.getElementById('nPrice').value)
    if (!entry) return say('Pick an item from the list.')
    if (price === null) return say('Use a price like 200m or 1.5b.')
    await post('watch', { entry, side: document.getElementById('nSide').value, price, filters: picked ? picked.filters : {} })
    notifier()
  })
  document.getElementById('watchList').addEventListener('click', async e => {
    const card = e.target.closest('[data-watch]')
    if (!card) return
    const id = +card.dataset.watch
    if (e.target.closest('[data-drop]')) {
      await post('watch/delete', { id })
      drawWatches()
    } else if (e.target.closest('[data-save]')) {
      const price = readPrice(card.querySelector('[data-price]').value)
      if (price === null) { card.querySelector('[data-error]').textContent = 'Use a price like 200m or 1.5b.'; return }
      await post('watch', { id, entry: card.dataset.entry, side: card.querySelector('[data-side]').value, price })
      drawWatches()
    }
  })
  clearInterval(notify.timer)
  // Kept fresh while it is open, unless you are halfway through typing a price.
  notify.timer = setInterval(() => {
    if (!document.getElementById('watchList')) { clearInterval(notify.timer); return }
    if (!document.activeElement?.closest('#page input, #page select')) drawWatches().catch(() => {})
  }, 30000)
}

async function drawNotificationSettings () {
  const panel = document.getElementById('notifyDelivery')
  const settings = await api('notification-settings')
  if (!panel.isConnected) return
  panel.innerHTML = `<h3>Alert delivery</h3>
    <p class="text-muted">In-game alerts show buy opportunities. Discord also sends sales that meet your sell target.</p>
    <form id="notifySettings">
      <div class="controls">
        <label><input type="checkbox" name="inGame" ${settings.in_game ? 'checked' : ''}> In-game mod alerts</label>
        <label><input type="checkbox" name="webhookEnabled" ${settings.webhook_enabled ? 'checked' : ''} ${settings.webhook_configured ? '' : 'disabled'}> Discord webhook</label>
        ${settings.discord_dm_available ? `<label><input type="checkbox" name="dm" ${settings.discord_dm ? 'checked' : ''}> Endsight bot DM</label>` : ''}
      </div>
      <div class="controls">
        <label for="notifyWebhook">${settings.webhook_configured ? 'Replace webhook' : 'Webhook URL'}</label>
        <input id="notifyWebhook" name="webhook" type="password" autocomplete="off" placeholder="https://discord.com/api/webhooks/…">
        <button type="submit" class="btn btn-primary btn-sm">Save delivery</button>
        ${settings.webhook_configured ? '<button type="button" id="removeWebhook" class="btn btn-secondary btn-sm">Remove webhook</button>' : ''}
        <span id="notifyDeliveryMessage" class="text-muted" role="status"></span>
      </div>
    </form>`
  const form = panel.querySelector('form')
  const save = async remove => {
    const fields = form.elements
    const webhook = fields.webhook.value.trim()
    const body = { in_game: fields.inGame.checked, webhook_enabled: !remove && (Boolean(webhook) || fields.webhookEnabled.checked) }
    if (fields.dm) body.discord_dm = fields.dm.checked
    if (remove || webhook) body.webhook = remove ? '' : webhook
    try {
      await post('notification-settings', body)
      if (!panel.isConnected) return
      await drawNotificationSettings()
      panel.querySelector('#notifyDeliveryMessage').textContent = 'Saved.'
    } catch {
      if (panel.isConnected) panel.querySelector('#notifyDeliveryMessage').textContent = 'Could not save. Check that the webhook URL is from discord.com.'
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); save(false) })
  panel.querySelector('#removeWebhook')?.addEventListener('click', () => save(true))
}

// ---------------------------------------------------------------- reported trades

const HOW = {
  ah: 'This one also sells on the auction house, so the price above comes from there.',
  said: 'A trader named this price outright.',
  alone: 'Sold by itself for this, or everything else in the trade already had a price.',
  share: 'Only seen in bundles, so this is its share of the money and could be well out.'
}
const WORD = { ah: 'Auction', said: 'Stated', alone: 'Sold alone', share: 'Bundle share' }
function receipt (t) {
  const lines = (rows, sign) => rows.map(line => /^Coins:/.test(line) ? `<div class="${sign === '+' ? 'plus' : 'minus'}">${sign} <span class="coin">${esc(line)}</span></div>` : `<div class="${sign === '+' ? 'plus' : 'minus'}">${sign} ${esc(line)}</div>`).join('')
  const day = new Date(t.at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
  return `<div class="receipt"><div class="meta">${day}</div>
    ${t.got.length || t.gave.length ? `<div class="head">Trade successful${t.other ? ` with ${esc(t.other)}` : ''}!</div>${lines(t.got, '+')}${lines(t.gave, '-')}` : ''}
    ${t.note ? `<div class="note">${esc(t.note)}</div>` : ''}
    <div class="note">Posted by ${esc(t.post)}</div></div>`
}
// ---------------------------------------------------------------- bazaar

async function bazaar () {
  const d = await api('bazaar')
  const line = side => side.amount ? `${num(side.amount)} at ${brief(side.average)}` : '-'
  page.innerHTML = `
    <h1 class="page">Bazaar</h1>
    <div class="panel"><p class="mb-0">Dragsim doesn't publish bazaar sale prices, so these are read from the order book every 30 seconds. An order that fills part way or leaves while the traded count rises is a real trade. One that leaves with no rise was cancelled.</p></div>
    <div class="panel"><div style="overflow-x:auto"><table class="table">
      <tr><th>Product</th><th class="num">Buy now</th><th class="num">Sell now</th><th class="num">Really trades at</th><th class="num">Sell offers filled 24h</th><th class="num">Buy orders filled 24h</th></tr>
      ${d.products.map(p => `<tr><td><a href="#/item/${url(p.entry)}" ${tierStyle(TIER[p.tier])}>${esc(p.name)}</a></td><td class="num">${brief(p.ask)}</td><td class="num">${brief(p.bid)}</td>
        <td class="num">${p.price ? brief(p.price) : '-'}</td><td class="num">${line(p.bought)}</td><td class="num">${line(p.sold)}</td></tr>`).join('')}
    </table></div></div>`
}

// ---------------------------------------------------------------- item page

const item = { entry: null, range: 'week', filters: {} }
const PET_TIERS = ['COMMON', 'UNCOMMON', 'RARE', 'EPIC', 'LEGENDARY', 'MYTHIC']
const LABELS = { rarity: 'Rarity', stars: 'Stars', reforge: 'Reforge', recomb: 'Recombobulated', hpb: 'Hot Potato Books', ghpb: 'Golden Hot Potato Books', aow: 'Art of War', sync: 'Final Synchronizers', enrich: 'Enrichment', dye: 'Dye', skin: 'Skin', pet_level: 'Pet Level', pet_item: 'Pet Item', pet_skin: 'Pet Skin', engine: 'Engine', tank: 'Fuel Tank', module: 'Module' }
const query = () => Object.entries(item.filters).filter(([, v]) => v).map(([k, v]) => `&${k}=${url(v)}`).join('')

async function itemPage (entry, view) {
  item.entry = entry
  item.filters = {}
  item.range = view === 'active' ? 'active' : 'week'
  charts()      // start the download alongside the item's own data
  const d = await api(`item/${url(entry)}`)
  const e = d.estimate
  const rarity = TIER[d.tier]
  const hasFilters = Object.keys(d.filters || {}).length
  // A bazaar product's auction history is all from before it moved to the bazaar, so its page
  // shows the order book and the tape only: no listings, no AH sales line, no AH stats.
  item.bazaar = Boolean(d.bazaar)
  // A Tier Boost raises a pet's shown rarity but leaves its entry alone, so a boosted Epic is
  // still ender_dragon_pet_epic. The engine keeps the two apart; this page did not, and its chart
  // and sales averaged 230m plain Epics with 700m boosted ones. A pet page shows its own rarity
  // unless Tier Boosted is picked.
  const natural = ((/_pet_([a-z]+)$/.exec(entry) || [])[1] || '').toUpperCase()
  const boostedTier = PET_TIERS[PET_TIERS.indexOf(natural) + 1]
  const boostable = natural && boostedTier && (d.filters.pet_item || []).some(([v]) => /tier_boost/.test(v))
  if (natural) item.filters.rarity = natural
  if (item.bazaar && item.range === 'active') item.range = 'week'
  const fuzzy = e.exact || item.bazaar ? '' : '~'
  item.blank = !d.chart.length && !d.bazaar
  page.innerHTML = `
    <div class="item-head">${icon(d)}<h1 ${tierStyle(rarity)}>${esc(d.name)}</h1><button type="button" class="fav-toggle small" id="favToggle"></button><a class="btn btn-primary btn-sm" id="notifyBtn" href="#/notifier">Notify</a></div>
    ${d.litter.length ? `<div class="filter-row"><label>Rarity <select id="litter">${d.litter.map(l => `<option value="${esc(l.entry)}" ${l.entry === d.entry ? 'selected' : ''}>${esc((l.tier || '').toLowerCase().replace(/^./, c => c.toUpperCase()))}</option>`).join('')}</select></label></div>` : ''}
    ${boostable ? `<div class="filter-row"><label>Tier Boosted <select id="boost"><option value="">No</option><option value="yes">Yes, shows ${esc(boostedTier.toLowerCase().replace(/^./, c => c.toUpperCase()))}</option></select></label></div>` : ''}
    ${hasFilters ? `<div class="filter-row"><button class="add-filter" id="addFilter">+ Add Filter</button></div><div class="filter-row" id="filterBox" hidden>${Object.entries(d.filters).map(([k, values]) =>
      `<label>${LABELS[k]} <select data-filter="${k}"><option value="">Any</option>${values.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></label>`).join('')}</div>` : ''}
    ${item.blank ? '' : `<div class="toggle" id="ranges">${[...(item.bazaar ? [] : [['active', 'Active']]), ['day', '1 Day'], ['week', '1 Week'], ['month', '1 Month'], ['all', 'All Time']].map(([v, l]) => `<button data-range="${v}" class="${v === item.range ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div id="chartArea"><div class="chart" id="chart"></div><div class="avg" id="avg"></div></div>`}
    <div id="listsTop"></div>
    <div class="panel">
      <h3>${e.value ? `Worth about ${fuzzy}${coins(e.value)}` : 'No price yet'}${e.confidence === 'low' && e.value && !item.bazaar ? ` <span class="text-muted" style="font-weight:400;font-size:14px">${e.median_key === 'reported' ? 'rough, from reported trades' : e.stripped ? 'rough, upgraded copies with their parts taken off' : 'rough, few sales'}</span>` : ''}</h3>
      <div class="stats">${item.bazaar ? `
        <div><b>${d.bazaar.ask ? brief(d.bazaar.ask) : '-'}</b><span>Buy now</span></div>
        <div><b>${d.bazaar.bid ? brief(d.bazaar.bid) : '-'}</b><span>Sell now</span></div>
        <div><b>${e.bz_price ? brief(e.bz_price) : '-'}</b><span>Really trades at</span></div>` : `
        <div><b>${e.median ? fuzzy + brief(e.median) : '-'}</b><span>Median</span></div>
        <div><b>${e.lbin ? brief(e.lbin) : '-'}</b><span>Lowest BIN${e.lbin_live ? ` (${e.lbin_live} up)` : ''}</span></div>
        <div><b>${e.last_sale ? brief(e.last_sale.price) : '-'}</b><span>${e.last_sale ? `Last sold ${ago(e.last_sale.at)}` : 'Last sold'}</span></div>
        <div><b id="perDay">-</b><span>Sold per day</span></div>
        <div><b>${e.time_to_sell ? span(e.time_to_sell * 60000) : '-'}</b><span>Usual time to sell</span></div>
        <div><b>${e.insta_sell ? brief(Math.min(e.insta_sell, e.value || e.insta_sell)) : '-'}</b><span>Sells fast at</span></div>`}
        ${d.npc ? `<div><b>${brief(d.npc)}</b><span>NPC pays</span></div>` : ''}
      </div></div>
    ${d.bazaar ? '<div id="tape"></div>' : ''}
    ${d.reports.length ? `<div class="panel"><h3>Reported trades</h3>${d.reported ? `<p class="text-muted">${HOW[d.reported.how]}${d.reported.october ? ` Fetched about ${coins(d.reported.october)} in October.` : ''}</p>` : ''}${d.reports.map(receipt).join('')}</div>` : ''}
    ${d.recipes.length ? '<div id="craftBox"></div>' : ''}
    <div id="lists"></div>`
  page.querySelectorAll('[data-range]').forEach(b => b.addEventListener('click', () => {
    item.range = b.dataset.range
    page.querySelectorAll('[data-range]').forEach(x => x.classList.toggle('on', x === b))
    refreshItem()
  }))
  page.querySelector('#addFilter')?.addEventListener('click', () => { const box = page.querySelector('#filterBox'); box.hidden = !box.hidden })
  page.querySelector('#litter')?.addEventListener('change', e => { location.hash = `#/item/${url(e.target.value)}` })
  // Sold per day is the week's real count. The engine's volume is de-duplicated for COFL's maths:
  // it read 4.0 for Warden Catalyst beside "1,691 sold" that week, which looked like a bug.
  if (!item.bazaar) {
    api(`item/${url(entry)}/history?range=week`).then(h => {
      const n = (h.rows || []).reduce((t, r) => t + r.vol, 0) / 7
      const el = page.querySelector('#perDay')
      if (el) el.textContent = n ? (n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString('en-US')) : '-'
    }).catch(() => {})
  }
  page.querySelector('#boost')?.addEventListener('change', e => { item.filters.rarity = e.target.value ? boostedTier : natural; refreshItem() })
  page.querySelector('#notifyBtn').addEventListener('click', () => { notify.picked = { entry, name: d.name, filters: { ...item.filters } } })
  const star = page.querySelector('#favToggle')
  paintStar(star, isFavorite(entry))
  star.addEventListener('click', () => paintStar(star, toggleFavorite({ entry, name: d.name, icon: d.icon, bazaar: Boolean(d.bazaar) })))
  page.querySelectorAll('[data-filter]').forEach(s => s.addEventListener('change', () => { item.filters[s.dataset.filter] = s.value; refreshItem() }))
  if (d.bazaar) loadTape(entry)
  if (d.recipes.length) loadCraftBox(entry)
  await refreshItem()
}

async function loadCraftBox (entry) {
  const d = await api(`crafts/${url(entry)}`)
  const box = document.getElementById('craftBox')
  if (!box) return
  const verb = KIND[d.kind] || 'Craft'
  box.innerHTML = `<div class="panel"><h3>${verb}</h3>
    ${d.plan ? `<div class="stats">
      <div><b>${brief(d.cost)}</b><span>Cost to make</span></div>
      <div><b>${d.price ? brief(d.price) : '-'}</b><span>Sell for</span></div>
      <div><b>${d.price ? signed(d.profit) : '-'}</b><span>Profit after the fee</span></div>
      ${d.hours ? `<div><b>${span(d.hours * 3600000)}</b><span>Forge time</span></div>` : ''}
    </div>${d.estimated ? '<p class="mb-0 mt-2 text-muted">Nobody is selling some parts and they cannot be made, so they use their last sale or our estimate.</p>' : ''}<p class="mb-0 mt-2"><a class="link" href="#/craft/${url(entry)}">What to buy and where</a></p>`
    : `<p class="mb-0">${d.short.length ? `Can't be made right now, nobody is selling ${esc(d.short.join(', '))}.` : 'Not all of this recipe is known.'}</p><p class="mb-0 text-muted">${esc((d.recipes[0] || {}).text || '')}</p>`}</div>`
}

async function loadTape (entry) {
  const d = await api(`bazaar/${url(entry)}`)
  const box = document.getElementById('tape')
  if (!box) return
  const PROOF = { exact: 'Confirmed', serial: 'Filled part way', likely: 'Likely', unseen: 'Price unseen' }
  box.innerHTML = `<div class="panel"><h3>Bazaar trades</h3>${d.tape.length ? `<div style="overflow-x:auto;max-height:320px"><table class="table"><tr><th>When</th><th>Filled</th><th class="num">Amount</th><th class="num">Price</th><th>How we know</th></tr>
    ${d.tape.map(t => `<tr><td>${ago(t.at)}</td><td class="${t.side === 'buy' ? 'lime' : t.side === 'sell' ? 'red' : ''}">${t.side === 'buy' ? 'Sell offer' : t.side === 'sell' ? 'Buy order' : '-'}</td><td class="num">${num(t.amount)}</td><td class="num">${t.price ? num(t.price) : '-'}</td><td>${PROOF[t.proof] || t.proof}</td></tr>`).join('')}
  </table></div>` : '<p class="mb-0 text-muted">Recording started today. Trades show up here as they happen.</p>'}</div>`
}

// The chart library is 326 KB, most of every page's weight, and only item pages draw a chart, so it
// is fetched the first time one opens instead of blocking every page. A failed load resolves too:
// drawChart then says the chart did not load rather than leaving the page waiting.
let chartLib
function charts () {
  chartLib = chartLib || new Promise(resolve => {
    const tag = document.createElement('script')
    tag.src = 'https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js'
    tag.integrity = 'sha384-Mx5lkUEQPM1pOJCwFtUICyX45KNojXbkWdYhkKUKsbv391mavbfoAmONbzkgYPzR'
    tag.crossOrigin = 'anonymous'
    tag.onload = tag.onerror = () => resolve()
    document.head.append(tag)
  })
  return chartLib
}

function drawChart (h) {
  const box = document.getElementById('chart')
  const avg = document.getElementById('avg')
  if (typeof echarts === 'undefined') { box.innerHTML = '<p class="text-muted">The chart library did not load.</p>'; return }
  const chart = echarts.getInstanceByDom(box) || echarts.init(box)
  const stamp = t => new Date(+t).toLocaleString('en-US', item.range === 'day' ? { hour: 'numeric', minute: '2-digit' } : { month: 'numeric', day: 'numeric', year: '2-digit' })
  // The hover label and the tooltip's heading print the raw category, a millisecond timestamp,
  // unless the axis pointer formats it. Day and week points are hours apart, so they get a time.
  const when = t => new Date(+t).toLocaleString('en-US', item.range === 'day' || item.range === 'week'
    ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' } : { month: 'short', day: 'numeric', year: 'numeric' })
  const asCoins = v => v === null || v === undefined || v === '-' ? '-' : coins(v)
  const phone = innerWidth < 760     // full coin labels need 120px, which is a third of a phone
  const axis = rows => {
    // Trolls would squash everything else, so the scale follows the typical prices.
    const tops = rows.slice().sort((a, b) => a - b)
    const cap = tops[Math.floor(tops.length * 0.9)] || tops[tops.length - 1] || 0
    return Math.max(cap * 1.25, 1)
  }
  // A line needs two points, and a thin item often has one sale in the range.
  const line = (name, colour, data, width) => ({ name, type: 'line', color: colour, symbol: data.length < 4 ? 'circle' : 'none', symbolSize: 8, yAxisIndex: 0, smooth: true, lineStyle: { width: width || 2 }, tooltip: { valueFormatter: asCoins }, data })
  const bars = (data) => ({ name: 'Volume', type: 'bar', color: '#3d3748', yAxisIndex: 1, data })
  let times; let series; let names; let selected
  if (h.bazaar) {
    times = h.book.map(r => r.t)
    series = [line('Buy', '#b9a2ec', h.book.map(r => r.ask), 4), line('Sell', '#228B22', h.book.map(r => r.bid)), bars(h.book.map(r => r.vol))]
    names = ['Buy', 'Sell', 'Volume']
    selected = { Buy: true, Sell: true, Volume: true }
  } else {
    times = h.rows.map(r => r.t)
    series = [line('Price', '#b9a2ec', h.rows.map(r => r.avg), 4), line('Min', '#228B22', h.rows.map(r => r.min)), line('Max', '#B22222', h.rows.map(r => r.max)), bars(h.rows.map(r => r.vol))]
    names = ['Price', 'Min', 'Max', 'Volume']
    selected = { Price: true, Min: true, Max: false, Volume: false }
  }
  const values = h.bazaar ? h.book.map(r => r.ask).filter(Boolean) : h.rows.map(r => r.avg)
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1f1c26', borderColor: '#3d3748', textStyle: { color: '#ece9f1' },
      axisPointer: { type: 'cross', crossStyle: { color: '#8a8495' }, label: { backgroundColor: '#3d3748', color: '#ece9f1' } } },
    legend: { top: 0, data: names, selected, icon: 'roundRect', inactiveColor: '#545454', textStyle: { color: 'rgb(255, 255, 255)' } },
    grid: { left: phone ? 54 : 120, right: phone ? 40 : 70, top: 44, bottom: 80 },
    dataZoom: [{ type: 'slider', xAxisIndex: [0], filterMode: 'filter', labelFormatter: (i, v) => stamp(v), backgroundColor: '#1f1c26', borderColor: '#2d2836', fillerColor: 'rgba(154, 127, 209, .22)', handleStyle: { color: '#9a7fd1', borderColor: '#9a7fd1' }, moveHandleStyle: { color: '#9a7fd1' }, dataBackground: { lineStyle: { color: '#9a7fd1', opacity: .5 }, areaStyle: { color: 'rgba(154, 127, 209, .12)' } }, selectedDataBackground: { lineStyle: { color: '#b9a2ec' }, areaStyle: { color: 'rgba(185, 162, 236, .2)' } }, textStyle: { color: '#a39dae' } }],
    xAxis: [{ type: 'category', data: times, axisPointer: { type: 'shadow', label: { formatter: p => when(p.value) } }, axisLabel: { formatter: stamp, color: '#ccc' }, axisLine: { lineStyle: { color: '#777' } } }],
    yAxis: [{ type: 'value', name: 'Price', position: 'left', max: axis(values), axisPointer: { label: { formatter: p => phone ? brief(p.value) : coins(Math.round(p.value)) } }, nameTextStyle: { color: '#aaa' }, axisLabel: { formatter: v => phone ? brief(v) : coins(v), color: '#aaa' }, splitLine: { lineStyle: { color: '#2d2836' } } },
      { type: 'value', name: 'Volume', position: 'right', nameTextStyle: { color: '#aaa' }, axisLabel: { color: '#aaa' }, splitLine: { show: false } }],
    series
  }, true)
  const list = h.rows.length && !h.bazaar ? h.rows : []
  const sold = list.reduce((t, r) => t + r.vol, 0)
  const average = sold ? list.reduce((t, r) => t + r.avg * r.vol, 0) / sold : 0
  avg.innerHTML = sold ? `<b>Avg Price:</b> ${coins(average)} over ${num(sold)} sold` : (h.bazaar ? '' : times.length ? '' : 'No sales in this range. Try a wider one.')
  chart.resize()
}

async function refreshItem () {
  const area = document.getElementById('chartArea')
  const lists = document.getElementById('lists')
  const seq = (item.seq = (item.seq || 0) + 1)
  // On Active the listings are the point, so they come up above the stats.
  if (item.range === 'active') document.getElementById('listsTop').after(lists)
  else page.append(lists)
  if (item.blank) {
    // Nothing has sold on the AH, so there is no chart to draw.
  } else if (item.range === 'active') {
    area.hidden = true
  } else {
    area.hidden = false
    const [h] = await Promise.all([api(`item/${url(item.entry)}/history?range=${item.range}${query()}`), charts()])
    if (seq !== item.seq) return
    drawChart(h)
  }
  if (item.bazaar) { lists.innerHTML = ''; return }
  const [sold, active] = await Promise.all([api(`item/${url(item.entry)}/auctions?type=sold${query()}`), api(`item/${url(item.entry)}/auctions?type=active${query()}`)])
  if (seq !== item.seq) return
  const mini = (a, line, whoName) => `<a class="mini" href="#/auction/${url(a.id)}">
    <div class="top"><span>${num(a.unit * a.count)} Coins</span></div>
    <div class="body"><div class="who">${head(whoName)}<span>${esc(whoName)}</span></div>
      <div class="when" ${tierStyle(a.rarity)}>${esc(a.label)}${a.count > 1 ? ` x${a.count}` : ''}</div>
      ${a.parts.length ? `<div class="when text-muted" style="font-size:13px">${esc(a.parts.join(', '))}</div>` : ''}
      <div class="when">${line}</div></div></a>`
  lists.innerHTML = `
    <h2 class="section">Active auctions</h2>
    <div class="grid-4">${active.auctions.map(a => mini(a, a.end > Date.now() ? `ends in ${span(a.end - Date.now())}` : 'outcome unknown', a.seller)).join('') || '<p class="text-muted">Nothing listed.</p>'}</div>
    <h2 class="section">Recent sales</h2>
    <div class="grid-4">${sold.auctions.slice(0, 24).map(a => mini(a, `sold ${ago(a.at)}`, a.buyer || a.seller)).join('') || '<p class="text-muted">No sales on record.</p>'}</div>`
}

// ---------------------------------------------------------------- auction page

const AUCTION_STATUS = {
  sold: ['bg-success', 'Sold'],
  active: ['bg-primary', 'Active'],
  expired: ['bg-secondary', 'Expired'],
  cancelled: ['bg-secondary', 'Cancelled'],
  unobserved: ['bg-secondary', 'Outcome unknown']
}
const auctionTimeLabel = status => ({ sold: 'Sold', active: 'Ends', expired: 'Expired' })[status] || 'Last seen'

async function auction (id) {
  const d = await api(`auction/${url(id)}`)
  const [cls, label] = AUCTION_STATUS[d.status] || AUCTION_STATUS.unobserved
  const e = d.estimate
  page.innerHTML = `
    <div class="item-head"><a class="item-title" href="#/item/${url(d.entry)}">${icon(d)}<h1 class="mono" ${tierStyle(d.rarity)}>${esc(d.label)}${d.count > 1 ? ` x${d.count}` : ''}</h1></a><span class="badge ${cls}">${label}</span>${d.bin ? '<span class="badge bg-success">BIN</span>' : ''}</div>
    <div class="panel"><div class="stats">
      <div><b>${num(d.price)}</b><span>${d.status === 'sold' ? 'Sold for' : 'Price'}</span></div>
      <div><b>${e.value ? brief(e.value) : '-'}</b><span>Worth now</span></div>
      <div><b>${playerLink(d.seller)}</b><span>Seller</span></div>
      <div><b>${d.buyer ? playerLink(d.buyer) : '-'}</b><span>Buyer</span></div>
      <div><b>${when(d.start)}</b><span>Started</span></div>
      <div><b>${when(d.end)}</b><span>${auctionTimeLabel(d.status)}</span></div>
    </div></div>
    ${d.reforge || d.parts.length ? `<div class="panel"><h3>On this item</h3><p class="mb-0">${esc([d.reforge ? d.reforge[0].toUpperCase() + d.reforge.slice(1) + ' reforge' : '', ...d.parts].filter(Boolean).join(', '))}</p></div>` : ''}
    ${e.shares ? `<div class="panel"><h3>Value breakdown</h3><table class="table"><tr><td>Bare item</td><td class="num">${coins(e.bare)}</td></tr>
      ${e.shares.map(s => `<tr><td>${esc(s.label)} <span class="text-muted">${Math.round(s.share * 100)}% of ${brief(s.coins)}</span></td><td class="num">+${coins(s.coins * s.share)}</td></tr>`).join('')}</table></div>` : ''}
    ${d.flips.length ? `<h2 class="section">Flips</h2>${d.flips.map(f => flipCard(f, true)).join('')}` : ''}`
}

// ---------------------------------------------------------------- players

const player = { name: null, tab: 'auctions' }
async function playerPage (name) {
  player.name = name
  page.innerHTML = `
    <div class="item-head">${head(name)}<h1 style="font-size:1.1rem">${esc(name)}</h1>
      ${!linkedSite || name.toLowerCase() === state.player.toLowerCase() ? `<a class="btn btn-info btn-sm" href="#/player/${url(name)}/flips">Check tracked flips</a>` : ''}
</div>
    <div class="toggle"><button data-tab="auctions" class="on">Auctions</button><button data-tab="bids">Bids</button></div>
    <div id="cards" class="grid-2"><p class="text-muted">Loading Data...</p></div>`
  page.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
    page.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('on', x === b))
    loadCards(b.dataset.tab)
  }))
  await loadCards('auctions')
}
async function loadCards (tab) {
  const name = player.name
  const d = await api(`player/${url(name)}/${tab}`)
  const box = document.getElementById('cards')
  if (!box || player.name !== name) return
  box.innerHTML = d.auctions.map(a => {
    const [cls, label] = AUCTION_STATUS[a.status] || AUCTION_STATUS.unobserved
    const total = a.unit * a.count
    return `<a class="auction" href="#/auction/${url(a.id)}">
      <h4>${icon(a)}<span ${tierStyle(a.rarity)}>${esc(a.name)}${a.count > 1 ? ` x${a.count}` : ''}</span><span class="badge ${cls}">${label}</span>${a.bin ? '<span class="badge bg-success">BIN</span>' : ''}</h4>
      ${a.parts.length ? `<p class="parts">${esc(a.parts.join(', '))}</p>` : ''}
      <p>Highest Bid: ${num(a.status === 'sold' ? total : 0)}</p>
      <p>Starting Bid: ${num(total)}</p>
      ${tab === 'bids' ? `<p>Seller: ${esc(a.seller)}</p>` : a.buyer ? `<p>Buyer: ${esc(a.buyer)}</p>` : ''}
      <p>${auctionTimeLabel(a.status)}: ${when(a.end)}</p></a>`
  }).join('') || `<p class="text-muted">No ${tab} on record.</p>`
}

// ---------------------------------------------------------------- tracked flips

const FLAGS = {
  via_trade: 'Bought in a trade.',
  multi_item_trade: 'Came in a bundle, so its share of the money is worked out from what each item was worth.',
  uncertain_cost: 'Part of this cost is an estimate.',
  unknown_cost: 'It came free, so this profit is left out of the total.',
  changed: 'The item was changed between buying and selling.',
  craft: 'Made from things bought.',
  same_partner: 'Sold back to the same player it came from.'
}
const VIA = { ah: 'Auction', trade: 'Trade', gift: 'Free', craft: 'Crafted' }

function flipCard (f, who) {
  const free = f.flags.includes('unknown_cost')
  const buy = f.buy.via === 'ah' && f.buy.ref ? ['a', `href="#/auction/${url(f.buy.ref)}"`] : ['div', '']
  const sell = f.sell.via === 'ah' && f.sell.ref ? ['a', `href="#/auction/${url(f.sell.ref)}"`] : ['div', '']
  const finder = f.buy.finder === 'gap' ? 'Deal' : f.buy.via === 'trade' ? 'Trade' : f.buy.via === 'craft' ? 'Craft' : 'Manual'
  // A craft has no purchase, so what it cost is the parts listed under it.
  const made = f.buy.via === 'craft' ? -f.changes.filter(c => c.amount < 0 && !/Listing fee|did not sell/.test(c.label)).reduce((t, c) => t + c.amount, 0) : 0
  const rows = f.changes.map(c => `<tr><td>${esc(c.label)}${c.source === 'bought' ? ' <span class="text-muted">what was really paid</span>' : ''}</td><td class="num">${signed(c.amount)}</td></tr>`).join('')
  const why = f.flags.map(flag => FLAGS[flag]).filter(Boolean).join(' ')
  return `<div class="flip">
    <div class="flip-title"><a class="it" href="#/item/${url(f.entry)}">${icon(f)}<span ${tierStyle(f.rarity)}>${esc(f.name)}${f.count > 1 ? ` x${f.count}` : ''}</span></a>
      ${free ? '<span class="text-muted">Not counted</span>' : signed(f.profit)}</div>
    <hr>
    <div class="flip-prices">
      <${buy[0]} class="price-box" ${buy[1]}>${f.buy.via === 'craft' ? coins(made) : coins(f.buy.price)}<small>${VIA[f.buy.via] || ''}${f.buy.from ? ` from ${esc(f.buy.from)}` : ''}</small></${buy[0]}>
      <svg viewBox="0 0 24 24" width="44" height="44" fill="currentColor"><path d="M10 17l5-5-5-5v10z"/></svg>
      <${sell[0]} class="price-box" ${sell[1]}>${coins(f.sell.price)}<small>${VIA[f.sell.via] || ''}${f.sell.to ? ` to ${esc(f.sell.to)}` : ''}</small></${sell[0]}>
    </div>
    <p class="flip-meta"><span>Finder: <span class="badge bg-dark">${finder}</span></span><span>Sold ${ago(f.sell.at)}</span>${f.hold !== null && f.hold !== undefined ? `<span>Held ${span(f.hold)}</span>` : ''}${f.roi !== null && f.roi !== undefined ? `<span>${f.roi >= 0 ? '+' : ''}${Math.round(f.roi * 100)}%</span>` : ''}${who ? `<span>${playerLink(f.player)}</span>` : ''}</p>
    ${rows ? `<p class="mb-1">Profit changes:</p><table class="table">${rows}</table>` : ''}
    ${why ? `<div class="why">${esc(why)}</div>` : ''}
  </div>`
}

const track = { days: '0', sort: 'time', flag: '' }
async function flipsPage (name) {
  const q = track
  const d = await api(`flips?player=${url(name)}&days=${q.days}&sort=${q.sort}&flag=${q.flag}&limit=200`)
  page.innerHTML = `
    <div class="item-head">Tracked flips of: ${head(name)}<a href="#/player/${url(name)}"><b>${esc(name)}</b></a></div>
    <div class="flip-total"><span>Total Profit: <span class="gold">${coins(d.profit)}</span></span></div>
    <div class="controls">
      <label>Sort: <select id="sort">${[['time', 'Time'], ['profit', 'Profit (highest => lowest)'], ['loss', 'Profit (lowest => highest)'], ['price', 'Sell price'], ['percent', 'Profit%']].map(([v, l]) => `<option value="${v}" ${q.sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label>Show: <select id="flag">${[['', 'All flips'], ['trades', 'Only trades'], ['crafts', 'Only crafts'], ['changed', 'Upgraded or crafted'], ['sure', 'Exact costs only']].map(([v, l]) => `<option value="${v}" ${q.flag === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label>Range: <select id="days">${[['1', '24 hours'], ['7', '7 days'], ['30', '30 days'], ['0', 'All time']].map(([v, l]) => `<option value="${v}" ${q.days === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    </div>
    <p class="text-muted">${d.counted ? `${d.counted} flip${d.counted === 1 ? '' : 's'}, ${Math.round(d.wins / d.counted * 100)}% made money, ${coins(d.volume)} sold.` : 'No flips in this range.'}${d.unmatched ? ` ${d.unmatched} other sale${d.unmatched === 1 ? '' : 's'} had no purchase on record, like things that were farmed.` : ''}</p>
    ${d.flips.map(f => flipCard(f)).join('')}
    <p class="text-muted" style="font-size:14px">Tracks auction flips for everyone, and trades too for your own account through the mod. Upgrades, crafts, listing fees and relists are all taken out of the profit.</p>`
  for (const id of ['sort', 'flag', 'days']) document.getElementById(id).addEventListener('change', e => { q[id] = e.target.value; flipsPage(name) })
}

// ---------------------------------------------------------------- mod

// The Mod page: what it does, how to install it, its commands and one Download button. The jar is
// the one in site/, the same file the link page offers, so both always hand out the same build.
const MOD_COMMANDS = [
  ['/dsm', 'Shows whether you are linked.'],
  ['/dsm link', 'Links your Minecraft account to the site.'],
  ['/dsm site', 'Opens the site.'],
  ['/dsm alerts on|off', 'Turns deal and Notifier alerts in chat on or off.']
]
function modPage () {
  page.innerHTML = `
    <h1 class="page">DSM Mod</h1>
    <div class="panel mod">
      <p>Prices on every item you hover, and deal and Notifier alerts in chat, all inside Dragsim. Once you link your account, your trades go to your own flip history.</p>
      <h3>Installation</h3>
      <ol>
        <li>Install Fabric Loader 0.19.2 or newer and Fabric API for Minecraft 26.1.2. The game needs Java 25.</li>
        <li>Download the mod and put it in your <code>mods</code> folder.</li>
        <li>Join Dragsim, run <code>/dsm link</code> and enter the code on the <a class="link" href="/market/link/#/me">sign-in page</a>.</li>
      </ol>
      <h3>Commands</h3>
      <table class="table">${MOD_COMMANDS.map(([command, what]) => `<tr><td><code>${command}</code></td><td>${what}</td></tr>`).join('')}</table>
      <a class="btn btn-primary download" href="dragsim-market-client.jar" download>${svg('download', 20)}<span>Download</span></a>
    </div>`
}

// ---------------------------------------------------------------- signing in

// The public site's front door. The mod proves the Minecraft account to Mojang and shows a one-time code;
// entering it here sets this browser's cookie, and a reload brings the site up signed in, on this same page.
function signIn (reason = 'Link your Minecraft account to see your profile, your flips and your alerts.') {
  page.innerHTML = `
    <h1 class="page">Sign in</h1>
    <div class="panel mod">
      <p>${esc(reason)}</p>
      <ol>
        <li>Install the <a class="link" href="#/mod">DSM mod</a>.</li>
        <li>Join Dragsim and run <code>/dsm link</code>.</li>
        <li>Enter the code it shows you here. A code lasts five minutes.</li>
      </ol>
      <form id="linkForm" class="controls">
        <label for="linkCode">Link code</label>
        <input id="linkCode" type="text" size="12" maxlength="8" pattern="[0-9A-Fa-f]{8}" spellcheck="false" autocomplete="off" required>
        <button class="btn btn-primary" type="submit">Link account</button>
      </form>
      <p id="linkMessage" class="mb-0 text-muted" role="status"></p>
    </div>`
  const form = document.getElementById('linkForm')
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const button = form.querySelector('button')
    const message = document.getElementById('linkMessage')
    button.disabled = true
    message.textContent = 'Linking…'
    try {
      const res = await fetch(`${base}/api/pair`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: document.getElementById('linkCode').value.trim() }) })
      const result = await res.json()
      if (!res.ok) throw new Error(result.error || 'Linking failed')
      location.reload()
    } catch (error) {
      message.textContent = error.message
      button.disabled = false
    }
  })
}

// ---------------------------------------------------------------- search

const input = document.getElementById('q')
const results = document.getElementById('results')
const historyKey = () => linkedSite ? `linkedSearches:${state.player}` : 'lastSearches'
const HISTORY_SHOWN = 5     // five when you click in, a hundred remembered
const HISTORY_KEPT = 100
let searchSeq = 0

function history () {
  try { return JSON.parse(localStorage.getItem(historyKey())) || [] } catch { return [] }
}
function remember (row) {
  try { localStorage.setItem(historyKey(), JSON.stringify([row, ...history().filter(r => r.href !== row.href)].slice(0, HISTORY_KEPT))) } catch {}
}
function resultRow (row, previous) {
  return `<a href="${esc(row.href)}" class="${previous ? 'previous' : ''}" data-row="${esc(JSON.stringify(row))}">${row.player ? head(row.player) : icon(row)}<span ${previous ? '' : tierStyle(TIER[row.tier])}>${esc(row.name)}</span></a>`
}
function showHistory () {
  if (input.value.trim()) return
  searchSeq++
  results.innerHTML = history().slice(0, HISTORY_SHOWN).map(r => resultRow(r, true)).join('')
  results.hidden = !results.innerHTML
}
function pick (link) {
  remember(JSON.parse(link.dataset.row))
  results.hidden = true
  input.value = ''
}
input.addEventListener('click', showHistory)
input.addEventListener('focus', showHistory)
input.addEventListener('input', async () => {
  const seq = ++searchSeq
  const text = input.value.trim()
  if (!text) { showHistory(); return }
  if (text.length < 2) { results.hidden = true; return }
  const d = await api(`search?q=${url(text)}`)
  if (seq !== searchSeq) return
  const previous = history().filter(r => r.name.toLowerCase().includes(text.toLowerCase())).slice(0, 3)
  const shown = new Set(previous.map(r => r.href))
  const rows = [...d.items.map(i => ({ href: `#/item/${url(i.entry)}`, name: i.name, entry: i.entry, tier: i.tier, icon: i.icon })),
    ...d.players.map(p => ({ href: `#/player/${url(p)}`, name: p, player: p }))]
  results.innerHTML = previous.map(r => resultRow(r, true)).join('') + rows.filter(r => !shown.has(r.href)).map(r => resultRow(r)).join('')
  results.hidden = !results.innerHTML
})
input.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const first = results.querySelector('a')
    if (first) { location.hash = first.getAttribute('href'); pick(first) }
  }
  if (e.key === 'Escape') results.hidden = true
})
document.addEventListener('click', e => { if (!e.target.closest('.search')) results.hidden = true })
results.addEventListener('click', e => { const link = e.target.closest('a'); if (link) pick(link) })

// ---------------------------------------------------------------- router

const state = { player: '' }
async function route () {
  const parts = location.hash.replace(/^#\/?/, '').split('/')
  const [name, arg, sub] = [parts[0], decodeURIComponent(parts[1] || ''), parts[2]]
  // Tracked flips have no menu entry of their own: they open from a profile, so your own sit under My Profile.
  const nav = name === 'flips' || (name === 'player' && arg === state.player) ? 'me' : name
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === nav))
  document.getElementById('rail').classList.remove('open')
  window.scrollTo(0, 0)
  try {
    if (!state.ready) {
      const status = await api('status')
      state.player = status.tracked[0] || ''
      state.signedIn = status.signed_in !== false
      state.ready = true
      await emojiReady
      accountButton()
      foot(status)
      showAlerts(status.alerts)
    }
    if (name === '') await home()
    else if (name === 'craft') await craftPage(arg)
    else if (name === 'notifier') await (state.signedIn ? notifier() : signIn('Link your Minecraft account to get told when items hit your price.'))
    else if (name === 'bazaar') await bazaar()
    else if (name === 'me') await (state.signedIn ? playerPage(state.player) : signIn())
    else if (name === 'flips') await (state.signedIn ? flipsPage(state.player) : signIn())
    else if (name === 'item') await itemPage(arg, sub)
    else if (name === 'auction') await auction(arg)
    else if (name === 'player' && sub === 'flips' && (!linkedSite || arg.toLowerCase() === state.player.toLowerCase())) await flipsPage(linkedSite ? state.player : arg)
    else if (name === 'player') await playerPage(arg)
    else if (name === 'mod') modPage()
    else page.innerHTML = '<p class="text-muted">No such page.</p>'
  } catch (e) {
    if (e.status === 401 && linkedSite) return signIn('Link your Minecraft account to see this.')
    console.error(e)
    page.innerHTML = '<p class="text-muted">That did not load. Try again in a moment.</p>'
  }
}
function foot (s) {
  // The API gets one quiet link here rather than a page in the menu: the people who want it look
  // in a footer, and everyone else never needs to see it.
  const foot = document.getElementById('foot')
  foot.textContent = `Prices current to ${new Date(s.market_time).toLocaleString('en-US')}. ${s.listings.toLocaleString('en-US')} live listings. Sales since Sept 23. `
  foot.insertAdjacentHTML('beforeend', '<a class="link" href="/api">Free API</a>')
}
window.addEventListener('hashchange', route)
window.addEventListener('resize', () => { const box = document.getElementById('chart'); if (box && typeof echarts !== 'undefined') echarts.getInstanceByDom(box)?.resize() })
route()

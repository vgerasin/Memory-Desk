/* Memory Desk для телефона: только чтение. Весь текст из ответов выводится через textContent, разметка из данных не строится. */
(function () {
  'use strict'
  var root = document.getElementById('app')
  var S = {}, lang = 'ru', device = null, transcriptAllowed = false

  function t(key, arg) {
    var s = S[key] || key
    return arg === undefined ? s : s.replace('{0}', arg)
  }

  function h(tag, attrs) {
    var el = document.createElement(tag)
    for (var k in (attrs || {})) {
      if (k === 'text') el.textContent = attrs[k]
      else if (k === 'class') el.className = attrs[k]
      else if (k === 'css') Object.assign(el.style, attrs[k])
      else if (typeof attrs[k] === 'function') el.addEventListener(k.replace(/^on/, ''), attrs[k])
      else if (attrs[k] !== false && attrs[k] != null) el.setAttribute(k, attrs[k])
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i]
      if (c == null || c === false) continue
      el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c)
    }
    return el
  }

  function api(path, opts) {
    opts = opts || {}
    var init = { method: opts.method || 'GET', credentials: 'same-origin', headers: {} }
    if (init.method !== 'GET') {
      init.headers['X-Memory-Desk'] = '1'
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(opts.body || {})
    }
    return fetch(path, init).then(function (r) {
      return r.text().then(function (text) {
        var data = {}
        try { data = text ? JSON.parse(text) : {} } catch (e) { data = {} }
        if (!r.ok) { var err = new Error(data.detail || String(r.status)); err.status = r.status; err.retry = data.retryAfter || 0; throw err }
        return data
      })
    })
  }

  function day(iso) {
    if (!iso) return ''
    var d = new Date(iso + 'T12:00:00')
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString(lang, { day: 'numeric', month: 'short', weekday: 'short' })
  }
  function stamp(sec) {
    if (!sec) return ''
    var d = new Date(sec * 1000)
    return d.toLocaleDateString(lang, { day: 'numeric', month: 'long' })
  }
  function owner(o) { return !o ? t('noOwner') : (o === 'Я' || o === 'Вы') ? t('you') : o }

  // --- экраны
  function shell(active, body) {
    var tabs = [['today', 'tabToday'], ['records', 'tabRecords'], ['tasks', 'tabTasks'], ['people', 'tabPeople']]
    var nav = h('nav')
    tabs.forEach(function (x) {
      var a = h('a', { href: '#/' + x[0], text: t(x[1]) })
      if (active === x[0]) a.setAttribute('aria-current', 'page')
      nav.appendChild(a)
    })
    root.replaceChildren(h('main', {}, body), nav)
    window.scrollTo(0, 0)
  }

  function fail(e) {
    if (e && e.status === 401) { device = null; return login() }
    if (e && e.status === 403) return root.replaceChildren(h('main', {}, h('div', { class: 'center' }, h('p', { class: 'err', text: t('off') }))))
    root.replaceChildren(h('main', {}, h('div', { class: 'center' }, h('p', { class: 'err', text: t(e && e.status ? 'notFound' : 'network') }), h('button', { class: 'primary', text: t('retry'), onclick: route }))))
  }

  function login(message, retry) {
    var input = h('input', { type: 'password', inputmode: 'numeric', pattern: '[0-9]*', autocomplete: 'off', maxlength: '12', 'aria-label': t('pin'), placeholder: t('pin') })
    var err = h('div', { class: 'err', text: message || '' })
    var button = h('button', { class: 'primary', type: 'submit', text: t('signIn') })
    var timer = null
    function countdown(n) {
      clearInterval(timer)
      if (!n) { button.disabled = false; return }
      button.disabled = true
      timer = setInterval(function () {
        n -= 1
        if (n <= 0) { clearInterval(timer); button.disabled = false; err.textContent = ''; return }
        err.textContent = t('wait', n)
      }, 1000)
    }
    var form = h('form', { autocomplete: 'off' }, h('h1', { text: t('signInTitle') }), h('p', { class: 'meta', text: t('pinPrompt') }), input, err, button)
    form.addEventListener('submit', function (ev) {
      ev.preventDefault()
      button.disabled = true
      api('/api/login', { method: 'POST', body: { pin: input.value } }).then(function () { input.value = ''; return boot() }).catch(function (e) {
        input.value = ''
        if (e.status === 401) err.textContent = t('wrongPin') + (e.retry ? '. ' + t('wait', e.retry) : '')
        else if (e.status === 429) err.textContent = t('wait', e.retry)
        else if (e.status === 403) err.textContent = /выключен/.test(e.message) && /много/.test(e.message) ? t('locked') : t('off')
        else err.textContent = t('network')
        if (e.retry) countdown(e.retry); else button.disabled = false
      })
    })
    root.replaceChildren(h('main', {}, h('div', { class: 'center' }, form, h('div', { class: 'ro', text: t('readOnly') }))))
    if (retry) { err.textContent = t('wait', retry); countdown(retry) }
    input.focus()
  }

  function dot(color) { return h('span', { class: 'dot', css: { background: /^#[0-9a-fA-F]{3,8}$/.test(color || '') ? color : 'var(--line)' } }) }

  function recordCard(m) {
    var sub = [day(m.date), m.time, m.duration && m.duration.replace(/^00:/, '')].filter(Boolean).join(' · ')
    var names = (m.people || []).map(function (p) { return p.name }).join(', ')
    return h('a', { class: 'card', href: '#/records/' + encodeURIComponent(m.id) },
      h('div', { class: 't', text: (m.important ? '★ ' : '') + m.title }),
      h('div', { class: 'meta' }, m.project ? dot(m.project.color) : null, [m.project ? m.project.title : '', sub].filter(Boolean).join(' · ')),
      names ? h('div', { class: 'meta', text: names }) : null,
      m.excerpt ? h('div', { class: 'ex', text: m.excerpt }) : null)
  }

  function itemRow(i, withMeeting) {
    var bits = []
    if (i.owner !== undefined) bits.push(owner(i.owner))
    if (i.due) bits.push(t('due') + ' ' + day(i.due))
    var li = h('li', {}, h('div', { text: i.text }))
    var line = h('div', { class: 'meta', text: bits.join(' · ') })
    if (i.draft) line.insertBefore(h('span', { class: 'pill', text: t('draft') }), line.firstChild)
    if (i.done) line.insertBefore(h('span', { class: 'pill', text: t('done') }), line.firstChild)
    if (i.stuck) line.insertBefore(h('span', { class: 'pill warn', text: t('stuck') }), line.firstChild)
    li.appendChild(line)
    if (withMeeting && i.meetingId) li.appendChild(h('a', { class: 'meta', href: '#/records/' + encodeURIComponent(i.meetingId), text: (i.meetingTitle || '') + (i.meetingDate ? ' · ' + day(i.meetingDate) : '') }))
    return li
  }

  function section(title, list, build) {
    if (!list || !list.length) return null
    var ul = h('ul', { class: 'plain' })
    list.forEach(function (x) { ul.appendChild(build(x)) })
    return h('div', {}, h('h2', { text: title }), h('div', { class: 'card' }, ul))
  }

  function today() {
    return api('/api/today').then(function (d) {
      var body = h('div', {}, h('h1', { text: t('tabToday') }))
      var counts = []
      if (d.reviewCount) counts.push(t('toReview', d.reviewCount))
      if (d.drafts) counts.push(t('drafts', d.drafts))
      if (counts.length) body.appendChild(h('div', { class: 'meta', text: counts.join(' · ') }))
      if (d.hero) {
        var c = d.hero.columns || {}
        body.appendChild(h('h2', { text: t('lastRecord') }))
        body.appendChild(h('a', { class: 'card', href: d.hero.meetingId ? '#/records/' + encodeURIComponent(d.hero.meetingId) : '#/records' },
          h('div', { class: 't', text: d.hero.title || '' }), h('div', { class: 'meta', text: [day(d.hero.date), d.hero.time].filter(Boolean).join(' · ') })))
        ;[section(t('decisions'), c.decisions, function (i) { return itemRow(i) }),
          section(t('tasks'), c.tasks, function (i) { return itemRow(i) }),
          section(t('questions'), c.questions, function (q) { return h('li', { text: typeof q === 'string' ? q : q.text }) })]
          .forEach(function (x) { if (x) body.appendChild(x) })
      }
      if ((d.newRecords || []).length) {
        body.appendChild(h('h2', { text: t('newRecords') }))
        d.newRecords.forEach(function (m) { body.appendChild(recordCard(m)) })
      }
      if (!d.hero && !(d.newRecords || []).length) body.appendChild(h('p', { class: 'meta', text: t('empty') }))
      body.appendChild(h('button', { class: 'link', text: t('signOut'), onclick: logout }))
      body.appendChild(h('div', { class: 'ro', text: t('readOnly') }))
      shell('today', body)
    })
  }

  function records(query) {
    var q = query || ''
    var holder = h('div'), more = h('button', { class: 'link', text: t('more2'), css: { display: 'none' } })
    var offset = 0, PAGE = 30
    var search = h('input', { type: 'search', placeholder: t('search'), value: q, 'aria-label': t('search') })
    function load(reset) {
      if (reset) { offset = 0; holder.replaceChildren() }
      return api('/api/records?limit=' + PAGE + '&offset=' + offset + '&q=' + encodeURIComponent(q)).then(function (d) {
        d.items.forEach(function (m) { holder.appendChild(recordCard(m)) })
        offset += d.items.length
        more.style.display = offset < d.total ? '' : 'none'
        if (!d.total) holder.appendChild(h('p', { class: 'meta', text: t('empty') }))
      })
    }
    var timer = null
    search.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { q = search.value.trim(); load(true).catch(fail) }, 350) })
    more.addEventListener('click', function () { load(false).catch(fail) })
    shell('records', h('div', {}, h('h1', { text: t('tabRecords') }), search, h('div', { css: { height: '10px' } }), holder, more))
    return load(true)
  }

  function record(id) {
    return api('/api/records/' + encodeURIComponent(id)).then(function (m) {
      var body = h('div', {}, h('a', { class: 'back', href: '#/records', text: '‹ ' + t('back') }), h('h1', { text: m.title }),
        h('div', { class: 'meta' }, m.project ? dot(m.project.color) : null, [m.project ? m.project.title : t('noProject'), day(m.date), m.time].filter(Boolean).join(' · ')))
      var names = (m.people || []).map(function (p) { return p.name }).join(', ')
      if (names) body.appendChild(h('div', { class: 'meta', text: names }))
      ;[section(t('summary'), m.summary, function (x) { return h('li', { text: x }) }),
        section(t('decisions'), m.decisions, function (i) { return itemRow(i) }),
        section(t('tasks'), m.tasks, function (i) { return itemRow(i) }),
        section(t('questions'), m.questions, function (q) { return h('li', { text: typeof q === 'string' ? q : q.text }) }),
        section(t('risks'), m.risks, function (q) { return h('li', { text: typeof q === 'string' ? q : q.text }) }),
        section(t('nextSteps'), m.nextSteps, function (q) { return h('li', { text: typeof q === 'string' ? q : q.text }) })]
        .forEach(function (x) { if (x) body.appendChild(x) })
      if (m.transcript) {
        var box = h('div', { class: 'card' })
        m.transcript.forEach(function (row) {
          box.appendChild(h('div', { class: 'tr' }, h('span', { class: 'who', text: owner(row.speaker) }), h('span', { class: 'at', text: row.t || '' }), h('span', { class: 'tx', text: row.text })))
        })
        body.appendChild(h('h2', { text: t('transcript') }))
        body.appendChild(box)
      } else {
        body.appendChild(h('p', { class: 'meta', text: t('transcriptOff') }))
      }
      shell('records', body)
    })
  }

  function tasks(kind) {
    kind = kind === 'decisions' ? 'decisions' : 'tasks'
    return api('/api/tasks?kind=' + kind + '&limit=100').then(function (d) {
      var seg = h('div', { class: 'seg' })
      ;[['tasks', 'tasksTab'], ['decisions', 'decisionsTab']].forEach(function (x) {
        var a = h('a', { href: '#/tasks/' + x[0], text: t(x[1]) })
        if (x[0] === kind) a.setAttribute('aria-current', 'page')
        seg.appendChild(a)
      })
      var ul = h('ul', { class: 'plain' })
      d.items.forEach(function (i) { ul.appendChild(itemRow(i, true)) })
      shell('tasks', h('div', {}, h('h1', { text: t('tabTasks') }), seg, d.items.length ? h('div', { class: 'card' }, ul) : h('p', { class: 'meta', text: t('empty') })))
    })
  }

  function people() {
    return Promise.all([api('/api/people'), api('/api/projects')]).then(function (r) {
      var body = h('div', {}, h('h1', { text: t('tabPeople') }))
      r[0].items.forEach(function (p) {
        var meta = [p.role, p.organization, p.meetings ? t('meetings', p.meetings) : '', p.obligations ? t('obligations', p.obligations) : ''].filter(Boolean).join(' · ')
        body.appendChild(h('div', { class: 'card' }, h('div', { class: 't', text: p.name }), meta ? h('div', { class: 'meta', text: meta }) : null))
      })
      if (r[1].items.length) body.appendChild(h('h2', { text: t('projects') }))
      r[1].items.forEach(function (p) {
        var c = p.counts || {}
        var meta = [c.records != null ? t('tabRecords') + ': ' + c.records : '', c.openTasks != null ? t('tasks') + ': ' + c.openTasks : '', c.questions != null ? t('questions') + ': ' + c.questions : ''].filter(Boolean).join(' · ')
        body.appendChild(h('div', { class: 'card' }, h('div', { class: 't' }, dot(p.color), p.title), p.goal ? h('div', { class: 'ex', text: p.goal }) : null, meta ? h('div', { class: 'meta', text: meta }) : null))
      })
      shell('people', body)
    })
  }

  function logout() {
    api('/api/logout', { method: 'POST' }).catch(function () {}).then(function () { device = null; location.hash = ''; login() })
  }

  function route() {
    if (!device) return login()
    var parts = (location.hash || '#/today').replace(/^#\/?/, '').split('/')
    var page = parts[0] || 'today', arg = parts[1] ? decodeURIComponent(parts[1]) : ''
    var job = page === 'records' ? (arg ? record(arg) : records()) : page === 'tasks' ? tasks(arg) : page === 'people' ? people() : today()
    root.setAttribute('aria-busy', 'true')
    job.catch(fail).then(function () { root.removeAttribute('aria-busy') })
  }

  function boot() {
    return api('/api/session').then(function (s) {
      lang = ['ru', 'en', 'fr', 'de', 'es'].indexOf(s.lang) >= 0 ? s.lang : 'ru'
      document.documentElement.lang = lang
      return fetch('/strings.json').then(function (r) { return r.json() }).then(function (all) {
        S = all[lang] || all.ru
        if (!s.authenticated) { device = null; return login('', s.retryAfter) }
        device = s.device
        transcriptAllowed = !!s.transcript
        route()
      })
    }).catch(function (e) { S = S.network ? S : { network: 'Нет связи с компьютером' }; fail(e) })
  }

  window.addEventListener('hashchange', route)
  boot()
})()

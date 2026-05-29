/* app.js — FAQ RAG demo browser UI.
 *
 * Vanilla JavaScript, no framework. One form handler, three render functions
 * (answer panel, retrieved chunks panel, audit panel), one helper for opco
 * badges. Markdown rendering in the answer is intentionally light — we use
 * marked from CDN.
 */

const form = document.getElementById('ask-form');
const output = document.getElementById('output');
const submitBtn = document.getElementById('submit-btn');
const $q = document.getElementById('question');
const $opco = document.getElementById('opco');
const $locale = document.getElementById('locale');
const $buildInfo = document.getElementById('build-info');

// Fetch the build manifest once and render in the footer. Useful for spotting
// stale builds when a recent Contentful edit isn't reflected in retrieval.
fetch('/api/info').then((r) => r.ok ? r.json() : null).then((info) => {
  if (!info || !$buildInfo) return;
  const m = info.manifest;
  if (!m) {
    $buildInfo.textContent = 'Index not yet built — run python src/build_index.py';
    return;
  }
  const locales = Object.entries(m.chunks_by_locale || {})
    .map(([loc, n]) => `${loc}: ${n}`).join(' · ');
  $buildInfo.textContent = `Index built ${m.built_at} · ${m.total_chunks} chunks (${locales})`;
}).catch(() => {});

// Restore last-used opco / locale from sessionStorage so the headline filter
// state survives a page reload — useful when iterating on a single opco.
const savedOpco = sessionStorage.getItem('opco');
const savedLocale = sessionStorage.getItem('locale');
if (savedOpco) $opco.value = savedOpco;
if (savedLocale) $locale.value = savedLocale;

$opco.addEventListener('change', () => sessionStorage.setItem('opco', $opco.value));
$locale.addEventListener('change', () => sessionStorage.setItem('locale', $locale.value));

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const question = $q.value.trim();
  if (!question) return;

  submitBtn.disabled = true;
  submitBtn.textContent = 'Thinking…';
  output.innerHTML = '<div class="panel"><div class="loader">Retrieving…</div></div>';

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        opco: $opco.value,
        locale: $locale.value,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      renderError(data.error || 'Request failed');
      return;
    }
    render(data);
  } catch (err) {
    renderError(String(err));
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Ask';
  }
});

function renderError(message) {
  output.innerHTML = `<div class="panel error"><h2>Error</h2><div>${escapeHtml(message)}</div></div>`;
}

function render(data) {
  const parts = [];

  // Answer panel
  if (data.answer) {
    parts.push(`
      <section class="panel">
        <h2>Answer</h2>
        <div class="answer">${renderMarkdown(data.answer)}</div>
      </section>
    `);
  } else {
    parts.push(`
      <section class="panel error">
        <h2>No answer</h2>
        <div>No FAQ chunks were retrieved for this question${data.opco ? ` under OpCo <strong>${data.opco}</strong>` : ''}. Try widening the filter or rephrasing.</div>
      </section>
    `);
  }

  // Retrieved chunks panel — the proof of which content the model saw
  if (data.results && data.results.length) {
    const items = data.results.map((r) => `
      <li>
        <span class="rank">#${r.rank}</span>
        <div class="result-body">
          <div class="result-q">${escapeHtml(r.question || r.internal_name)}</div>
          <div class="result-id">${escapeHtml(r.internal_name)}</div>
          <div class="result-meta">
            ${(r.applicable_opcos || []).map(opcoBadge).join('')}
            <span class="badge badge-dist">dist ${r.distance.toFixed(3)}</span>
            ${r.hub_name ? `<span class="badge badge-dist">${escapeHtml(r.hub_name)} › ${escapeHtml(r.topic_name)}</span>` : ''}
            ${r.canonical_url ? `<a href="${escapeAttr(r.canonical_url)}" target="_blank" rel="noreferrer">↗ live</a>` : ''}
          </div>
        </div>
      </li>
    `).join('');
    parts.push(`
      <section class="panel">
        <h2>Retrieved chunks (${data.results.length})</h2>
        <ul class="results-list">${items}</ul>
      </section>
    `);
  }

  // Audit panel — citations + tokens + log path
  const meta = [];
  if (data.cited_sources && data.cited_sources.length) {
    meta.push(`<div><strong>Cited:</strong> ${data.cited_sources.map(escapeHtml).join(', ')}</div>`);
  }
  if (data.tokens) {
    const cost = (data.tokens.input * 1.0 / 1_000_000 + data.tokens.output * 5.0 / 1_000_000).toFixed(5);
    meta.push(`<div><strong>Tokens:</strong> ${data.tokens.input} in / ${data.tokens.output} out (~$${cost})</div>`);
  }
  meta.push(`<div><strong>k:</strong> ${data.k}${data.listing_detected ? ' (listing-bumped)' : ''}</div>`);
  meta.push(`<div><strong>Locale:</strong> ${data.locale}</div>`);
  meta.push(`<div><strong>OpCo:</strong> ${data.opco || '(none — no filter)'}</div>`);
  if (data.log_path) {
    meta.push(`<div><strong>Audit log:</strong> <code>${escapeHtml(data.log_path)}</code></div>`);
  }
  parts.push(`
    <section class="panel">
      <h2>Audit</h2>
      <div class="meta">${meta.join('')}</div>
    </section>
  `);

  output.innerHTML = parts.join('');
}

function opcoBadge(opco) {
  const labels = {
    'british-airways': ['BA', 'badge-ba'],
    'aer-lingus': ['Aer', 'badge-aer'],
    'iberia': ['IB', 'badge-iberia'],
  };
  const [label, cls] = labels[opco] || [opco, 'badge-dist'];
  return `<span class="badge ${cls}" title="${escapeAttr(opco)}">${label}</span>`;
}

/* Minimal markdown renderer — paragraphs, lists, links, headings, bold/italic.
 * Avoids the marked dep so we have no external request from the demo UI.
 * Good enough for short FAQ answers; if rendering goes weird, add marked.
 */
function renderMarkdown(md) {
  if (!md) return '';
  let html = escapeHtml(md);

  // Headings
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  // Bold + italic
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  // Bullet lists — group consecutive '- ' lines into a <ul>
  html = html.replace(/(?:^- .+(?:\n|$))+/gm, (block) => {
    const items = block.trim().split(/\n/).map((l) => `<li>${l.replace(/^-\s+/, '')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });
  // Numbered lists
  html = html.replace(/(?:^\d+\.\s.+(?:\n|$))+/gm, (block) => {
    const items = block.trim().split(/\n/).map((l) => `<li>${l.replace(/^\d+\.\s+/, '')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });
  return html;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }

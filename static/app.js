/* app.js — Multi-turn chat UI for the FAQ RAG demo.
 *
 * Stateless server, client-side conversation. The full message history is
 * sent with every request so the server can do query rewriting + pass
 * context to Claude. Reset button clears state.
 */

const $form = document.getElementById('ask-form');
const $convo = document.getElementById('conversation');
const $empty = document.getElementById('empty-state');
const $q = document.getElementById('question');
const $send = document.getElementById('send-btn');
const $reset = document.getElementById('reset-btn');
const $opco = document.getElementById('opco');
const $locale = document.getElementById('locale');
const $buildInfo = document.getElementById('build-info');

// Conversation state: array of {role: 'user'|'assistant', content: string,
// retrievedChunks?: [...], retrievalQuery?: string, wasRewritten?: boolean }
let history = [];

// Restore opco/locale across reloads — keeps the headline filter sticky
const savedOpco = sessionStorage.getItem('opco');
const savedLocale = sessionStorage.getItem('locale');
if (savedOpco) $opco.value = savedOpco;
if (savedLocale) $locale.value = savedLocale;
$opco.addEventListener('change', () => sessionStorage.setItem('opco', $opco.value));
$locale.addEventListener('change', () => sessionStorage.setItem('locale', $locale.value));

// Build info footer
fetch('/api/info').then((r) => r.ok ? r.json() : null).then((info) => {
  if (!info || !$buildInfo) return;
  const m = info.manifest;
  if (!m) {
    $buildInfo.textContent = 'Index not built';
    return;
  }
  const locales = Object.entries(m.chunks_by_locale || {}).map(([l, n]) => `${l}: ${n}`).join(' · ');
  $buildInfo.textContent = `Index ${m.built_at} · ${locales}`;
}).catch(() => {});

// Auto-grow textarea
$q.addEventListener('input', () => {
  $q.style.height = 'auto';
  $q.style.height = Math.min($q.scrollHeight, 120) + 'px';
});
// Enter to send, Shift+Enter for newline
$q.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $form.requestSubmit();
  }
});

$form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $q.value.trim();
  if (!text) return;

  // Append user turn immediately for snappy feedback
  history.push({ role: 'user', content: text });
  $q.value = '';
  $q.style.height = 'auto';
  renderConversation();

  // Insert a typing indicator
  const typingId = `typing-${Date.now()}`;
  appendTypingTurn(typingId);

  setLoading(true);
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: history.map((t) => ({ role: t.role, content: t.content })),
        opco: $opco.value,
        locale: $locale.value,
      }),
    });
    document.getElementById(typingId)?.remove();
    const data = await res.json();
    if (!res.ok) {
      appendErrorTurn(data.error || `Request failed (${res.status})`);
      // Drop the user turn we optimistically added so the next try is clean
      history.pop();
      renderConversation();
      return;
    }
    history.push({
      role: 'assistant',
      content: data.answer,
      retrievedChunks: data.results || [],
      retrievalQuery: data.retrieval_query,
      wasRewritten: data.was_rewritten,
      citedSources: data.cited_sources || [],
      tokens: data.tokens,
      logPath: data.log_path,
      opco: data.opco,
      locale: data.locale,
    });
    renderConversation();
  } catch (err) {
    document.getElementById(typingId)?.remove();
    appendErrorTurn(String(err));
    history.pop();
    renderConversation();
  } finally {
    setLoading(false);
    $q.focus();
  }
});

$reset.addEventListener('click', () => {
  if (history.length && !confirm('Start a new conversation? Current history will be cleared.')) return;
  history = [];
  renderConversation();
  $q.focus();
});

function setLoading(loading) {
  $send.disabled = loading;
  $send.textContent = loading ? 'Thinking…' : 'Send';
  $reset.disabled = loading || history.length === 0;
}

function appendTypingTurn(id) {
  if ($empty) $empty.remove();
  const node = document.createElement('div');
  node.className = 'turn assistant';
  node.id = id;
  node.innerHTML = `<div class="bubble"><div class="typing"><span></span><span></span><span></span></div></div>`;
  $convo.appendChild(node);
  scrollToBottom();
}

function appendErrorTurn(message) {
  const node = document.createElement('div');
  node.className = 'turn assistant';
  node.innerHTML = `<div class="bubble error-bubble">${escapeHtml(message)}</div>`;
  $convo.appendChild(node);
  scrollToBottom();
}

function renderConversation() {
  // Hide the empty state if we have content
  if (history.length > 0 && document.getElementById('empty-state')) {
    document.getElementById('empty-state').remove();
  }
  if (history.length === 0) {
    $convo.innerHTML = `
      <div class="empty-state" id="empty-state">
        <h2>Ask a question</h2>
        <p>Pick an OpCo above to apply the hard filter, then ask anything about the help centre. You can follow up — the assistant remembers the conversation.</p>
      </div>`;
    setLoading(false);
    return;
  }

  $convo.innerHTML = history.map((t) => {
    if (t.role === 'user') {
      return `<div class="turn user"><div class="bubble">${escapeHtml(t.content)}</div></div>`;
    }
    // assistant — bubble + per-turn collapsible sources/audit below
    let sourcesHtml = '';
    if (t.retrievedChunks && t.retrievedChunks.length) {
      const items = t.retrievedChunks.map((r) => `
        <div class="source-item">
          <div class="source-q">${escapeHtml(r.question || r.internal_name)}</div>
          <div class="source-id">${escapeHtml(r.internal_name)}</div>
          <div class="source-badges">
            ${(r.applicable_opcos || []).map(opcoBadge).join('')}
            <span class="badge badge-dist">dist ${r.distance.toFixed(2)}</span>
            ${r.canonical_url ? `<a href="${escapeAttr(r.canonical_url)}" target="_blank" rel="noreferrer">↗ live</a>` : ''}
          </div>
        </div>
      `).join('');
      const rewriteNote = t.wasRewritten
        ? `<div style="margin-bottom:8px; font-style:italic; color:var(--fg-tertiary)">Retrieval query rewritten: "${escapeHtml(t.retrievalQuery)}"</div>`
        : '';
      const cited = (t.citedSources && t.citedSources.length)
        ? t.citedSources.map((c) => `<code>${escapeHtml(c)}</code>`).join(' · ')
        : '<span style="color:var(--fg-tertiary)">none</span>';
      const tokens = t.tokens || { input: 0, output: 0 };
      const cost = (tokens.input * 1.0 / 1_000_000 + tokens.output * 5.0 / 1_000_000).toFixed(5);
      const auditInline = `
        <div style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border-tertiary); font-size:11px; color:var(--fg-tertiary)">
          <div><strong style="color:var(--fg-secondary)">Cited:</strong> ${cited}</div>
          <div style="margin-top:3px"><strong style="color:var(--fg-secondary)">Tokens:</strong> ${tokens.input} in · ${tokens.output} out · ~$${cost}</div>
        </div>`;
      sourcesHtml = `
        <details>
          <summary>${t.retrievedChunks.length} source${t.retrievedChunks.length === 1 ? '' : 's'}</summary>
          <div class="sources">${rewriteNote}${items}${auditInline}</div>
        </details>
      `;
    }
    return `
      <div>
        <div class="turn assistant"><div class="bubble">${renderMarkdown(t.content)}</div></div>
        <div class="turn-meta" style="justify-content:flex-start; margin-left:4px">${sourcesHtml}</div>
      </div>
    `;
  }).join('');

  setLoading(false);
  scrollToBottom();
}

function scrollToBottom() {
  // Defer so the DOM is fully painted before scrolling
  requestAnimationFrame(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  });
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

/* Minimal markdown — same as before */
function renderMarkdown(md) {
  if (!md) return '';
  let html = escapeHtml(md);
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  html = html.replace(/(?:^- .+(?:\n|$))+/gm, (block) => {
    const items = block.trim().split(/\n/).map((l) => `<li>${l.replace(/^-\s+/, '')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });
  html = html.replace(/(?:^\d+\.\s.+(?:\n|$))+/gm, (block) => {
    const items = block.trim().split(/\n/).map((l) => `<li>${l.replace(/^\d+\.\s+/, '')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });
  // Preserve paragraph breaks (double newlines → <br><br>, single → <br>)
  html = html.replace(/\n\n+/g, '<br><br>').replace(/(?<!>)\n(?!<)/g, '<br>');
  return html;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }

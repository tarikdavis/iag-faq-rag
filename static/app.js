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
      suggestedFollowups: data.suggested_followups || [],
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

// Delegated handler for the copy, follow-up, and new-topic chips. Delegated
// because renderConversation rebuilds the chip DOM on every turn —
// attaching listeners per-chip would mean re-binding on every render.
$convo.addEventListener('click', (e) => {
  const copyBtn = e.target.closest('.copy-answer-btn');
  if (copyBtn) {
    const idx = parseInt(copyBtn.dataset.turnIndex, 10);
    const turn = history[idx];
    if (turn && turn.content) {
      copyAnswerToClipboard(turn.content, copyBtn);
    }
    return;
  }
  const followupBtn = e.target.closest('[data-followup]');
  if (followupBtn) {
    // Fill the textarea and submit so the chip behaves like the user
    // typed the question. Preserves the multi-turn context (the prior
    // exchange feeds into query rewriting).
    $q.value = followupBtn.dataset.followup;
    $form.requestSubmit();
    return;
  }
  const newTopicBtn = e.target.closest('.new-topic-chip');
  if (newTopicBtn) {
    // No confirm dialog here — "switch topic" is a deliberate user
    // action with a fresh question almost certainly coming next, not
    // an accidental destructive click. The header Reset button keeps
    // its confirm dialog for the explicit "clear everything" case.
    history = [];
    renderConversation();
    $q.focus();
    return;
  }
});

// -----------------------------------------------------------------------
// Copy-to-clipboard helpers
//
// QA workflow: response → Excel cell. Excel pastes plain text into one
// cell with line breaks preserved. Markdown markers (** _ - [ ]) would
// be ugly noise in a spreadsheet, so we strip them. Links keep their
// URL in parentheses so QA can click through if needed.
// -----------------------------------------------------------------------

function markdownToPlainText(md) {
  let s = String(md || '');
  // Links: [text](url) → "text (url)"
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)');
  // Bold + italic — strip the markers
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1');
  s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '$1');
  // Headers — strip leading hashes, keep the text
  s = s.replace(/^#{1,6}\s+/gm, '');
  // Bullets — convert `- foo` to `• foo` (renders cleanly in Excel)
  s = s.replace(/^[-*]\s+/gm, '• ');
  // Inline code — strip backticks
  s = s.replace(/`([^`]+)`/g, '$1');
  return s.trim();
}

function copyAnswerToClipboard(markdownText, btn) {
  const plain = markdownToPlainText(markdownText);
  // navigator.clipboard requires a secure context (https or localhost).
  // The fallback path handles file:// or older browsers — rare for this
  // app but worth keeping the QA workflow working.
  const writePromise = navigator.clipboard && navigator.clipboard.writeText
    ? navigator.clipboard.writeText(plain)
    : new Promise((resolve, reject) => {
        try {
          const ta = document.createElement('textarea');
          ta.value = plain;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          resolve();
        } catch (err) { reject(err); }
      });

  writePromise.then(() => flashCopiedFeedback(btn))
              .catch((err) => console.error('Copy failed:', err));
}

function flashCopiedFeedback(btn) {
  const originalHtml = btn.innerHTML;
  const originalTitle = btn.getAttribute('title');
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>';
  btn.setAttribute('title', 'Copied!');
  btn.classList.add('copied');
  setTimeout(() => {
    btn.innerHTML = originalHtml;
    btn.setAttribute('title', originalTitle || 'Copy response to clipboard');
    btn.classList.remove('copied');
  }, 1500);
}

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
  scrollLatestIntoView();
}

function appendErrorTurn(message) {
  const node = document.createElement('div');
  node.className = 'turn assistant';
  node.innerHTML = `<div class="bubble error-bubble">${escapeHtml(message)}</div>`;
  $convo.appendChild(node);
  scrollLatestIntoView();
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

  $convo.innerHTML = history.map((t, i) => {
    if (t.role === 'user') {
      return `<div class="turn user"><div class="bubble">${escapeHtml(t.content)}</div></div>`;
    }
    // assistant — bubble + per-turn collapsible sources/audit below
    let sourcesHtml = '';
    if (t.retrievedChunks && t.retrievedChunks.length) {
      const items = t.retrievedChunks.map((r) => {
        const addl = (r.additional_topic_names || []).filter(Boolean);
        const addlLine = addl.length
          ? `<div style="font-size:11px;color:var(--fg-tertiary);margin-top:2px">also surfaces in: ${addl.map(escapeHtml).join(', ')}</div>`
          : '';
        return `
        <div class="source-item">
          <div class="source-q">${escapeHtml(r.question || r.internal_name)}</div>
          <div class="source-id">${escapeHtml(r.internal_name)}</div>
          ${addlLine}
          <div class="source-badges">
            ${sourceTypeBadge(r.source_type)}
            ${(r.applicable_opcos || []).map(opcoBadge).join('')}
            <span class="badge badge-dist">dist ${r.distance.toFixed(2)}</span>
            ${r.canonical_url ? `<a href="${escapeAttr(r.canonical_url)}" target="_blank" rel="noreferrer">↗ live</a>` : ''}
          </div>
        </div>
      `;
      }).join('');
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
    // Action row below the bubble: copy button + follow-up chips + new-topic.
    // Copy is leftmost (icon-only) because it's a meta action on this turn;
    // follow-ups continue the conversation; new-topic clears it. Visually
    // grouped, semantically distinct.
    let followupChipsHtml = '';
    const fups = t.suggestedFollowups || [];
    if (t.content) {
      const copyBtn = `<button type="button" class="copy-answer-btn" data-turn-index="${i}" title="Copy response to clipboard" aria-label="Copy response to clipboard">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      </button>`;
      const followupBtns = fups.map((f) =>
        `<button type="button" class="followup-chip" data-followup="${escapeAttr(f)}">${escapeHtml(f)}</button>`
      ).join('');
      const resetBtn = `<button type="button" class="followup-chip new-topic-chip">↻ Ask about something else</button>`;
      followupChipsHtml = `<div class="followup-chips">${copyBtn}${followupBtns}${resetBtn}</div>`;
    }
    return `
      <div>
        <div class="turn assistant"><div class="bubble">${renderMarkdown(t.content)}</div></div>
        ${followupChipsHtml}
        <div class="turn-meta" style="justify-content:flex-start; margin-left:4px">${sourcesHtml}</div>
      </div>
    `;
  }).join('');

  setLoading(false);
  scrollLatestIntoView();
}

function scrollLatestIntoView() {
  // Chat-app pattern: when a new turn arrives, anchor the most recent
  // user message near the TOP of the viewport (rather than scrolling the
  // page to its absolute bottom). That way the user's question + the
  // assistant response rendered below it are both visible in one scroll
  // position — they don't have to chase content downward as it appears.
  // Same behaviour as ChatGPT and claude.ai.
  //
  // Defer to the next frame so the DOM has painted before we measure.
  requestAnimationFrame(() => {
    const userTurns = $convo.querySelectorAll('.turn.user');
    const lastUserTurn = userTurns[userTurns.length - 1];
    if (lastUserTurn) {
      lastUserTurn.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      // No user message yet (e.g. fresh empty state) — fall back to a
      // true bottom-scroll so any banner/error is visible.
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    }
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

function sourceTypeBadge(sourceType) {
  // Differentiates FAQ chunks from inspiration sections + component banners in the
  // retrieved-sources panel. FAQs are the default; only render a badge for the others.
  if (!sourceType || sourceType === 'faq') return '';
  const labels = {
    'inspiration_section': ['Inspiration', '#5f3dc4'],
    'banner': ['Banner', '#0b7285'],
  };
  const [label, color] = labels[sourceType] || [sourceType, '#737177'];
  return `<span class="badge" style="background:${color}1a;color:${color}" title="${escapeAttr(sourceType)}">${label}</span>`;
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
  // Preserve paragraph breaks — but DON'T insert <br>s adjacent to block
  // elements we already rendered (<ul>, <ol>, <h3>, <h4>). Those have their
  // own margins; stacking <br><br> on top of them creates the gappy look
  // you see in screenshots when a paragraph leads into a bulleted list.
  // The lookbehind `(?<!>)` skips if a tag just closed, the lookahead
  // `(?!<)` skips if a tag is about to open.
  html = html
    .replace(/(?<!>)\n\n+(?!<)/g, '<br><br>')
    .replace(/(?<!>)\n(?!<)/g, '<br>');
  return html;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }

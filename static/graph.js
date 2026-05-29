/* graph.js — Render the FAQ relationship cluster using cytoscape.js.
 *
 * Loads /api/graph?locale=... once, builds the graph, lets the user
 * switch layout and dim by OpCo. Click a node for a detail panel.
 */

const HUB_PALETTE = [
  '#011dac', '#d52b1e', '#006a4e', '#f59e0b', '#9333ea',
  '#0891b2', '#dc2626', '#16a34a', '#7c3aed', '#ea580c', '#737177',
];

let cy = null;
let hubColors = {};

const $locale = document.getElementById('locale');
const $opco = document.getElementById('opco');
const $layout = document.getElementById('layout');
const $detail = document.getElementById('detail');
const $legend = document.getElementById('legend');
const $stats = document.getElementById('stats');

$locale.addEventListener('change', load);
$opco.addEventListener('change', applyOpcoDim);
$layout.addEventListener('change', applyLayout);

load();

async function load() {
  $detail.className = 'empty';
  $detail.textContent = 'Loading…';
  const res = await fetch(`/api/graph?locale=${encodeURIComponent($locale.value)}`);
  if (!res.ok) {
    $detail.textContent = `Failed to load graph: ${res.status}`;
    return;
  }
  const data = await res.json();
  buildHubColors(data.nodes);
  renderLegend();
  $stats.innerHTML = `<br>${data.node_count} FAQs, ${data.edge_count} explicit related-FAQ edges.`;
  buildCy(data);
  $detail.className = 'empty';
  $detail.textContent = 'Click any node to inspect it.';
}

function buildHubColors(nodes) {
  hubColors = {};
  const hubs = [...new Set(nodes.map((n) => n.data.hub_name))].sort();
  hubs.forEach((h, i) => {
    hubColors[h] = HUB_PALETTE[i % HUB_PALETTE.length];
  });
}

function renderLegend() {
  $legend.innerHTML = Object.entries(hubColors)
    .map(([hub, color]) => `
      <div class="legend-item">
        <span class="swatch" style="background:${color}"></span>${escapeHtml(hub)}
      </div>
    `)
    .join('');
}

function buildCy(data) {
  if (cy) cy.destroy();
  cy = cytoscape({
    container: document.getElementById('cy'),
    elements: [...data.nodes, ...data.edges],
    style: [
      {
        selector: 'node',
        style: {
          'background-color': (ele) => hubColors[ele.data('hub_name')] || '#737177',
          'label': 'data(label)',
          'color': '#000',
          'font-size': '9px',
          'font-family': 'Poppins, sans-serif',
          'text-wrap': 'wrap',
          'text-max-width': '80px',
          'text-valign': 'bottom',
          'text-margin-y': 4,
          'width': 16,
          'height': 16,
          'border-width': 1,
          'border-color': '#fff',
          'transition-property': 'opacity, background-color, width, height',
          'transition-duration': '180ms',
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-color': '#011dac',
          'border-width': 3,
          'width': 22,
          'height': 22,
          'font-size': '11px',
        },
      },
      {
        selector: 'node.dimmed',
        style: { 'opacity': 0.12 },
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'line-color': '#9593a0',
          'opacity': 0.5,
          'width': 1.5,
          'target-arrow-shape': 'triangle',
          'target-arrow-color': '#9593a0',
          'arrow-scale': 0.8,
        },
      },
      {
        selector: 'edge.dimmed',
        style: { 'opacity': 0.05 },
      },
    ],
    layout: { name: 'cose', animate: false, padding: 30, nodeRepulsion: 4500 },
  });

  cy.on('tap', 'node', (evt) => showDetail(evt.target.data()));
  cy.on('tap', (evt) => {
    if (evt.target === cy) {
      $detail.className = 'empty';
      $detail.textContent = 'Click any node to inspect it.';
    }
  });

  applyOpcoDim();
}

function applyLayout() {
  if (!cy) return;
  const name = $layout.value;
  const options = { name, animate: true, animationDuration: 400, padding: 30 };
  if (name === 'cose') options.nodeRepulsion = 4500;
  if (name === 'concentric') {
    options.concentric = (n) => {
      // Highest concentric ring = the hub with most FAQs. Crude but useful.
      return n.data('hub_name') ? Object.keys(hubColors).indexOf(n.data('hub_name')) : 0;
    };
    options.levelWidth = () => 1;
  }
  cy.layout(options).run();
}

function applyOpcoDim() {
  if (!cy) return;
  const opco = $opco.value;
  cy.elements().removeClass('dimmed');
  if (opco === 'all') return;
  cy.nodes().forEach((n) => {
    const opcos = n.data('applicable_opcos') || [];
    if (!opcos.includes(opco)) n.addClass('dimmed');
  });
  cy.edges().forEach((e) => {
    if (e.source().hasClass('dimmed') || e.target().hasClass('dimmed')) {
      e.addClass('dimmed');
    }
  });
}

function showDetail(d) {
  $detail.className = 'detail';
  const opcos = (d.applicable_opcos || []).join(', ') || '(none)';
  $detail.innerHTML = `
    <div class="detail-q">${escapeHtml(d.question || '(no question)')}</div>
    <div class="detail-id">${escapeHtml(d.internal_name)}</div>
    <div class="detail-row"><strong>Hub:</strong> ${escapeHtml(d.hub_name)}</div>
    <div class="detail-row"><strong>Topic:</strong> ${escapeHtml(d.topic_name)}</div>
    <div class="detail-row"><strong>OpCos:</strong> ${escapeHtml(opcos)}</div>
    ${d.short_answer ? `<div class="detail-row" style="margin-top:8px"><em>${escapeHtml(d.short_answer)}</em></div>` : ''}
    ${d.canonical_url ? `<div class="detail-row" style="margin-top:8px"><a href="${escapeAttr(d.canonical_url)}" target="_blank" rel="noreferrer">↗ open on avios.com</a></div>` : ''}
  `;
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

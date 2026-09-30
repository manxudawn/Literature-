const state = {
  data: null,
  filter: 'all',
  query: '',
  read: new Set(JSON.parse(localStorage.getItem('cs-read') || '[]')),
  deleted: new Set(JSON.parse(localStorage.getItem('cs-deleted') || '[]')),
  keywordConfig: null,
  repoKeywordConfig: null,
};

const topicLabels = {
  organic: '有机电催化 CO₂',
  gde: 'GDE / Micro-CT',
  analysis: '电化学分析',
};

const filterLabels = {
  all: '全部',
  organic: '有机电催化 CO₂',
  gde: 'GDE / Micro-CT',
  analysis: '电化学分析',
  recommended: '为你推荐',
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

function safeUrl(value) {
  try {
    const url = new URL(String(value || ''), window.location.href);
    if (['http:', 'https:'].includes(url.protocol)) return url.href;
  } catch (_) {}
  return '#';
}

function persist() {
  localStorage.setItem('cs-read', JSON.stringify([...state.read]));
  localStorage.setItem('cs-deleted', JSON.stringify([...state.deleted]));
}

const KEYWORD_STORAGE_KEY = 'cs-keywords-v1';

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function normalizeKeywordConfig(raw) {
  const base = {
    version: 1,
    updated_at: new Date().toISOString().slice(0, 10),
    topics: {},
  };
  ['organic', 'gde', 'analysis'].forEach((topic) => {
    const source = raw?.topics?.[topic] || {};
    const keywords = Array.isArray(source.keywords) ? source.keywords : [];
    const seen = new Set();
    base.topics[topic] = {
      label: source.label || topicLabels[topic],
      keywords: keywords
        .map((x) => String(x || '').trim())
        .filter((x) => x && !seen.has(x.toLowerCase()) && seen.add(x.toLowerCase())),
    };
  });
  return base;
}

function persistKeywords() {
  if (!state.keywordConfig) return;
  state.keywordConfig.updated_at = new Date().toISOString().slice(0, 10);
  localStorage.setItem(KEYWORD_STORAGE_KEY, JSON.stringify(state.keywordConfig));
}

async function loadKeywords() {
  let repoConfig;
  try {
    const response = await fetch(`data/keywords.json?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`keywords.json HTTP ${response.status}`);
    repoConfig = normalizeKeywordConfig(await response.json());
  } catch (err) {
    console.warn('Keyword config unavailable; using empty config.', err);
    repoConfig = normalizeKeywordConfig({});
  }
  state.repoKeywordConfig = repoConfig;

  try {
    const local = JSON.parse(localStorage.getItem(KEYWORD_STORAGE_KEY) || 'null');
    state.keywordConfig = local ? normalizeKeywordConfig(local) : cloneJson(repoConfig);
  } catch (_) {
    state.keywordConfig = cloneJson(repoConfig);
  }
}

function allKeywords() {
  if (!state.keywordConfig) return [];
  return Object.entries(state.keywordConfig.topics).flatMap(([topic, group]) =>
    group.keywords.map((keyword) => ({ topic, keyword }))
  );
}

function renderKeywordManager() {
  const holder = document.getElementById('keywordGroups');
  if (!holder || !state.keywordConfig) return;

  const groups = Object.entries(state.keywordConfig.topics).map(([topic, group]) => {
    const chips = group.keywords.map((keyword) => `
      <span class="keyword-chip" data-topic="${esc(topic)}" data-keyword="${esc(keyword)}">
        <button class="keyword-search" type="button" title="用此关键词筛选文献">${esc(keyword)}</button>
        <button class="keyword-remove" type="button" aria-label="删除 ${esc(keyword)}" title="删除">×</button>
      </span>`).join('');

    return `<div class="keyword-group">
      <div class="keyword-group-head">
        <b>${esc(group.label || topicLabels[topic] || topic)}</b>
        <span>${group.keywords.length}</span>
      </div>
      <div class="keyword-chips">${chips || '<span class="keyword-empty">暂无关键词</span>'}</div>
    </div>`;
  }).join('');

  holder.innerHTML = groups;
  const counter = document.getElementById('keywordCount');
  if (counter) counter.textContent = allKeywords().length;

  holder.querySelectorAll('.keyword-search').forEach((button) => {
    button.onclick = () => {
      const keyword = button.parentElement.dataset.keyword || '';
      state.query = keyword;
      const search = document.getElementById('searchInput');
      search.value = keyword;
      render();
      document.getElementById('today').scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });

  holder.querySelectorAll('.keyword-remove').forEach((button) => {
    button.onclick = () => {
      const chip = button.closest('.keyword-chip');
      const topic = chip.dataset.topic;
      const keyword = chip.dataset.keyword;
      const list = state.keywordConfig?.topics?.[topic]?.keywords || [];
      state.keywordConfig.topics[topic].keywords = list.filter(
        (x) => x.toLowerCase() !== keyword.toLowerCase()
      );
      persistKeywords();
      renderKeywordManager();
    };
  });
}

function addKeyword() {
  const input = document.getElementById('keywordInput');
  const topic = document.getElementById('keywordTopic').value;
  const keyword = String(input.value || '').trim().replace(/\s+/g, ' ');
  if (!keyword) return;
  if (keyword.length < 2) {
    input.setCustomValidity('关键词至少需要 2 个字符。');
    input.reportValidity();
    return;
  }
  input.setCustomValidity('');

  const list = state.keywordConfig.topics[topic].keywords;
  if (!list.some((x) => x.toLowerCase() === keyword.toLowerCase())) {
    list.push(keyword);
    persistKeywords();
    renderKeywordManager();
  }
  input.value = '';
  input.focus();
}

function exportKeywords() {
  const payload = cloneJson(state.keywordConfig);
  payload.updated_at = new Date().toISOString().slice(0, 10);
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'keywords.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function resetKeywords() {
  state.keywordConfig = cloneJson(state.repoKeywordConfig);
  localStorage.removeItem(KEYWORD_STORAGE_KEY);
  renderKeywordManager();
}

function detailBlock(p) {
  const methods = p.methods_summary
    ? `<div class="insight-block"><b>研究方法</b><p>${esc(p.methods_summary)}</p></div>`
    : '';
  const findings = p.key_findings
    ? `<div class="insight-block"><b>重点结论</b><p>${esc(p.key_findings)}</p></div>`
    : '';
  const relevance = `<div class="insight-block"><b>为什么与你相关</b><p>${esc(p.relevance_reason || '与当前追踪主题高度匹配。')}</p></div>`;
  return methods + findings + relevance;
}

function card(p, i) {
  const read = state.read.has(p.id);
  const url = safeUrl(p.url);
  const date = p.publication_date ? ` · ${esc(p.publication_date)}` : '';
  return `<article class="paper ${read ? 'read' : ''}" data-id="${esc(p.id)}">
    <div class="paper-index">${String(i + 1).padStart(2, '0')}</div>
    <div>
      <div class="paper-meta"><b>${esc(topicLabels[p.topic] || p.topic)}</b> • ${esc(p.badge || '精选研究')} • ${esc(p.read_minutes || 10)} min${date}</div>
      <h3><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)} ↗</a></h3>
      <div class="journal">${esc(p.journal)} ${esc(p.year || '')} <span>${p.impact_factor ? `IF ${esc(p.impact_factor)}` : 'IF —'} · ${esc(p.quartile || 'JCR —')}${p.metric_year ? ` · ${esc(p.metric_year)}` : ''}</span></div>
      <p class="paper-summary">${esc(p.summary_en || p.summary_zh || p.abstract || '暂无摘要')}</p>
      <div class="why">${detailBlock(p)}</div>
      <div class="tags">${(p.tags || []).map((x) => `<span class="tag">${esc(x)}</span>`).join('')}</div>
      <div class="actions">
        <button class="text-btn why-btn">查看研究方法与结论 →</button>
        <a class="text-btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开原文网页 ↗</a>
        <button class="text-btn read-btn">${read ? '↶ 标记未读' : '✓ 标记已读'}</button>
        <button class="text-btn delete-btn">删除</button>
      </div>
    </div>
    <div class="score"><strong>${esc(p.score ?? 0)}</strong><span>相关度</span></div>
  </article>`;
}

function filtered(papers) {
  return papers
    .filter((p) => !state.deleted.has(p.id))
    .filter((p) => {
      if (state.filter === 'all') return true;
      if (state.filter === 'recommended') return Number(p.score || 0) >= 88;
      return p.topic === state.filter;
    })
    .filter((p) => {
      if (!state.query) return true;
      const q = state.query.toLowerCase();
      return [
        p.title,
        p.summary_en,
        p.summary_zh,
        p.methods_summary,
        p.key_findings,
        p.relevance_reason,
        p.journal,
        ...(p.tags || []),
      ].join(' ').toLowerCase().includes(q);
    });
}

function render() {
  const d = state.data;
  const current = filtered(d.papers.filter((p) => !p.archive));
  const archive = filtered(d.papers.filter((p) => p.archive));

  document.getElementById('paperList').innerHTML = current.map(card).join('') ||
    '<p class="paper-summary empty-note">今天没有发现新的高相关论文。历史推送仍可在下方查看。</p>';
  document.getElementById('archiveList').innerHTML = archive.map(card).join('') ||
    '<p class="paper-summary">暂无历史文献。</p>';

  document.getElementById('archiveCount').textContent = d.papers.filter((p) => p.archive && !state.deleted.has(p.id)).length;
  document.getElementById('readCount').textContent = d.papers.filter((p) => p.archive && state.read.has(p.id) && !state.deleted.has(p.id)).length;
  bindCards();
}

function bindCards() {
  document.querySelectorAll('.paper').forEach((el) => {
    const id = el.dataset.id;
    el.querySelector('.why-btn').onclick = () => {
      el.classList.toggle('expanded');
      el.querySelector('.why-btn').textContent = el.classList.contains('expanded')
        ? '收起研究解读 ↑'
        : '查看研究方法与结论 →';
    };
    el.querySelector('.read-btn').onclick = () => {
      state.read.has(id) ? state.read.delete(id) : state.read.add(id);
      persist();
      render();
    };
    el.querySelector('.delete-btn').onclick = () => {
      state.deleted.add(id);
      persist();
      render();
    };
  });
}

async function init() {
  const [r] = await Promise.all([
    fetch(`data/papers.json?v=${Date.now()}`, { cache: 'no-store' }),
    loadKeywords(),
  ]);
  if (!r.ok) throw new Error(`papers.json HTTP ${r.status}`);
  state.data = await r.json();
  const d = state.data;
  const today = d.papers.filter((p) => !p.archive);
  const matchScore = Number.isFinite(Number(d.match_score)) ? Number(d.match_score) : 0;

  document.getElementById('briefDate').textContent = `${d.brief_date || ''} 研究简报`;
  document.getElementById('statToday').textContent = today.length;
  document.getElementById('statHigh').textContent = today.filter((p) => Number(p.score || 0) >= 88).length;
  document.getElementById('statMethods').textContent = today.filter((p) => p.topic === 'analysis').length;
  document.getElementById('matchScore').textContent = `${matchScore}%`;
  document.querySelector('.meter span').style.width = `${Math.max(0, Math.min(100, matchScore))}%`;
  document.getElementById('lastUpdated').textContent = `最后更新 ${d.last_updated || '—'}`;

  const filters = ['all', 'organic', 'gde', 'analysis', 'recommended'];
  document.getElementById('filters').innerHTML = filters.map((key) =>
    `<button class="filter ${key === 'all' ? 'active' : ''}" data-filter="${key}">${filterLabels[key]}${key === 'all' ? today.length : ''}</button>`
  ).join('');

  document.querySelectorAll('.filter').forEach((button) => {
    button.onclick = () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll('.filter').forEach((x) => x.classList.remove('active'));
      button.classList.add('active');
      render();
    };
  });

  document.getElementById('topicTracker').innerHTML = (d.trackers || []).map((t) =>
    `<div class="topic-row"><span>${esc(t.name)}</span><span>${esc(t.rate)}</span></div>`
  ).join('');

  renderKeywordManager();
  render();
}

document.getElementById('searchInput').addEventListener('input', (e) => {
  state.query = e.target.value;
  render();
});
document.getElementById('openSearch').onclick = () => document.getElementById('searchInput').focus();
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    document.getElementById('searchInput').focus();
  }
});
document.getElementById('settingsBtn').onclick = () => document.getElementById('settingsDialog').showModal();
document.getElementById('scrollKeywords').onclick = () =>
  document.getElementById('keywords').scrollIntoView({ behavior: 'smooth', block: 'start' });
document.getElementById('addKeyword').onclick = addKeyword;
document.getElementById('keywordInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    addKeyword();
  }
});
document.getElementById('exportKeywords').onclick = exportKeywords;
document.getElementById('resetKeywords').onclick = resetKeywords;

init().catch((err) => {
  console.error(err);
  document.getElementById('paperList').innerHTML = '<p class="paper-summary">数据加载失败，请稍后刷新页面。</p>';
});

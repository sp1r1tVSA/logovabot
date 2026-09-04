/**
 * web/js/ui.js
 * Comprehensive UI Components and Views Renderer for Logovo.bet (v2.0).
 */

import { store } from './store.js';
import { tgBridge } from './tg.js';

export const TEAM_LOGO_MAP = {
  'спортинг': 'sporting.png',
  'sporting': 'sporting.png',
  'копенгаген': 'copenhagen.png',
  'copenhagen': 'copenhagen.png',
  'ривер плейт': 'river_plate.png',
  'river plate': 'river_plate.png',
  'ривер': 'river_plate.png',
  'бока хуниорс': 'boca_juniors.png',
  'boca juniors': 'boca_juniors.png',
  'бока': 'boca_juniors.png',
  'бенфика': 'benfica.png',
  'benfica': 'benfica.png',
  'псв': 'psv.png',
  'psv': 'psv.png',
  'порту': 'porto.png',
  'porto': 'porto.png',
  'будё глимт': 'bodo_glimt.png',
  'будë глимт': 'bodo_glimt.png',
  'буде глимт': 'bodo_glimt.png',
  'bodo glimt': 'bodo_glimt.png',
  'bodo_glimt': 'bodo_glimt.png',
  'фейеноорд': 'feyenoord.png',
  'feyenoord': 'feyenoord.png',
  'селтик': 'celtic.png',
  'celtic': 'celtic.png',
  'расинг': 'racing.png',
  'racing': 'racing.png',
  'аякс': 'ajax.png',
  'ajax': 'ajax.png',
  'брага': 'braga.png',
  'braga': 'braga.png',
  'рейнджерс': 'rangers.png',
  'rangers': 'rangers.png',
  'брюгге': 'brugge.png',
  'club brugge': 'brugge.png',
  'brugge': 'brugge.png',
  'аек': 'aek.png',
  'aek': 'aek.png'
};

export function getTeamLogoUrl(teamName) {
  if (!teamName) return null;
  const t = teamName.trim().toLowerCase();
  for (const [k, file] of Object.entries(TEAM_LOGO_MAP)) {
    if (t === k || t.includes(k) || k.includes(t)) {
      return `/assets/logos/${file}`;
    }
  }
  return null;
}

export function renderTeamLogoHtml(teamName, size = 28, extraClass = '') {
  const url = getTeamLogoUrl(teamName);
  if (url) {
    return `<img src="${url}" alt="${teamName || 'Club'}" class="team-logo-img ${extraClass}" style="width:${size}px; height:${size}px; object-fit:contain; filter:drop-shadow(0 2px 6px rgba(0,0,0,0.4)); vertical-align:middle; display:inline-block; flex-shrink:0; background:transparent;" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='inline-block';" /><span class="team-logo-fallback ${extraClass}" style="display:none; font-size:${Math.round(size * 0.75)}px; vertical-align:middle;">🛡️</span>`;
  }
  return `<span class="team-logo-fallback ${extraClass}" style="font-size:${Math.round(size * 0.75)}px; vertical-align:middle;">🛡️</span>`;
}

const OUTCOME_NAMES = {
  p1: 'П1',
  x: 'Х',
  p2: 'П2',
  tb25: 'ТБ 2.5',
  tm25: 'ТМ 2.5',
  btts_yes: 'ОЗ: Да',
  btts_no: 'ОЗ: Нет',
  dc_1x: '1X',
  dc_12: '12',
  dc_x2: 'X2',
  over_15: 'ТБ 1.5',
  under_15: 'ТМ 1.5',
  over_25: 'ТБ 2.5',
  under_25: 'ТМ 2.5',
  over_35: 'ТБ 3.5',
  under_35: 'ТМ 3.5'
};

export class UIRenderer {
  static formatNumber(n) {
    return (n || 0).toLocaleString('ru-RU');
  }

  static renderHeader(user, progression) {
    const balEl = document.getElementById('user-balance-val');
    if (balEl && user) {
      balEl.textContent = `${this.formatNumber(user.balance)} 🪙`;
    }
    const lvlEl = document.getElementById('user-level-val');
    if (lvlEl && progression) {
      lvlEl.textContent = `Lvl ${progression.level || 1}`;
    }

    const aBadge = document.getElementById('achievements-badge');
    if (aBadge) {
      aBadge.style.display = store.state.unclaimedAchievementsCount > 0 ? 'inline-block' : 'none';
      aBadge.textContent = store.state.unclaimedAchievementsCount;
    }
  }

  static renderBonusBanner(bonus) {
    const bannerEl = document.getElementById('bonus-banner-container');
    if (!bannerEl) return;

    if (!bonus) {
      bannerEl.innerHTML = '';
      return;
    }

    if (bonus.can_claim) {
      bannerEl.innerHTML = `
        <div class="bonus-banner">
          <div class="bonus-banner-left">
            <div class="bonus-banner-icon">🎁</div>
            <div>
              <div class="bonus-banner-title">Ежедневный Бонус</div>
              <div class="bonus-banner-subtitle">+250 🪙 ждут тебя прямо сейчас!</div>
            </div>
          </div>
          <button class="btn-bonus-claim" id="btn-claim-daily-bonus">Забрать</button>
        </div>
      `;
    } else {
      const hours = Math.ceil(bonus.cooldown_seconds / 3600);
      bannerEl.innerHTML = `
        <div class="bonus-banner" style="background: rgba(18, 22, 31, 0.7); border-color: rgba(255,255,255,0.08);">
          <div class="bonus-banner-left">
            <div class="bonus-banner-icon" style="filter: grayscale(1);">⏳</div>
            <div>
              <div class="bonus-banner-title">Ежедневный Бонус</div>
              <div class="bonus-banner-subtitle">Следующий через ~${hours} ч.</div>
            </div>
          </div>
          <span style="font-size: 0.8rem; color: var(--text-muted); font-weight: 700; padding: 6px 12px; background: rgba(255,255,255,0.05); border-radius: var(--radius-sm);">
            Забрано
          </span>
        </div>
      `;
    }
  }

  static renderDivisionTabs(divisions, selectedDivisionId, containerId = 'lobby-division-tabs-container') {
    const container = document.getElementById(containerId);
    if (!container) return;

    const divs = (divisions && divisions.length > 0) ? divisions : [
      { id: 1, name: 'Дивизион 1' },
      { id: 2, name: 'Дивизион 2' },
      { id: 3, name: 'Дивизион 3' },
      { id: 4, name: 'Дивизион 4' },
      { id: 5, name: 'Дивизион 5' }
    ];

    container.innerHTML = divs.map(d => `
      <button class="division-tab-btn ${d.id === selectedDivisionId ? 'active' : ''}" 
              data-division-id="${d.id}">
        🛡️ ${d.name || `Дивизион ${d.id}`}
      </button>
    `).join('');
  }

  static renderTourTabs(tours, selectedTour) {
    const container = document.getElementById('tour-tabs-container');
    if (!container) return;

    if (!tours || tours.length === 0) {
      container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; padding: 10px 0;">Нет активных туров</div>';
      return;
    }

    container.innerHTML = tours.map(t => `
      <button class="tour-tab-btn ${t.round_number === selectedTour ? 'active' : ''}" 
              data-tour="${t.round_number}">
        ⚽ Тур ${t.round_number} (${t.unplayed_matches || t.total_matches})
      </button>
    `).join('');
  }

  static renderMatches(tours, selectedTour, activeCategory = 'all', searchQuery = '', statusFilter = 'all', selectedDivisionId = 1) {
    const container = document.getElementById('matches-list-container');
    if (!container) return;

    const currentTour = tours.find(t => t.round_number === selectedTour);
    if (!currentTour || !currentTour.matches || currentTour.matches.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 50px 20px; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 10px;">🏆</div>
          <div style="font-size: 1rem; font-weight: 700; color: #fff; margin-bottom: 4px;">Матчи тура завершены</div>
          <div style="font-size: 0.85rem;">Ожидайте открытия следующего тура Лиги</div>
        </div>
      `;
      return;
    }

    let filteredMatches = currentTour.matches;

    // Filter by match status
    if (statusFilter && statusFilter !== 'all') {
      if (statusFilter === 'open') {
        filteredMatches = filteredMatches.filter(m => ['open', 'scheduled', 'pending', 'live'].includes(m.status));
      } else if (statusFilter === 'upcoming') {
        filteredMatches = filteredMatches.filter(m => ['scheduled', 'pending'].includes(m.status));
      } else if (statusFilter === 'completed') {
        filteredMatches = filteredMatches.filter(m => ['confirmed', 'completed', 'finished'].includes(m.status));
      }
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      filteredMatches = filteredMatches.filter(m => 
        (m.team1_name || '').toLowerCase().includes(q) || 
        (m.team2_name || '').toLowerCase().includes(q)
      );
    }

    if (filteredMatches.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          Ничего не найдено в текущей категории или по запросу «${searchQuery}»
        </div>
      `;
      return;
    }

    container.innerHTML = filteredMatches.map(m => {
      const isLive = m.status === 'live';
      const isCompleted = ['confirmed', 'completed', 'finished'].includes(m.status);
      const tourLabel = m.tour || selectedTour;
      const divLabel = m.division_id || selectedDivisionId || 1;
      return `
        <div class="match-card ${isCompleted ? 'completed' : ''}" data-match-id="${m.match_id}">
          <!-- Match Card Header -->
          <div class="match-card-header">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span class="match-division-tag" style="background: rgba(245,176,39,0.15); color: var(--accent-gold); font-size: 0.72rem; font-weight: 800; padding: 2px 6px; border-radius: 4px;">
                Дивизион ${divLabel}
              </span>
              <span class="match-tour-tag">Тур ${tourLabel}</span>
              ${isLive ? `
                <span class="live-badge">
                  <span class="live-dot"></span> LIVE ${m.live_minute ? `${m.live_minute}'` : ''}
                </span>
              ` : ''}
              ${isCompleted ? `
                <span style="background: rgba(46, 204, 113, 0.15); color: #2ecc71; font-size: 0.72rem; font-weight: 800; padding: 2px 6px; border-radius: 4px;">
                  ✅ Завершён ${m.player1_score ?? 0}:${m.player2_score ?? 0}
                </span>
              ` : ''}
            </div>
            <span style="font-size: 0.75rem; color: var(--text-muted); font-weight: 600;">Лига Фифарей</span>
          </div>

          <!-- Teams Row with Crest Logos (Horizontal Centered Layout) -->
          <div class="match-teams-row">
            <div class="team-block-side left">
              <span class="team-name">${m.team1_name}</span>
              ${renderTeamLogoHtml(m.team1_name, 28)}
            </div>
            <div class="match-vs-divider">VS</div>
            <div class="team-block-side right">
              ${renderTeamLogoHtml(m.team2_name, 28)}
              <span class="team-name">${m.team2_name}</span>
            </div>
          </div>

          <!-- Primary 1X2 Odds Buttons Grid -->
          <div class="odds-grid-3col">
            <div class="odd-btn ${store.isSelectionActive(m.match_id, 'p1') ? 'selected' : ''}" 
                 data-match-id="${m.match_id}" data-outcome="p1" data-odd="${m.odds?.p1 || 1.90}">
              <span class="odd-label">П1</span>
              <span class="odd-val">${(m.odds?.p1 || 1.90).toFixed(2)}</span>
            </div>
            <div class="odd-btn ${store.isSelectionActive(m.match_id, 'x') ? 'selected' : ''}" 
                 data-match-id="${m.match_id}" data-outcome="x" data-odd="${m.odds?.x || 3.20}">
              <span class="odd-label">X</span>
              <span class="odd-val">${(m.odds?.x || 3.20).toFixed(2)}</span>
            </div>
            <div class="odd-btn ${store.isSelectionActive(m.match_id, 'p2') ? 'selected' : ''}" 
                 data-match-id="${m.match_id}" data-outcome="p2" data-odd="${m.odds?.p2 || 2.40}">
              <span class="odd-label">П2</span>
              <span class="odd-val">${(m.odds?.p2 || 2.40).toFixed(2)}</span>
            </div>
          </div>

          <!-- Secondary Filtered Category Odds (if chosen) -->
          ${(activeCategory === 'totals') ? `
            <div class="odds-grid-2col">
              <div class="odd-btn ${store.isSelectionActive(m.match_id, 'tb25') ? 'selected' : ''}" 
                   data-match-id="${m.match_id}" data-outcome="tb25" data-odd="${m.odds?.tb25 || 1.80}">
                <span class="odd-label">ТБ 2.5</span>
                <span class="odd-val">${(m.odds?.tb25 || 1.80).toFixed(2)}</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(m.match_id, 'tm25') ? 'selected' : ''}" 
                   data-match-id="${m.match_id}" data-outcome="tm25" data-odd="${m.odds?.tm25 || 1.95}">
                <span class="odd-label">ТМ 2.5</span>
                <span class="odd-val">${(m.odds?.tm25 || 1.95).toFixed(2)}</span>
              </div>
            </div>
          ` : ''}

          ${(activeCategory === 'btts') ? `
            <div class="odds-grid-2col">
              <div class="odd-btn ${store.isSelectionActive(m.match_id, 'btts_yes') ? 'selected' : ''}" 
                   data-match-id="${m.match_id}" data-outcome="btts_yes" data-odd="${m.odds?.btts_yes || 1.70}">
                <span class="odd-label">ОЗ Да</span>
                <span class="odd-val">${(m.odds?.btts_yes || 1.70).toFixed(2)}</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(m.match_id, 'btts_no') ? 'selected' : ''}" 
                   data-match-id="${m.match_id}" data-outcome="btts_no" data-odd="${m.odds?.btts_no || 2.05}">
                <span class="odd-label">ОЗ Нет</span>
                <span class="odd-val">${(m.odds?.btts_no || 2.05).toFixed(2)}</span>
              </div>
            </div>
          ` : ''}

          <!-- Match Navigation Action Buttons -->
          <div class="match-card-actions">
            <button class="btn-match-action btn-open-match-center" data-match-id="${m.match_id}">
              📊 Статистика & H2H
            </button>
            <button class="btn-match-action accent btn-more-markets" data-match-id="${m.match_id}">
              ⚡ Все рынки (15+)
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  static renderMatchCenter(matchDetail, stats, h2h, insights, live, markets = [], activeSubTab = 'markets') {
    const container = document.getElementById('match-center-container');
    if (!container) return;

    if (!matchDetail) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2.2rem; margin-bottom: 8px;">⚽</div>
          <div style="font-size: 0.95rem; font-weight: 700; color: #fff;">Матч не выбран</div>
          <div style="font-size: 0.8rem; margin-top: 4px;">Выберите матч из линии для просмотра коэффициентов и статистики</div>
        </div>
      `;
      return;
    }

    const t1 = matchDetail.team1_name || matchDetail.player1_team || 'Хозяева';
    const t2 = matchDetail.team2_name || matchDetail.player2_team || 'Гости';
    const s1 = live?.score1 ?? matchDetail.player1_score ?? '-';
    const s2 = live?.score2 ?? matchDetail.player2_score ?? '-';
    const matchId = matchDetail.id || matchDetail.match_id;
    const tourNum = matchDetail.round_number || 1;

    const t1Form = stats?.team1?.stats?.form || ['W', 'D', 'W'];
    const t2Form = stats?.team2?.stats?.form || ['D', 'L', 'W'];

    container.innerHTML = `
      <!-- Header Hero Card with Clean Logos -->
      <div class="match-center-header">
        <div class="team-vs-display">
          <div class="team-block">
            <div class="team-crest-container">
              ${renderTeamLogoHtml(t1, 48, 'team-crest-img')}
            </div>
            <div class="team-name-lg" style="margin-top: 6px;">${t1}</div>
            <div class="form-badges-row">
              ${t1Form.map(f => `<span class="form-dot ${f.toLowerCase()}">${f}</span>`).join('')}
            </div>
          </div>
          <div style="display: flex; flex-direction: column; align-items: center; gap: 4px;">
            <div class="score-center-badge">${s1} : ${s2}</div>
            <span style="font-size: 0.75rem; color: var(--text-muted); font-weight: 700;">
              ${matchDetail.status === 'live' ? '🔴 LIVE' : `Тур ${tourNum}`}
            </span>
          </div>
          <div class="team-block">
            <div class="team-crest-container">
              ${renderTeamLogoHtml(t2, 48, 'team-crest-img')}
            </div>
            <div class="team-name-lg" style="margin-top: 6px;">${t2}</div>
            <div class="form-badges-row">
              ${t2Form.map(f => `<span class="form-dot ${f.toLowerCase()}">${f}</span>`).join('')}
            </div>
          </div>
        </div>
      </div>

      <!-- Dedicated Sub-Navigation Menu for this Match -->
      <div class="mc-tabs">
        <button class="mc-subtab-btn ${activeSubTab === 'markets' ? 'active' : ''}" data-subtab="markets">
          🎯 Ставки и Рынки
        </button>
        <button class="mc-subtab-btn ${activeSubTab === 'stats' ? 'active' : ''}" data-subtab="stats">
          📊 Статистика & H2H
        </button>
        <button class="mc-subtab-btn ${activeSubTab === 'insights' ? 'active' : ''}" data-subtab="insights">
          🔥 Инсайты
        </button>
      </div>

      <!-- Sub-Tab Content -->
      ${activeSubTab === 'markets' ? `
        <!-- Betting Markets Menu -->
        <div class="match-markets-container">
          <!-- 1X2 Main Outcomes -->
          <div class="market-group-card">
            <div class="market-group-title">⚡ Основные исходы (1X2)</div>
            <div class="odds-grid-3col">
              <div class="odd-btn ${store.isSelectionActive(matchId, 'p1') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="p1" data-odd="1.95">
                <span class="odd-label">П1 (${t1})</span>
                <span class="odd-val">1.95</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'x') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="x" data-odd="3.20">
                <span class="odd-label">Ничья (X)</span>
                <span class="odd-val">3.20</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'p2') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="p2" data-odd="2.35">
                <span class="odd-label">П2 (${t2})</span>
                <span class="odd-val">2.35</span>
              </div>
            </div>
          </div>

          <!-- Double Chance -->
          <div class="market-group-card">
            <div class="market-group-title">🔄 Двойной шанс</div>
            <div class="odds-grid-3col">
              <div class="odd-btn ${store.isSelectionActive(matchId, 'dc_1x') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="dc_1x" data-odd="1.30">
                <span class="odd-label">1X</span>
                <span class="odd-val">1.30</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'dc_12') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="dc_12" data-odd="1.25">
                <span class="odd-label">12</span>
                <span class="odd-val">1.25</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'dc_x2') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="dc_x2" data-odd="1.45">
                <span class="odd-label">X2</span>
                <span class="odd-val">1.45</span>
              </div>
            </div>
          </div>

          <!-- Over / Under Totals -->
          <div class="market-group-card">
            <div class="market-group-title">⚽ Тоталы матча</div>
            <div class="odds-grid-2col">
              <div class="odd-btn ${store.isSelectionActive(matchId, 'over_15') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="over_15" data-odd="1.28">
                <span class="odd-label">ТБ 1.5</span>
                <span class="odd-val">1.28</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'under_15') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="under_15" data-odd="3.40">
                <span class="odd-label">ТМ 1.5</span>
                <span class="odd-val">3.40</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'tb25') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="tb25" data-odd="1.80">
                <span class="odd-label">ТБ 2.5</span>
                <span class="odd-val">1.80</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'tm25') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="tm25" data-odd="1.95">
                <span class="odd-label">ТМ 2.5</span>
                <span class="odd-val">1.95</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'over_35') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="over_35" data-odd="2.85">
                <span class="odd-label">ТБ 3.5</span>
                <span class="odd-val">2.85</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'under_35') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="under_35" data-odd="1.38">
                <span class="odd-label">ТМ 3.5</span>
                <span class="odd-val">1.38</span>
              </div>
            </div>
          </div>

          <!-- Both Teams To Score -->
          <div class="market-group-card">
            <div class="market-group-title">🥅 Обе команды забьют</div>
            <div class="odds-grid-2col">
              <div class="odd-btn ${store.isSelectionActive(matchId, 'btts_yes') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="btts_yes" data-odd="1.68">
                <span class="odd-label">ОЗ: Да</span>
                <span class="odd-val">1.68</span>
              </div>
              <div class="odd-btn ${store.isSelectionActive(matchId, 'btts_no') ? 'selected' : ''}" 
                   data-match-id="${matchId}" data-outcome="btts_no" data-odd="2.05">
                <span class="odd-label">ОЗ: Нет</span>
                <span class="odd-val">2.05</span>
              </div>
            </div>
          </div>
        </div>
      ` : activeSubTab === 'stats' ? `
        <!-- Statistics & H2H Menu -->
        <div class="match-stats-container">
          <!-- Head-to-Head Section -->
          ${h2h?.summary ? `
            <div class="market-group-card">
              <div class="market-group-title">🤝 История Очных Встреч (H2H)</div>
              <div style="display: flex; justify-content: space-between; font-size: 0.8rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 6px;">
                <span>Побед ${t1}: ${h2h.summary.team1_wins}</span>
                <span>Ничьих: ${h2h.summary.draws}</span>
                <span>Побед ${t2}: ${h2h.summary.team2_wins}</span>
              </div>
              <div class="h2h-progress-bar">
                <div class="h2h-bar-p1" style="width: ${(h2h.summary.team1_wins / Math.max(1, h2h.summary.total_meetings)) * 100}%"></div>
                <div class="h2h-bar-x" style="width: ${(h2h.summary.draws / Math.max(1, h2h.summary.total_meetings)) * 100}%"></div>
                <div class="h2h-bar-p2" style="width: ${(h2h.summary.team2_wins / Math.max(1, h2h.summary.total_meetings)) * 100}%"></div>
              </div>
            </div>
          ` : ''}

          <!-- Goals Analytics -->
          ${stats?.team1?.stats ? `
            <div class="market-group-card">
              <div class="market-group-title">⚽ Статистика Голов</div>
              <div class="kpi-grid">
                <div class="kpi-card">
                  <span class="kpi-label">Ср. голов ${t1}</span>
                  <span class="kpi-value gold">${stats.team1.stats.avg_goals_scored}</span>
                </div>
                <div class="kpi-card">
                  <span class="kpi-label">Ср. голов ${t2}</span>
                  <span class="kpi-value gold">${stats.team2.stats.avg_goals_scored}</span>
                </div>
                <div class="kpi-card">
                  <span class="kpi-label">ТБ 2.5 % (${t1})</span>
                  <span class="kpi-value green">${stats.team1.stats.over_25_pct}%</span>
                </div>
                <div class="kpi-card">
                  <span class="kpi-label">ТБ 2.5 % (${t2})</span>
                  <span class="kpi-value green">${stats.team2.stats.over_25_pct}%</span>
                </div>
              </div>
            </div>
          ` : ''}
        </div>
      ` : `
        <!-- AI Insights & Preview Menu -->
        <div class="match-insights-container">
          <div class="market-group-card">
            <div class="market-group-title" style="display:flex; justify-content:space-between; align-items:center;">
              <span>🧠 Прогноз ИИ «Темшик»</span>
              <span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; font-size:0.75rem; padding:2px 8px; border-radius:12px;">Ensemble v1</span>
            </div>

            ${insights?.probabilities ? `
              <div style="margin: 12px 0 8px 0;">
                <div style="display:flex; justify-content:space-between; font-size:0.8rem; font-weight:700; margin-bottom:4px;">
                  <span style="color:#60a5fa;">П1: ${Math.round(insights.probabilities.home * 100)}%</span>
                  <span style="color:#9ca3af;">Х: ${Math.round(insights.probabilities.draw * 100)}%</span>
                  <span style="color:#f87171;">П2: ${Math.round(insights.probabilities.away * 100)}%</span>
                </div>
                <div class="h2h-progress-bar" style="height:8px; border-radius:4px; overflow:hidden; display:flex;">
                  <div style="background:#3b82f6; width:${insights.probabilities.home * 100}%;"></div>
                  <div style="background:#6b7280; width:${insights.probabilities.draw * 100}%;"></div>
                  <div style="background:#ef4444; width:${insights.probabilities.away * 100}%;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.75rem; color:var(--text-muted); margin-top:4px;">
                  <span>Уверенность модели: ${Math.round((insights.confidence || 0.6) * 100)}%</span>
                  ${insights.elo ? `<span>Elo: ${insights.elo.rating_t1} vs ${insights.elo.rating_t2}</span>` : ''}
                </div>
              </div>
            ` : ''}

            <!-- Key Factors ("Why?") -->
            <div style="margin-top: 10px; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
              <div style="font-size:0.8rem; font-weight:700; color:var(--text-secondary); margin-bottom:6px;">💡 Ключевые факторы модели (Why?):</div>
              ${((insights?.key_factors && insights.key_factors.length > 0) ? insights.key_factors : (insights?.insights || [])).map(txt => `
                <div class="insight-card" style="padding:6px 10px; margin-bottom:6px; font-size:0.8rem; background:rgba(255,255,255,0.03); border-left:3px solid var(--primary-accent); border-radius:4px;">
                  <span>${txt}</span>
                </div>
              `).join('')}
            </div>

            <div style="margin-top:12px; font-size:0.7rem; color:var(--text-muted); text-align:center; font-style:italic;">
              ⚠️ Прогноз AI — аналитическая оценка, а не гарантия результата.
            </div>
          </div>
        </div>
      `}
    `;
  }

  static renderTournaments(standings, results, topScorers, activeTab = 'standings') {
    const container = document.getElementById('tournaments-content-container');
    if (!container) return;

    if (activeTab === 'standings') {
      if (!standings || standings.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">Таблица пока пуста.</div>';
        return;
      }
      container.innerHTML = `
        <div style="background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 10px; overflow-x: auto;">
          <table class="standings-table">
            <thead>
              <tr>
                <th>Клуб</th>
                <th>И</th>
                <th>В</th>
                <th>Н</th>
                <th>П</th>
                <th>Р/Г</th>
                <th>О</th>
              </tr>
            </thead>
            <tbody>
              ${standings.map((s, idx) => {
                const teamName = s.team_name || s.team || s.player_team || s.name || 'Команда';
                const played = s.played ?? s.games ?? 0;
                const wins = s.wins ?? s.won ?? 0;
                const draws = s.draws ?? s.drawn ?? 0;
                const losses = s.losses ?? s.lost ?? 0;
                const gf = s.goals_scored ?? s.goals_for ?? 0;
                const ga = s.goals_conceded ?? s.goals_against ?? 0;
                const diff = gf - ga;
                const diffStr = diff > 0 ? `+${diff}` : `${diff}`;
                const points = s.points ?? 0;

                return `
                  <tr>
                    <td style="text-align: left;">
                      <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="standings-pos-pill ${idx < 3 ? 'top' : 'mid'}">${idx + 1}</span>
                        ${renderTeamLogoHtml(teamName, 22)}
                        <span style="font-weight: 700; color: #fff;">${teamName}</span>
                      </div>
                    </td>
                    <td>${played}</td>
                    <td>${wins}</td>
                    <td>${draws}</td>
                    <td>${losses}</td>
                    <td style="color: ${diff > 0 ? 'var(--color-success)' : diff < 0 ? 'var(--color-danger)' : 'var(--text-secondary)'}; font-weight: 700;">${diffStr}</td>
                    <td style="font-weight: 900; color: var(--accent-gold); font-size: 0.95rem;">${points}</td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      `;
    } else if (activeTab === 'results') {
      if (!results || results.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">Архив результатов пуст.</div>';
        return;
      }
      container.innerHTML = results.map(r => {
        const t1 = r.team1_name || r.player1_team || 'Хозяева';
        const t2 = r.team2_name || r.player2_team || 'Гости';
        return `
          <div style="background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 12px 14px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
            <div style="flex: 1; text-align: right; font-weight: 700; color: #fff; display: flex; align-items: center; justify-content: flex-end; gap: 6px;">
              <span>${t1}</span>
              ${renderTeamLogoHtml(t1, 22)}
            </div>
            <div style="padding: 4px 14px; font-family: 'Outfit', sans-serif; font-weight: 900; font-size: 1.15rem; color: var(--accent-gold); background: rgba(0,0,0,0.3); border-radius: var(--radius-sm); margin: 0 10px;">
              ${r.player1_score ?? 0} : ${r.player2_score ?? 0}
            </div>
            <div style="flex: 1; text-align: left; font-weight: 700; color: #fff; display: flex; align-items: center; justify-content: flex-start; gap: 6px;">
              ${renderTeamLogoHtml(t2, 22)}
              <span>${t2}</span>
            </div>
          </div>
        `;
      }).join('');
    } else if (activeTab === 'scorers') {
      if (!topScorers || topScorers.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">Список бомбардиров формируется.</div>';
        return;
      }
      container.innerHTML = `
        <div style="background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 10px;">
          ${topScorers.map((sc, idx) => `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 4px; border-bottom: 1px solid rgba(255,255,255,0.04);">
              <div style="display: flex; align-items: center; gap: 10px;">
                <span style="font-weight: 800; color: ${idx < 3 ? 'var(--accent-gold)' : 'var(--text-secondary)'}; width: 22px;">#${idx + 1}</span>
                ${renderTeamLogoHtml(sc.team_name, 26)}
                <div>
                  <div style="font-weight: 700; color: #fff; font-size: 0.88rem;">${sc.player_name}</div>
                  <div style="font-size: 0.75rem; color: var(--text-muted);">${sc.team_name}</div>
                </div>
              </div>
              <div style="font-family: 'Outfit', sans-serif; font-weight: 900; color: var(--accent-gold); font-size: 1.05rem;">
                ⚽ ${sc.goals}
              </div>
            </div>
          `).join('')}
        </div>
      `;
    }
  }

  static renderPredictionsHistory(bets, filter = 'all') {
    const container = document.getElementById('history-list-container');
    if (!container) return;

    let filtered = bets || [];
    if (filter !== 'all') {
      if (filter === 'cancelled') {
        filtered = filtered.filter(b => ['cancelled', 'voided', 'void', 'refunded'].includes(b.status));
      } else {
        filtered = filtered.filter(b => b.status === filter);
      }
    }

    if (filtered.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2rem; margin-bottom: 8px;">📜</div>
          <div>Прогнозов в данной категории не найдено</div>
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(b => {
      const isWon = b.status === 'won';
      const isLost = b.status === 'lost';
      const isPending = b.status === 'pending';
      const isRefunded = b.status === 'refunded';
      const isCancelled = ['cancelled', 'voided', 'void'].includes(b.status);

      const isCashedOut = !!b.cashout_at;
      const statusBadge = isCashedOut
        ? '<span style="color: #60a5fa; font-weight: 800;">💰 CASHOUT</span>'
        : isWon
        ? '<span style="color: var(--color-success); font-weight: 800;">🎉 ВЫИГРЫШ</span>'
        : isLost
        ? '<span style="color: var(--color-danger); font-weight: 800;">❌ ПРОИГРЫШ</span>'
        : isRefunded
        ? '<span style="color: var(--color-warning); font-weight: 800;">↩️ ВОЗВРАТ</span>'
        : isCancelled
        ? '<span style="color: var(--text-muted); font-weight: 800;">🚫 ОТМЕНА</span>'
        : '<span style="color: var(--accent-gold); font-weight: 800;">⏳ В ИГРЕ</span>';

      return `
        <div class="bet-history-card" data-bet-id="${b.id}" style="background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 14px; margin-bottom: 12px; box-shadow: var(--shadow-card);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 8px;">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span style="font-size: 0.75rem; font-weight: 800; background: var(--bg-tertiary); padding: 3px 8px; border-radius: var(--radius-xs); color: #fff;">
                ${b.bet_type === 'express' ? '⚡ ЭКСПРЕСС' : 'ОДИНАР'}
              </span>
              <span style="font-size: 0.75rem; font-weight: 700; color: var(--accent-gold);">#${b.id}</span>
              <span style="font-size: 0.78rem; color: var(--text-muted);">${b.created_at ? b.created_at.substring(0, 16) : ''}</span>
            </div>
            <div>${statusBadge}</div>
          </div>

          <!-- Items in Slip -->
          <div style="margin-bottom: 10px;">
            ${(b.items || []).map(it => {
              const acceptedOdd = Number(it.odds_at_placement || it.odd || 1.0).toFixed(2);
              const divTag = it.division_id ? `<span style="font-size: 0.7rem; color: var(--text-muted); margin-right: 4px;">[Д${it.division_id}]</span>` : '';
              return `
              <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 0.82rem; border-bottom: 1px dashed rgba(255,255,255,0.04);">
                <div style="display: flex; align-items: center; gap: 6px; color: #fff; font-weight: 600;">
                  ${divTag}
                  ${renderTeamLogoHtml(it.team1_name, 18)}
                  <span>${it.team1_name}</span>
                  <span style="color: var(--text-muted); font-size: 0.75rem;">vs</span>
                  ${renderTeamLogoHtml(it.team2_name, 18)}
                  <span>${it.team2_name}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <span style="background: rgba(245,176,39,0.15); color: var(--accent-gold); padding: 2px 6px; border-radius: 4px; font-weight: 700;">
                    ${OUTCOME_NAMES[it.outcome_type] || it.outcome_type} @ ${acceptedOdd}
                  </span>
                  <span style="font-size: 0.75rem;">
                    ${it.status === 'won' ? '✅' : it.status === 'lost' ? '❌' : it.status === 'refunded' ? '↩️' : '⏳'}
                  </span>
                </div>
              </div>
            `;}).join('')}
          </div>

          <!-- Payout Summary -->
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 8px;">
            <div>
              <span style="color: var(--text-muted);">Ставка: </span>
              <span style="font-weight: 800; color: #fff;">${b.amount} 🪙</span>
              <span style="color: var(--text-muted); margin-left: 8px;">Кэф: </span>
              <span style="font-weight: 800; color: var(--accent-gold);">${Number(b.total_odd || 1.0).toFixed(2)}</span>
            </div>
            <div>
              <span style="color: var(--text-muted);">Выплата: </span>
              <span style="font-weight: 900; color: ${isWon || isCashedOut ? 'var(--color-success)' : '#fff'};">
                ${isCashedOut ? b.actual_payout : isWon ? (b.actual_payout || b.potential_win) : isPending ? b.potential_win : 0} 🪙
              </span>
            </div>
          </div>

          <!-- Card Actions (Cashout & Repeat) -->
          <div style="margin-top: 10px; display: flex; justify-content: flex-end; gap: 8px;">
            ${isPending ? `
              <button class="btn-cashout" data-bet-id="${b.id}" style="background: rgba(59,130,246,0.15); border: 1px solid rgba(59,130,246,0.3); color: #60a5fa; font-size: 0.75rem; font-weight: 700; padding: 5px 10px; border-radius: var(--radius-sm); cursor: pointer;">
                💰 Cashout
              </button>
            ` : ''}
            <button class="btn-repeat-bet" data-bet-id="${b.id}" style="background: transparent; border: 1px solid var(--border-subtle); color: var(--text-secondary); font-size: 0.75rem; font-weight: 700; padding: 5px 10px; border-radius: var(--radius-sm); cursor: pointer;">
              🔄 Повторить прогноз
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  static showOddsChangedModal(oldOdd, newOdd, onAccept, onReject) {
    const modal = document.getElementById('odds-changed-modal');
    const desc = document.getElementById('odds-changed-modal-desc');
    const acceptBtn = document.getElementById('btn-accept-odds-change');
    const rejectBtn = document.getElementById('btn-reject-odds-change');

    if (!modal) return;

    if (desc) {
      desc.innerHTML = `Коэффициент одного из исходов изменился: <b style="color: var(--text-muted); text-decoration: line-through;">${Number(oldOdd).toFixed(2)}</b> → <b style="color: var(--accent-gold);">${Number(newOdd).toFixed(2)}</b>.<br>Принять новые условия?`;
    }

    const cleanup = () => {
      modal.classList.remove('active');
    };

    const handleAccept = (e) => {
      e.stopPropagation();
      cleanup();
      if (typeof onAccept === 'function') onAccept();
    };

    const handleReject = (e) => {
      e.stopPropagation();
      cleanup();
      if (typeof onReject === 'function') onReject();
    };

    if (acceptBtn) {
      const newAccept = acceptBtn.cloneNode(true);
      acceptBtn.parentNode.replaceChild(newAccept, acceptBtn);
      newAccept.addEventListener('click', handleAccept);
    }
    if (rejectBtn) {
      const newReject = rejectBtn.cloneNode(true);
      rejectBtn.parentNode.replaceChild(newReject, rejectBtn);
      newReject.addEventListener('click', handleReject);
    }

    modal.classList.add('active');
  }

  static renderSavedCoupons(savedCoupons) {
    const container = document.getElementById('saved-coupons-container');
    if (!container) return;

    if (!savedCoupons || savedCoupons.length === 0) {
      container.innerHTML = '';
      return;
    }

    container.innerHTML = `
      <div style="font-size: 0.95rem; font-weight: 800; color: #fff; margin-bottom: 8px;">
        💾 Сохраненные Черновики (${savedCoupons.length})
      </div>
      ${savedCoupons.map(sc => `
        <div class="saved-coupon-card">
          <div>
            <div style="font-weight: 800; color: #fff; font-size: 0.88rem;">${sc.name || 'Купон'}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">
              ${sc.selections?.length || 0} событий | Кэф: ${(sc.total_odd || 1.0).toFixed(2)}
            </div>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn-restore-coupon" data-saved-id="${sc.id}">Загрузить</button>
            <button class="btn-delete-saved-coupon" data-saved-id="${sc.id}" style="background: transparent; border: none; color: var(--color-danger); font-size: 0.9rem; cursor: pointer;">✕</button>
          </div>
        </div>
      `).join('')}
    `;
  }

  static renderProfile(user, progression, stats, achievements) {
    const cardEl = document.getElementById('profile-card-container');
    if (cardEl && user) {
      const uName = user.username ? `@${user.username}` : (user.first_name || 'Каппер');
      const tgUser = tgBridge.getUser();
      const photoUrl = user.photo_url || tgUser?.photo_url || null;
      const initial = (user.username || user.first_name || 'K').replace('@', '').charAt(0).toUpperCase();

      const avatarHtml = photoUrl 
        ? `<img src="${photoUrl}" alt="Avatar" class="user-profile-avatar-img" onerror="this.outerHTML='<div class=\\'user-profile-avatar-fallback\\'>${initial}</div>'" />`
        : `<div class="user-profile-avatar-fallback">${initial}</div>`;

      cardEl.innerHTML = `
        <div style="display: flex; align-items: center; gap: 14px;">
          <div class="user-profile-avatar-container">
            ${avatarHtml}
          </div>
          <div>
            <div style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; font-weight: 900; color: #fff;">
              ${uName}
            </div>
            <div style="font-size: 0.82rem; color: var(--accent-gold); font-weight: 700; margin-top: 2px;">
              ${progression?.equipped_title || 'Каппер Лиги'} • Уровень ${progression?.level || 1}
            </div>
          </div>
        </div>
      `;
    }

    // KPI Metrics
    if (stats) {
      const roiEl = document.getElementById('kpi-roi');
      if (roiEl) {
        roiEl.textContent = `${stats.roi_pct > 0 ? '+' : ''}${stats.roi_pct}%`;
        roiEl.className = `kpi-value ${stats.roi_pct >= 0 ? 'green' : 'red'}`;
      }
      const wrEl = document.getElementById('kpi-winrate');
      if (wrEl) wrEl.textContent = `${stats.win_rate_pct}%`;
      const avgEl = document.getElementById('kpi-avg-odds');
      if (avgEl) avgEl.textContent = (stats.average_odds || 1.0).toFixed(2);
      const bestEl = document.getElementById('kpi-best-win');
      if (bestEl) bestEl.textContent = `${this.formatNumber(stats.best_win)} 🪙`;
    }

    // Achievements Grid
    const achEl = document.getElementById('achievements-grid-container');
    const achCountEl = document.getElementById('achievements-count-label');
    if (achEl && achievements) {
      const unlocked = achievements.filter(a => a.is_unlocked).length;
      if (achCountEl) achCountEl.textContent = `${unlocked}/${achievements.length}`;

      achEl.innerHTML = achievements.map(a => `
        <div class="achievement-card ${a.is_unlocked ? 'unlocked' : 'locked'}" data-ach-id="${a.id}">
          <div class="ach-icon" style="font-size: 1.8rem;">${a.icon || '🏆'}</div>
          <div style="margin-top: 6px;">
            <div class="ach-title" style="font-weight: 800; color: #fff; font-size: 0.85rem;">${a.title}</div>
            <div class="ach-desc" style="font-size: 0.72rem; color: var(--text-secondary); margin-top: 2px;">${a.description}</div>
          </div>
          ${a.is_unlocked && !a.is_claimed ? `
            <button class="btn-claim-ach" data-ach-id="${a.id}" style="margin-top: 8px; background: var(--accent-gold); color: #000; border: none; font-weight: 800; font-size: 0.75rem; padding: 4px 8px; border-radius: 4px; cursor: pointer;">
              Забрать +${a.reward_coins}🪙
            </button>
          ` : ''}
        </div>
      `).join('');
    }
  }

  static renderSlipDrawer(slip, stakeAmount) {
    const badgeEl = document.getElementById('slip-count-badge');
    const oddEl = document.getElementById('slip-total-odd');
    const itemsEl = document.getElementById('slip-items-container');
    const forecastEl = document.getElementById('slip-forecast-val');

    const totalOdd = store.getTotalOdd();
    const potentialWin = store.getPotentialWin();
    const isExpress = slip.length > 1;

    if (badgeEl) badgeEl.textContent = `Купон (${slip.length})`;
    if (oddEl) {
      oddEl.innerHTML = `Кэф: <b>${totalOdd.toFixed(2)}</b> ${isExpress ? '<span style="color: var(--accent-cyan); font-size: 0.75rem;">(⚡+5% Экспресс)</span>' : ''}`;
    }
    if (forecastEl) forecastEl.textContent = `${this.formatNumber(potentialWin)} 🪙`;

    if (itemsEl) {
      if (slip.length === 0) {
        itemsEl.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-muted); font-size: 0.85rem;">Выберите исходы матчей для добавления в купон</div>';
      } else {
        itemsEl.innerHTML = slip.map(s => `
          <div style="display: flex; justify-content: space-between; align-items: center; background: var(--bg-tertiary); padding: 8px 10px; border-radius: var(--radius-sm); margin-bottom: 6px;">
            <div>
              <div style="font-weight: 700; font-size: 0.82rem; color: #fff; display: flex; align-items: center; gap: 6px;">
                ${renderTeamLogoHtml(s.team1_name, 16)}
                <span>${s.team1_name}</span>
                <span style="color: var(--text-muted); font-size: 0.7rem;">—</span>
                ${renderTeamLogoHtml(s.team2_name, 16)}
                <span>${s.team2_name}</span>
              </div>
              <div style="font-size: 0.75rem; color: var(--accent-gold); font-weight: 800; margin-top: 2px;">
                ${s.selection_name || OUTCOME_NAMES[s.outcome] || s.outcome} @ ${s.odd.toFixed(2)}
              </div>
            </div>
            <button class="btn-remove-slip-item" data-match-id="${s.match_id}" style="background: transparent; border: none; color: var(--text-muted); font-size: 1.1rem; cursor: pointer;">✕</button>
          </div>
        `).join('');
      }
    }
  }

  static renderMatchMarketsModal(matchId, markets, matchTitle) {
    const titleEl = document.getElementById('modal-match-title');
    const listEl = document.getElementById('modal-markets-list');
    if (titleEl && matchTitle) titleEl.textContent = matchTitle;

    if (!listEl) return;

    if (!markets || markets.length === 0) {
      listEl.innerHTML = '<div style="text-align: center; padding: 30px; color: var(--text-muted);">Рынки загружаются...</div>';
      return;
    }

    listEl.innerHTML = markets.map(m => `
      <div style="background: var(--bg-tertiary); border-radius: var(--radius-sm); padding: 10px; margin-bottom: 10px;">
        <div style="font-weight: 800; font-size: 0.85rem; color: #fff; margin-bottom: 8px;">${m.name}</div>
        <div style="display: grid; grid-template-columns: repeat(${Math.min(3, m.selections?.length || 2)}, 1fr); gap: 6px;">
          ${(m.selections || []).map(sel => {
            const isSel = store.isSelectionActive(matchId, sel.selection_key);
            return `
              <div class="odd-btn ${isSel ? 'selected' : ''}" 
                   data-match-id="${matchId}" 
                   data-outcome="${sel.selection_key}" 
                   data-odd="${sel.current_odd}"
                   data-market-id="${m.id}"
                   data-selection-id="${sel.id}"
                   data-selection-name="${sel.name}">
                <span class="odd-label">${sel.name}</span>
                <span class="odd-val">${Number(sel.current_odd).toFixed(2)}</span>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `).join('');
  }

  static renderLeaderboardModal(leaderboard, myRank) {
    const podiumEl = document.getElementById('leaderboard-podium');
    const listEl = document.getElementById('leaderboard-list');

    if (!leaderboard || leaderboard.length === 0) {
      if (listEl) listEl.innerHTML = '<div style="text-align: center; padding: 30px; color: var(--text-muted);">Зал славы формируется...</div>';
      return;
    }

    const top3 = leaderboard.slice(0, 3);
    const rest = leaderboard.slice(3);

    if (podiumEl) {
      podiumEl.innerHTML = top3.map((p, idx) => `
        <div class="podium-col rank-${idx + 1}" style="text-align: center; flex: 1;">
          <div style="font-size: 1.8rem; margin-bottom: 4px;">${idx === 0 ? '🥇' : idx === 1 ? '🥈' : '🥉'}</div>
          <div style="font-weight: 800; font-size: 0.85rem; color: #fff;">${p.username || 'Игрок'}</div>
          <div style="font-size: 0.78rem; color: var(--accent-gold); font-weight: 800;">${this.formatNumber(p.balance)} 🪙</div>
        </div>
      `).join('');
    }

    if (listEl) {
      listEl.innerHTML = rest.map((p, idx) => `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 4px; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.85rem;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-weight: 800; color: var(--text-muted); width: 22px;">#${idx + 4}</span>
            <span style="font-weight: 700; color: #fff;">${p.username || 'Игрок'}</span>
          </div>
          <div style="font-weight: 800; color: var(--accent-gold);">${this.formatNumber(p.balance)} 🪙</div>
        </div>
      `).join('');
    }
  }

  // ─── Phase 6: Live Center & Sports Intelligence ───────────────────────────

  static renderLiveCenter(liveMatches, selectedMatchId, liveDetail, liveEvents, liveStats, liveMarkets, liveIntelligence) {
    const listEl = document.getElementById('live-matches-list-container');
    const detailEl = document.getElementById('live-match-detail-container');
    const statusEl = document.getElementById('live-provider-status-container');

    if (!listEl) return;

    if (statusEl) {
      statusEl.innerHTML = `
        <div style="background: rgba(255, 71, 87, 0.08); border: 1px solid rgba(255, 71, 87, 0.25); border-radius: var(--radius-sm); padding: 8px 12px; display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem;">
          <span style="color: #ff4757; font-weight: 800;">⚡ IN-PLAY FEED: АКТИВЕН</span>
          <span style="color: var(--text-muted);">Матчей в игре: <b>${liveMatches ? liveMatches.length : 0}</b></span>
        </div>
      `;
    }

    if (!liveMatches || liveMatches.length === 0) {
      listEl.innerHTML = `
        <div style="text-align: center; padding: 48px 20px; background: var(--bg-secondary); border: 1px dashed var(--border-subtle); border-radius: var(--radius-md);">
          <div style="font-size: 2.5rem; margin-bottom: 10px;">📡</div>
          <div style="font-family: 'Outfit', sans-serif; font-size: 1.1rem; font-weight: 800; color: #fff; margin-bottom: 6px;">
            НЕТ АКТИВНЫХ ЛАЙВ-МАТЧЕЙ
          </div>
          <div style="font-size: 0.82rem; color: var(--text-muted); max-width: 320px; margin: 0 auto; line-height: 1.45;">
            Внешний спорт-провайдер в режиме ожидания (LIVE DATA UNAVAILABLE). Лайв-трансляции и счет активируются в начале матчей тура.
          </div>
        </div>
      `;
      if (detailEl) detailEl.style.display = 'none';
      return;
    }

    // Render matches list
    listEl.innerHTML = liveMatches.map(m => {
      const isSelected = m.id === selectedMatchId;
      const hScore = m.home_score !== undefined ? m.home_score : 0;
      const aScore = m.away_score !== undefined ? m.away_score : 0;
      const min = m.minute ? `${m.minute}'` : 'LIVE';

      const freshnessBadge = m.freshness && m.freshness.badge ? m.freshness.badge : '';

      return `
        <div class="match-card ${isSelected ? 'live-selected' : ''}" style="margin-bottom: 12px; border-left: 3px solid #ff4757;">
          <div class="match-card-header" style="display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span class="live-pulse-dot" style="width: 8px; height: 8px; background: #ff4757; border-radius: 50%; display: inline-block;"></span>
              <span style="font-weight: 800; color: #ff4757; font-size: 0.82rem;">${min}</span>
              <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">${m.period || 'Основное время'}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              ${freshnessBadge ? `<span style="font-size: 0.72rem; font-weight: 700;">${freshnessBadge}</span>` : ''}
              <span style="font-size: 0.75rem; color: var(--accent-gold); font-weight: 700;">Дивизион ${m.division_id || 1}</span>
            </div>
          </div>

          <div class="match-card-teams" style="display: flex; justify-content: space-between; align-items: center; padding: 12px 0;">
            <div style="flex: 1; text-align: left; display: flex; align-items: center; gap: 8px;">
              ${renderTeamLogoHtml(m.player1_team, 28)}
              <span style="font-weight: 800; font-size: 0.95rem; color: #fff;">${m.player1_team}</span>
            </div>
            <div style="padding: 4px 12px; background: var(--bg-tertiary); border-radius: var(--radius-sm); font-size: 1.25rem; font-weight: 900; color: #ff4757; letter-spacing: 2px;">
              ${hScore} : ${aScore}
            </div>
            <div style="flex: 1; text-align: right; display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
              <span style="font-weight: 800; font-size: 0.95rem; color: #fff;">${m.player2_team}</span>
              ${renderTeamLogoHtml(m.player2_team, 28)}
            </div>
          </div>

          <div style="display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.05);">
            <button class="btn-open-live-detail" data-match-id="${m.id}" style="background: linear-gradient(135deg, #ff4757, #ff6b81); color: #fff; border: none; padding: 6px 14px; border-radius: var(--radius-sm); font-size: 0.8rem; font-weight: 800; cursor: pointer;">
              ⚡ Лайв Центр 2.0
            </button>
          </div>
        </div>
      `;
    }).join('');

    // Render detailed match center 2.0 if match selected
    if (detailEl && selectedMatchId && liveDetail) {
      detailEl.style.display = 'block';
      const m = liveDetail;
      const hScore = m.home_score !== undefined ? m.home_score : 0;
      const aScore = m.away_score !== undefined ? m.away_score : 0;

      // Statistics bars (strictly preserves NULL without fake 0s)
      let statsHtml = '';
      if (liveStats && liveStats.statistics) {
        const s = liveStats.statistics;
        const metrics = [
          { key: 'possession', label: 'Владение мячом', unit: '%' },
          { key: 'shots', label: 'Удары по воротам', unit: '' },
          { key: 'shots_on_target', label: 'Удары в створ', unit: '' },
          { key: 'corners', label: 'Угловые', unit: '' },
          { key: 'fouls', label: 'Фолы', unit: '' },
          { key: 'yellow_cards', label: 'Желтые карточки', unit: '' },
          { key: 'red_cards', label: 'Красные карточки', unit: '' },
          { key: 'xg', label: 'Ожидаемые голы (xG)', unit: '' },
        ];

        const validMetrics = metrics.filter(met => s[met.key] && (s[met.key].home !== null || s[met.key].away !== null));
        if (validMetrics.length > 0) {
          statsHtml = `
            <div style="margin-top: 16px; background: var(--bg-secondary); padding: 14px; border-radius: var(--radius-md); border: 1px solid var(--border-subtle);">
              <div style="font-weight: 800; font-size: 0.92rem; color: #fff; margin-bottom: 12px;">📊 Лайв-Статистика Матча</div>
              ${validMetrics.map(met => {
                const hVal = s[met.key].home !== null ? s[met.key].home : '—';
                const aVal = s[met.key].away !== null ? s[met.key].away : '—';
                return `
                  <div style="margin-bottom: 10px;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.78rem; font-weight: 700; margin-bottom: 4px;">
                      <span style="color: var(--accent-gold);">${hVal}${met.unit}</span>
                      <span style="color: var(--text-muted);">${met.label}</span>
                      <span style="color: var(--accent-cyan);">${aVal}${met.unit}</span>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `;
        }
      }

      // Timeline events
      let eventsHtml = '';
      if (liveEvents && liveEvents.length > 0) {
        eventsHtml = `
          <div style="margin-top: 16px; background: var(--bg-secondary); padding: 14px; border-radius: var(--radius-md); border: 1px solid var(--border-subtle);">
            <div style="font-weight: 800; font-size: 0.92rem; color: #fff; margin-bottom: 12px;">⏱ Хроника Событий</div>
            <div style="display: flex; flex-direction: column; gap: 8px;">
              ${liveEvents.map(ev => {
                const icon = ev.event_type === 'goal' ? '⚽' : ev.event_type === 'yellow_card' ? '🟨' : ev.event_type === 'red_card' ? '🟥' : ev.event_type === 'substitution' ? '🔄' : '📌';
                return `
                  <div style="display: flex; align-items: center; gap: 10px; font-size: 0.82rem; padding: 6px 8px; background: var(--bg-tertiary); border-radius: var(--radius-sm);">
                    <span style="font-weight: 900; color: #ff4757; min-width: 28px;">${ev.minute}'</span>
                    <span>${icon}</span>
                    <span style="font-weight: 700; color: #fff;">${ev.event_type.toUpperCase()}</span>
                    <span style="color: var(--text-secondary); margin-left: auto;">${ev.player_id || ''}</span>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `;
      }

      detailEl.innerHTML = `
        <div style="background: var(--bg-secondary); border: 1px solid var(--border-active); border-radius: var(--radius-md); padding: 16px; margin-bottom: 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 0.8rem; font-weight: 800; color: #ff4757;">🔴 В ЭФИРЕ: ${m.minute ? m.minute + "'" : 'LIVE'}</span>
              <span style="font-size: 0.72rem; font-weight: 700;">${m.freshness && m.freshness.badge ? m.freshness.badge : '🟢 LIVE DATA FRESH'}</span>
            </div>
            <span style="font-size: 0.75rem; color: var(--text-muted);">${m.provider || 'Официальный поток'}</span>
          </div>

          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <div style="flex: 1; text-align: center;">
              ${renderTeamLogoHtml(m.player1_team, 40)}
              <div style="font-weight: 800; font-size: 0.95rem; color: #fff; margin-top: 6px;">${m.player1_team}</div>
            </div>
            <div style="font-family: 'Outfit', sans-serif; font-size: 2rem; font-weight: 900; color: #ff4757; padding: 0 16px;">
              ${hScore} : ${aScore}
            </div>
            <div style="flex: 1; text-align: center;">
              ${renderTeamLogoHtml(m.player2_team, 40)}
              <div style="font-weight: 800; font-size: 0.95rem; color: #fff; margin-top: 6px;">${m.player2_team}</div>
            </div>
          </div>
        </div>

        ${eventsHtml}
        ${statsHtml}
      `;
    }
  }

  static renderHotMatches(hotMatches) {
    const el = document.getElementById('hot-matches-container');
    if (!el) return;

    if (!hotMatches || hotMatches.length === 0) {
      el.innerHTML = '';
      return;
    }

    el.innerHTML = `
      <div style="margin-bottom: 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <span style="font-size: 0.95rem; font-weight: 800; color: #fff; display: flex; align-items: center; gap: 6px;">
            🔥 Топ Горячих Матчей
          </span>
          <span style="font-size: 0.75rem; color: var(--accent-gold); font-weight: 700;">Scoring Engine</span>
        </div>
        <div style="display: flex; gap: 10px; overflow-x: auto; padding-bottom: 6px; -webkit-overflow-scrolling: touch;">
          ${hotMatches.slice(0, 5).map(m => `
            <div style="min-width: 220px; background: var(--bg-secondary); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 10px; flex-shrink: 0;">
              <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-muted); margin-bottom: 6px;">
                <span>Тур ${m.round_number || 1}</span>
                <span style="color: #ff4757; font-weight: 800;">🔥 ${m.hot_score} pts</span>
              </div>
              <div style="font-size: 0.85rem; font-weight: 800; color: #fff; margin-bottom: 6px;">
                ${m.player1_team} — ${m.player2_team}
              </div>
              <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem;">
                <span style="color: var(--text-secondary);">${m.reasons ? m.reasons[0] : 'Высокий интерес'}</span>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  static renderOddsMovers(oddsMovers) {
    const el = document.getElementById('odds-movers-container');
    if (!el) return;

    if (!oddsMovers || oddsMovers.length === 0) {
      el.innerHTML = '';
      return;
    }

    el.innerHTML = `
      <div style="margin-bottom: 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <span style="font-size: 0.95rem; font-weight: 800; color: #fff; display: flex; align-items: center; gap: 6px;">
            📈 Движение Коэффициентов
          </span>
          <span style="font-size: 0.75rem; color: var(--accent-cyan); font-weight: 700;">Live Volatility</span>
        </div>
        <div style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 6px; -webkit-overflow-scrolling: touch;">
          ${oddsMovers.slice(0, 6).map(mov => {
            const isDrop = mov.direction === 'down';
            const arrow = isDrop ? '▼' : '▲';
            const color = isDrop ? 'var(--color-success)' : 'var(--color-danger)';
            return `
              <div style="min-width: 170px; background: var(--bg-secondary); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 8px 10px; flex-shrink: 0;">
                <div style="font-size: 0.72rem; color: var(--text-muted);">${mov.player1_team} - ${mov.player2_team}</div>
                <div style="font-size: 0.8rem; font-weight: 800; color: #fff; margin: 3px 0;">${mov.selection_name || mov.outcome_type || 'Исход'}</div>
                <div style="display: flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 800;">
                  <span style="color: var(--text-muted); text-decoration: line-through;">${mov.previous_odds ? mov.previous_odds.toFixed(2) : ''}</span>
                  <span style="color: #fff;">${mov.current_odds.toFixed(2)}</span>
                  <span style="color: ${color};">${arrow} ${Math.abs(mov.pct_change).toFixed(1)}%</span>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  static renderRecommendations(recommendations) {
    const el = document.getElementById('recommendations-container');
    if (!el) return;

    if (!recommendations || recommendations.length === 0) {
      el.innerHTML = '';
      return;
    }

    el.innerHTML = `
      <div style="margin-bottom: 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <span style="font-size: 0.95rem; font-weight: 800; color: #fff; display: flex; align-items: center; gap: 6px;">
            💡 Рекомендации для вас
          </span>
          <span style="font-size: 0.75rem; color: var(--accent-gold); font-weight: 700;">Personalized</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 6px;">
          ${recommendations.slice(0, 3).map(rec => `
            <div style="background: var(--bg-secondary); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 10px 12px; display: flex; justify-content: space-between; align-items: center;">
              <div>
                <div style="font-weight: 800; font-size: 0.85rem; color: #fff;">${rec.player1_team} — ${rec.player2_team}</div>
                <div style="font-size: 0.75rem; color: var(--text-gold); margin-top: 2px;">${rec.reason || 'Высокий интерес'}</div>
              </div>
              <button class="btn-open-match-center" data-match-id="${rec.match_id}" style="background: var(--bg-tertiary); border: 1px solid var(--border-subtle); color: var(--accent-gold); border-radius: var(--radius-sm); padding: 5px 10px; font-size: 0.75rem; font-weight: 800; cursor: pointer;">
                Аналитика →
              </button>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }
}

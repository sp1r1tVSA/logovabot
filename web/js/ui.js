/**
 * web/js/ui.js
 * UI Components and Views Renderer for Logovo.bet.
 */

import { store } from './store.js';
import { tgBridge } from './tg.js';

const OUTCOME_NAMES = {
  p1: 'П1',
  x: 'Х',
  p2: 'П2',
  tb25: 'ТБ 2.5',
  tm25: 'ТМ 2.5',
  btts_yes: 'ОЗ: Да',
  btts_no: 'ОЗ: Нет'
};

export class UIRenderer {
  static formatNumber(n) {
    return (n || 0).toLocaleString('ru-RU');
  }

  static renderHeader(user) {
    const balEl = document.getElementById('user-balance-val');
    if (balEl && user) {
      balEl.textContent = `${this.formatNumber(user.balance)} 🪙`;
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
              <div class="bonus-banner-subtitle">Доступен через ~${hours} ч.</div>
            </div>
          </div>
        </div>
      `;
    }
  }

  static renderTourTabs(tours, selectedTour) {
    const container = document.getElementById('tour-tabs-container');
    if (!container) return;

    if (!tours || tours.length === 0) {
      container.innerHTML = '';
      return;
    }

    let html = '';
    for (const t of tours) {
      const isActive = t.round_number === selectedTour ? 'active' : '';
      const dlNote = t.deadline ? `⏰ до ${t.deadline.slice(5, 16)}` : '';
      html += `
        <button class="tour-tab-btn ${isActive}" data-tour="${t.round_number}">
          ⚽ Тур #${t.round_number} (${t.unplayed_matches})
        </button>
      `;
    }
    container.innerHTML = html;
  }

  static renderMatchCards(matches, slip) {
    const container = document.getElementById('matches-list-container');
    if (!container) return;

    if (!matches || matches.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 10px;">🔒</div>
          <div style="font-weight: 700; color: #fff; margin-bottom: 6px;">Линия закрыта</div>
          <div style="font-size: 0.85rem;">Все матчи тура завершены либо дедлайн истёк.</div>
        </div>
      `;
      return;
    }

    let html = '';
    for (const m of matches) {
      const curPick = slip.find(s => s.match_id === m.match_id);

      const isP1 = curPick?.outcome === 'p1' ? 'selected' : '';
      const isX = curPick?.outcome === 'x' ? 'selected' : '';
      const isP2 = curPick?.outcome === 'p2' ? 'selected' : '';

      const isTB = curPick?.outcome === 'tb25' ? 'selected' : '';
      const isTM = curPick?.outcome === 'tm25' ? 'selected' : '';
      const isBTTSYes = curPick?.outcome === 'btts_yes' ? 'selected' : '';
      const isBTTSNo = curPick?.outcome === 'btts_no' ? 'selected' : '';

      html += `
        <div class="match-card" data-match-id="${m.match_id}">
          <div class="match-header">
            <span>🏆 Тур #${m.tour}</span>
            <span class="vs-badge">МАТЧ</span>
          </div>
          
          <div class="match-teams">
            <div class="team-block">
              <span class="team-name">${m.team1_name}</span>
            </div>
            <span class="vs-badge">VS</span>
            <div class="team-block away">
              <span class="team-name">${m.team2_name}</span>
            </div>
          </div>

          <div class="odds-grid-main">
            <div class="odd-btn ${isP1}" data-outcome="p1" data-odd="${m.odds.p1}">
              <span class="odd-label">П1</span>
              <span class="odd-val">${m.odds.p1.toFixed(2)}</span>
            </div>
            <div class="odd-btn ${isX}" data-outcome="x" data-odd="${m.odds.x}">
              <span class="odd-label">Ничья</span>
              <span class="odd-val">${m.odds.x.toFixed(2)}</span>
            </div>
            <div class="odd-btn ${isP2}" data-outcome="p2" data-odd="${m.odds.p2}">
              <span class="odd-label">П2</span>
              <span class="odd-val">${m.odds.p2.toFixed(2)}</span>
            </div>
          </div>

          <button class="extra-markets-toggle" data-match-toggle="${m.match_id}">
            <span>Доп. исходы (Тотал / Обе Забьют)</span> <span>▾</span>
          </button>

          <div class="extra-markets-panel" id="extra-panel-${m.match_id}">
            <div class="odd-btn ${isTB}" data-outcome="tb25" data-odd="${m.odds.tb25}">
              <span class="odd-label">ТБ 2.5</span>
              <span class="odd-val">${m.odds.tb25.toFixed(2)}</span>
            </div>
            <div class="odd-btn ${isTM}" data-outcome="tm25" data-odd="${m.odds.tm25}">
              <span class="odd-label">ТМ 2.5</span>
              <span class="odd-val">${m.odds.tm25.toFixed(2)}</span>
            </div>
            <div class="odd-btn ${isBTTSYes}" data-outcome="btts_yes" data-odd="${m.odds.btts_yes}">
              <span class="odd-label">ОЗ: Да</span>
              <span class="odd-val">${m.odds.btts_yes.toFixed(2)}</span>
            </div>
            <div class="odd-btn ${isBTTSNo}" data-outcome="btts_no" data-odd="${m.odds.btts_no}">
              <span class="odd-label">ОЗ: Нет</span>
              <span class="odd-val">${m.odds.btts_no.toFixed(2)}</span>
            </div>
          </div>
        </div>
      `;
    }

    container.innerHTML = html;
  }

  static renderBetSlip(slip, totalOdd, stakeAmount, potentialWin) {
    const drawerEl = document.getElementById('slip-drawer');
    const badgeEl = document.getElementById('slip-count-badge');
    const totalOddEl = document.getElementById('slip-total-odd');
    const itemsContainer = document.getElementById('slip-items-container');
    const forecastEl = document.getElementById('slip-forecast-val');
    const navBadge = document.getElementById('nav-slip-badge');

    if (!drawerEl) return;

    if (navBadge) {
      navBadge.textContent = slip.length > 0 ? slip.length : '';
      navBadge.style.display = slip.length > 0 ? 'inline-block' : 'none';
    }

    if (slip.length === 0) {
      drawerEl.classList.remove('has-items', 'expanded');
      return;
    }

    drawerEl.classList.add('has-items');

    const betType = slip.length === 1 ? 'Ординар' : `Экспресс (${slip.length})`;
    if (badgeEl) badgeEl.textContent = betType;
    if (totalOddEl) totalOddEl.textContent = `Кэф: ${totalOdd.toFixed(2)}`;
    if (forecastEl) forecastEl.textContent = `${this.formatNumber(potentialWin)} 🪙`;

    if (itemsContainer) {
      let itemsHtml = '';
      for (const item of slip) {
        itemsHtml += `
          <div class="slip-item">
            <div class="slip-item-left">
              <span class="slip-item-match">${item.team1_name} vs ${item.team2_name}</span>
              <span class="slip-item-pick">${OUTCOME_NAMES[item.outcome] || item.outcome}</span>
            </div>
            <div class="slip-item-right">
              <span class="slip-item-odd">${item.odd.toFixed(2)}</span>
              <button class="btn-remove-item" data-remove-id="${item.match_id}">✕</button>
            </div>
          </div>
        `;
      }
      itemsContainer.innerHTML = itemsHtml;
    }
  }

  static renderPredictions(bets) {
    const container = document.getElementById('history-list-container');
    if (!container) return;

    if (!bets || bets.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 10px;">📜</div>
          <div style="font-weight: 700; color: #fff; margin-bottom: 6px;">Нет ставок</div>
          <div style="font-size: 0.85rem;">Вы ещё не сделали ни одного прогноза.</div>
        </div>
      `;
      return;
    }

    let html = '';
    for (const b of bets) {
      const isWon = b.status === 'won';
      const isLost = b.status === 'lost';
      const statusBadge = isWon ? '💸 Выигрыш' : (isLost ? '❌ Проигрыш' : '⏳ В игре');
      const statusColor = isWon ? 'var(--color-success)' : (isLost ? 'var(--color-danger)' : 'var(--color-warning)');
      const bType = b.bet_type === 'single' ? 'Ординар' : 'Экспресс';

      html += `
        <div class="match-card">
          <div class="match-header">
            <span>Ставка #${b.id} (${bType})</span>
            <span style="color: ${statusColor}; font-weight: 800;">${statusBadge}</span>
          </div>
          <div style="font-size: 0.85rem; margin-bottom: 8px; color: var(--text-secondary);">
            Сумма: <b>${b.amount.toLocaleString()} 🪙</b> | Кэф: <b>${b.total_odd.toFixed(2)}</b> | 
            Выигрыш: <b style="color: var(--accent-gold);">${b.potential_win.toLocaleString()} 🪙</b>
          </div>
          <div style="border-top: 1px solid var(--border-subtle); padding-top: 6px;">
            ${(b.items || []).map(it => `
              <div style="font-size: 0.78rem; display: flex; justify-content: space-between; padding: 2px 0;">
                <span>${it.team1_name || 'Клуб 1'} vs ${it.team2_name || 'Клуб 2'} (${OUTCOME_NAMES[it.outcome_type] || it.outcome_type})</span>
                <b>${it.odd.toFixed(2)}</b>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }
    container.innerHTML = html;
  }

  static renderLeaderboard(leaders, myRank) {
    const podiumEl = document.getElementById('leaderboard-podium');
    const listEl = document.getElementById('leaderboard-list');

    if (podiumEl && leaders && leaders.length >= 3) {
      podiumEl.innerHTML = `
        <div class="podium-card">
          <div class="podium-medal">🥈</div>
          <div class="podium-name">${leaders[1].username || leaders[1].team_name || 'Игрок 2'}</div>
          <div class="podium-bal">${this.formatNumber(leaders[1].balance)} 🪙</div>
        </div>
        <div class="podium-card first">
          <div class="podium-medal">🥇</div>
          <div class="podium-name">${leaders[0].username || leaders[0].team_name || 'Игрок 1'}</div>
          <div class="podium-bal">${this.formatNumber(leaders[0].balance)} 🪙</div>
        </div>
        <div class="podium-card">
          <div class="podium-medal">🥉</div>
          <div class="podium-name">${leaders[2].username || leaders[2].team_name || 'Игрок 3'}</div>
          <div class="podium-bal">${this.formatNumber(leaders[2].balance)} 🪙</div>
        </div>
      `;
    }

    if (listEl && leaders) {
      let listHtml = '';
      for (let i = 3; i < leaders.length; i++) {
        const u = leaders[i];
        const name = u.username || u.team_name || `Игрок ${u.user_id}`;
        listHtml += `
          <div class="leader-row">
            <div class="leader-left">
              <span class="leader-rank">${i + 1}.</span>
              <div>
                <div class="leader-name">${name}</div>
                <div class="leader-stats">Побед: ${u.bets_won}/${u.bets_count}</div>
              </div>
            </div>
            <div class="leader-bal">${this.formatNumber(u.balance)} 🪙</div>
          </div>
        `;
      }
      listEl.innerHTML = listHtml;
    }
  }
}

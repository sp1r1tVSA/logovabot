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

  static renderHeader(user, progression) {
    const balEl = document.getElementById('user-balance-val');
    if (balEl && user) {
      balEl.textContent = `${this.formatNumber(user.balance)} 🪙`;
    }
    const lvlEl = document.getElementById('user-level-val');
    if (lvlEl && progression) {
      lvlEl.textContent = `Lvl ${progression.level || 1}`;
    }

    // Badges on nav items
    const qBadge = document.getElementById('quests-badge');
    if (qBadge) {
      qBadge.style.display = store.state.unclaimedQuestsCount > 0 ? 'inline-block' : 'none';
      qBadge.textContent = store.state.unclaimedQuestsCount;
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

  static renderTourTabs(tours, selectedTour) {
    const container = document.getElementById('tour-tabs-container');
    if (!container) return;

    if (!tours || tours.length === 0) {
      container.innerHTML = '';
      return;
    }

    container.innerHTML = tours.map(t => `
      <button class="tour-tab-btn ${t.round_number === selectedTour ? 'active' : ''}" 
              data-tour="${t.round_number}">
        ⚽ Тур #${t.round_number} (${t.unplayed_matches || t.total_matches})
      </button>
    `).join('');
  }

  static renderMatchCards(matches, currentSlip = []) {
    const container = document.getElementById('matches-list-container');
    if (!container) return;

    if (!matches || matches.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 48px 20px; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 10px;">🏆</div>
          <div style="font-weight: 700; font-size: 1.05rem; color: #fff; margin-bottom: 6px;">Матчи тура завершены</div>
          <div style="font-size: 0.85rem;">Все результаты внесены или тур закрыт. Ожидайте открытия следующего тура!</div>
        </div>
      `;
      return;
    }

    container.innerHTML = matches.map(m => {
      const isPick = (outcome) => {
        const item = currentSlip.find(s => s.match_id === m.match_id);
        return item && item.outcome === outcome;
      };

      return `
        <div class="match-card" data-match-id="${m.match_id}">
          <div class="match-header">
            <span>⚽ Тур #${m.tour}</span>
            <span style="color: var(--accent-gold); font-weight: 800;">До ${m.deadline ? m.deadline.slice(0, 16) : 'дедлайна'}</span>
          </div>

          <div class="match-teams">
            <div class="team-block home">
              <span class="team-name" title="${m.team1_name}">${m.team1_name}</span>
            </div>
            <div class="vs-badge">VS</div>
            <div class="team-block away">
              <span class="team-name" title="${m.team2_name}">${m.team2_name}</span>
            </div>
          </div>

          <!-- Main 1X2 Odds -->
          <div class="odds-grid-main">
            <button class="odd-btn ${isPick('p1') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="p1" data-odd="${m.odd_p1}">
              <span class="odd-label">П1</span>
              <span class="odd-val">${Number(m.odd_p1).toFixed(2)}</span>
            </button>
            <button class="odd-btn ${isPick('x') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="x" data-odd="${m.odd_x}">
              <span class="odd-label">Ничья</span>
              <span class="odd-val">${Number(m.odd_x).toFixed(2)}</span>
            </button>
            <button class="odd-btn ${isPick('p2') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="p2" data-odd="${m.odd_p2}">
              <span class="odd-label">П2</span>
              <span class="odd-val">${Number(m.odd_p2).toFixed(2)}</span>
            </button>
          </div>

          <!-- Extra Markets Accordion Toggle -->
          <button class="extra-markets-toggle" data-target="extra-${m.match_id}">
            <span>Дополнительные рынки (ТБ / ОЗ)</span>
            <span class="arrow-icon">▼</span>
          </button>

          <!-- Extra Markets Panel -->
          <div class="extra-markets-panel" id="extra-${m.match_id}">
            <button class="odd-btn ${isPick('tb25') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="tb25" data-odd="${m.odd_tb25}">
              <span class="odd-label">ТБ 2.5</span>
              <span class="odd-val">${Number(m.odd_tb25).toFixed(2)}</span>
            </button>
            <button class="odd-btn ${isPick('tm25') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="tm25" data-odd="${m.odd_tm25}">
              <span class="odd-label">ТМ 2.5</span>
              <span class="odd-val">${Number(m.odd_tm25).toFixed(2)}</span>
            </button>
            <button class="odd-btn ${isPick('btts_yes') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="btts_yes" data-odd="${m.odd_btts_yes}">
              <span class="odd-label">ОЗ: Да</span>
              <span class="odd-val">${Number(m.odd_btts_yes).toFixed(2)}</span>
            </button>
            <button class="odd-btn ${isPick('btts_no') ? 'selected' : ''}" data-match-id="${m.match_id}" data-outcome="btts_no" data-odd="${m.odd_btts_no}">
              <span class="odd-label">ОЗ: Нет</span>
              <span class="odd-val">${Number(m.odd_btts_no).toFixed(2)}</span>
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  static renderBetSlip(slip, stakeAmount) {
    const drawer = document.getElementById('slip-drawer');
    const badge = document.getElementById('slip-count-badge');
    const oddEl = document.getElementById('slip-total-odd');
    const itemsContainer = document.getElementById('slip-items-container');
    const forecastEl = document.getElementById('slip-forecast-val');
    const inputEl = document.getElementById('stake-input');

    if (!drawer) return;

    if (!slip || slip.length === 0) {
      drawer.classList.remove('has-items');
      drawer.classList.remove('expanded');
      return;
    }

    drawer.classList.add('has-items');
    const count = slip.length;
    const typeLabel = count === 1 ? 'Ординар' : `Экспресс (${count})`;
    badge.textContent = typeLabel;

    const totalOdd = store.getTotalOdd();
    oddEl.textContent = `Кэф: ${totalOdd.toFixed(2)}`;

    if (itemsContainer) {
      itemsContainer.innerHTML = slip.map(s => `
        <div class="slip-item">
          <div class="slip-item-left">
            <div class="slip-item-match">${s.team1_name} vs ${s.team2_name}</div>
            <div class="slip-item-pick">${OUTCOME_NAMES[s.outcome] || s.outcome}</div>
          </div>
          <div class="slip-item-right">
            <span class="slip-item-odd">${s.odd.toFixed(2)}</span>
            <button class="btn-remove-item" data-remove-match="${s.match_id}">✕</button>
          </div>
        </div>
      `).join('');
    }

    if (inputEl) {
      inputEl.value = stakeAmount;
    }

    if (forecastEl) {
      const potWin = store.getPotentialWin();
      forecastEl.textContent = `${this.formatNumber(potWin)} 🪙`;
    }
  }

  static renderQuestsView(quests, streak) {
    const calendarContainer = document.getElementById('streak-calendar-container');
    const questsContainer = document.getElementById('quests-list-container');
    const countLabel = document.getElementById('quests-count-label');

    if (calendarContainer && streak) {
      const curStreak = streak.streak || 1;
      const rewards = [200, 300, 500, 700, 1000, 1500, 3000];
      calendarContainer.innerHTML = `
        <div class="streak-header">
          <div class="streak-title-wrap">
            <span class="streak-flame-icon">🔥</span>
            <div>
              <div style="font-family: 'Outfit'; font-size: 1.1rem; font-weight: 900; color: #fff;">
                Серия входов: ${curStreak} дн.
              </div>
              <div style="font-size: 0.78rem; color: var(--text-secondary);">
                Заходи каждый день за растущими бонусами!
              </div>
            </div>
          </div>
          <div style="text-align: right;">
            <span style="font-size: 0.72rem; font-weight: 800; color: var(--accent-cyan); background: rgba(0,210,255,0.12); padding: 3px 8px; border-radius: var(--radius-xs);">
              🛡 Щиты: ${streak.streak_shield_count || 0}
            </span>
          </div>
        </div>
        <div class="streak-days-grid">
          ${rewards.map((r, i) => {
            const dayNum = i + 1;
            const isCompleted = dayNum < (curStreak % 7 || 7);
            const isCurrent = dayNum === (curStreak % 7 || 7);
            return `
              <div class="streak-day-item ${isCompleted ? 'active' : ''} ${isCurrent ? 'current' : ''}">
                <span>День ${dayNum}</span>
                <span class="streak-day-reward">+${r}</span>
                <span>🪙</span>
              </div>
            `;
          }).join('')}
        </div>
      `;
    }

    if (questsContainer && quests) {
      const completedCount = quests.filter(q => q.is_completed).length;
      if (countLabel) countLabel.textContent = `${completedCount}/${quests.length}`;

      questsContainer.innerHTML = quests.map(q => {
        const pct = Math.min(100, Math.round((q.progress / q.target_count) * 100));
        return `
          <div class="quest-card">
            <div class="quest-top">
              <div>
                <div class="quest-title">${q.title}</div>
                <div class="quest-desc">${q.description}</div>
              </div>
              <div class="quest-rewards-badge">
                +${q.reward_coins} 🪙 | +${q.reward_xp} XP
              </div>
            </div>

            <div class="quest-progress-bar-wrap">
              <div class="quest-progress-bar-fill" style="width: ${pct}%;"></div>
            </div>

            <div class="quest-bottom">
              <span class="quest-count-label">Прогресс: ${q.progress}/${q.target_count}</span>
              ${q.is_claimed ? `
                <span style="font-size: 0.78rem; font-weight: 700; color: var(--text-muted);">Получено ✓</span>
              ` : q.is_completed ? `
                <button class="btn-claim-quest" data-quest-id="${q.id}">Забрать награду</button>
              ` : `
                <span style="font-size: 0.78rem; font-weight: 700; color: var(--text-secondary);">В процессе</span>
              `}
            </div>
          </div>
        `;
      }).join('');
    }
  }

  static renderDuelsView(duels) {
    const container = document.getElementById('duels-list-container');
    if (!container) return;

    if (!duels || duels.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 8px;">⚔️</div>
          <div style="font-weight: 700; color: #fff;">Открытых вызовов нет</div>
          <div style="font-size: 0.85rem; margin-top: 4px;">Создайте свой первый вызов и отправьте ссылку другу!</div>
        </div>
      `;
      return;
    }

    container.innerHTML = duels.map(d => `
      <div class="duel-card">
        <div class="duel-players">
          <span style="font-size: 1.8rem;">⚔️</span>
          <div>
            <div style="font-family: 'Outfit'; font-size: 0.95rem; font-weight: 800; color: #fff;">
              ${d.creator_username || 'Игрок'} (${d.stake_amount} 🪙)
            </div>
            <div style="font-size: 0.76rem; color: var(--text-secondary);">
              Тур #${d.round_number} • ${d.status === 'open' ? 'Ожидает оппонента' : 'В игре'}
            </div>
          </div>
        </div>
        ${d.status === 'open' ? `
          <button class="btn-accept-duel" data-duel-id="${d.id}">Принять вызов</button>
        ` : `
          <span style="font-size: 0.8rem; font-weight: 800; color: var(--accent-gold);">Активна</span>
        `}
      </div>
    `).join('');
  }

  static renderProfileView(profile, achievements) {
    const cardContainer = document.getElementById('profile-card-container');
    const achContainer = document.getElementById('achievements-grid-container');
    const achCountLabel = document.getElementById('achievements-count-label');

    if (cardContainer && profile) {
      const nextLvlXp = (profile.level ** 2) * 120;
      const curLvlXp = ((profile.level - 1) ** 2) * 120;
      const spanXp = nextLvlXp - curLvlXp;
      const progressXp = profile.current_xp || 0;
      const pct = Math.min(100, Math.max(5, Math.round((progressXp / spanXp) * 100)));

      cardContainer.innerHTML = `
        <div class="gamer-header">
          <div class="gamer-avatar-wrap">🐺</div>
          <div class="gamer-info">
            <div class="gamer-username">${profile.username || 'Каппер'}</div>
            <div class="gamer-title-badge">${profile.title || 'Новичок'}</div>
          </div>
        </div>

        <div style="display: flex; justify-content: space-between; font-size: 0.8rem; font-weight: 800; color: var(--text-secondary);">
          <span>Уровень ${profile.level}</span>
          <span>${progressXp} / ${spanXp} XP</span>
        </div>
        <div class="gamer-xp-bar-wrap">
          <div class="gamer-xp-bar-fill" style="width: ${pct}%;"></div>
        </div>

        <div class="gamer-stats-grid">
          <div class="gamer-stat-item">
            <div class="gamer-stat-val">${profile.win_rate}%</div>
            <div class="gamer-stat-lbl">Винрейт</div>
          </div>
          <div class="gamer-stat-item">
            <div class="gamer-stat-val">${profile.bets_count}</div>
            <div class="gamer-stat-lbl">Прогнозов</div>
          </div>
          <div class="gamer-stat-item">
            <div class="gamer-stat-val">${profile.best_streak} 🔥</div>
            <div class="gamer-stat-lbl">Рекорд стрика</div>
          </div>
        </div>
      `;
    }

    if (achContainer && achievements) {
      const unlockedCount = achievements.filter(a => a.is_unlocked).length;
      if (achCountLabel) achCountLabel.textContent = `${unlockedCount}/${achievements.length}`;

      achContainer.innerHTML = achievements.map(a => `
        <div class="achievement-card rarity-${a.rarity} ${a.is_unlocked ? 'unlocked' : ''}">
          <div class="ach-icon-name">
            <span class="ach-icon">${a.badge_icon}</span>
            <div class="ach-name">${a.name}</div>
          </div>
          <div class="ach-desc">${a.description}</div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
            <span style="font-size: 0.74rem; font-weight: 800; color: var(--accent-gold);">+${a.reward_coins} 🪙</span>
            ${a.is_unlocked && !a.is_claimed ? `
              <button class="btn-claim-achievement" data-ach-id="${a.id}">Забрать</button>
            ` : a.is_claimed ? `
              <span style="font-size: 0.72rem; color: var(--text-muted);">Получено ✓</span>
            ` : ''}
          </div>
        </div>
      `).join('');
    }
  }

  static renderHistory(bets) {
    const container = document.getElementById('history-list-container');
    if (!container) return;

    if (!bets || bets.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
          <div style="font-size: 2rem; margin-bottom: 8px;">📜</div>
          <div>У вас пока нет оформленных прогнозов</div>
        </div>
      `;
      return;
    }

    container.innerHTML = bets.map(b => {
      const statusIcon = b.status === 'won' ? '🎉 Выигрыш' : b.status === 'lost' ? '❌ Проигрыш' : '⏳ В игре';
      const statusColor = b.status === 'won' ? 'var(--color-success)' : b.status === 'lost' ? 'var(--color-danger)' : 'var(--accent-gold)';

      return `
        <div class="match-card" style="margin-bottom: 12px;">
          <div style="display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 0.85rem;">
            <span style="font-weight: 800; color: #fff;">Ставка #${b.id} (${b.bet_type === 'express' ? 'Экспресс' : 'Ординар'})</span>
            <span style="color: ${statusColor}; font-weight: 800;">${statusIcon}</span>
          </div>
          <div style="font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 8px;">
            Сумма: <b>${this.formatNumber(b.amount)} 🪙</b> | Кэф: <b>${b.total_odd.toFixed(2)}</b> | Выигрыш: <b>${this.formatNumber(b.potential_win)} 🪙</b>
          </div>
          <div style="border-top: 1px solid var(--border-subtle); padding-top: 8px;">
            ${(b.items || []).map(i => `
              <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-bottom: 4px;">
                <span>${i.team1_name} vs ${i.team2_name} (${OUTCOME_NAMES[i.outcome_type] || i.outcome_type})</span>
                <span style="font-weight: 800; color: var(--accent-gold);">${Number(i.odd).toFixed(2)}</span>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }).join('');
  }

  static renderLeaderboard(leaders) {
    const podiumEl = document.getElementById('leaderboard-podium');
    const listEl = document.getElementById('leaderboard-list');

    if (!podiumEl || !listEl || !leaders || leaders.length === 0) return;

    const top3 = leaders.slice(0, 3);
    const rest = leaders.slice(3);

    const medals = ['🥇', '🥈', '🥉'];
    podiumEl.innerHTML = top3.map((u, i) => `
      <div class="podium-card ${i === 0 ? 'first' : ''}">
        <div class="podium-medal">${medals[i]}</div>
        <div class="podium-name">${u.username || u.team_name || 'Каппер'}</div>
        <div class="podium-bal">${this.formatNumber(u.balance)} 🪙</div>
      </div>
    `).join('');

    listEl.innerHTML = rest.map((u, idx) => `
      <div class="leader-row">
        <div class="leader-left">
          <span class="leader-rank">#${idx + 4}</span>
          <div>
            <div class="leader-name">${u.username || u.team_name || 'Каппер'}</div>
            <div class="leader-stats">Винрейт: ${Math.round((u.bets_won / Math.max(1, u.bets_count)) * 100)}% (${u.bets_count} ставок)</div>
          </div>
        </div>
        <span class="leader-bal">${this.formatNumber(u.balance)} 🪙</span>
      </div>
    `).join('');
  }
}

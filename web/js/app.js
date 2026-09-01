/**
 * web/js/app.js
 * Main App Controller and Event Handlers for Logovo.bet Telegram Mini App.
 */

import { api } from './api.js';
import { store } from './store.js';
import { tgBridge } from './tg.js';
import { UIRenderer } from './ui.js';
import { ParticleEffects } from './effects.js';

class AppController {
  constructor() {
    this.init();
  }

  async init() {
    // 1. Subscribe UI renderer to store changes
    store.subscribe((state) => {
      UIRenderer.renderHeader(state.user, state.progression);
      UIRenderer.renderBonusBanner(state.bonus);
      UIRenderer.renderTourTabs(state.tours, state.selectedTour);

      const activeTourData = state.tours.find(t => t.round_number === state.selectedTour);
      UIRenderer.renderMatchCards(activeTourData?.matches || [], state.slip);

      UIRenderer.renderBetSlip(state.slip, state.stakeAmount);
      UIRenderer.renderQuestsView(state.quests, state.streak);
      UIRenderer.renderDuelsView(state.duels);
      UIRenderer.renderProfileView(state.profile, state.achievements);
      UIRenderer.renderHistory(state.myBets);
      UIRenderer.renderLeaderboard(state.leaderboard);
    });

    // 2. Setup DOM Events
    this.bindEvents();

    // 3. Initial Data Load
    await this.loadInitialData();
  }

  async loadInitialData() {
    try {
      const data = await api.getBootstrap();
      if (data.status === 'ok') {
        store.setUser(data.user, data.bonus);

        if (!data.user.has_access) {
          const lockScreen = document.getElementById('lab-lock-screen');
          if (lockScreen) lockScreen.style.display = 'flex';
          const nav = document.querySelector('.bottom-nav');
          if (nav) nav.style.display = 'none';
          const drawer = document.getElementById('slip-drawer');
          if (drawer) drawer.style.display = 'none';
          const views = document.querySelector('.views-container');
          if (views) views.style.display = 'none';
          return;
        }

        // Fetch markets
        const toursData = await api.getTours();
        if (toursData.status === 'ok') {
          store.setTours(toursData.tours);
        }

        // Fetch progression & quests
        this.fetchProgressionData();
      }
    } catch (err) {
      console.error("Failed to bootstrap app:", err);
    }
  }

  async fetchProgressionData() {
    try {
      const res = await api.getProgression();
      if (res.status === 'ok') {
        store.setProgression(
          res.progression,
          res.streak,
          res.quests,
          res.unclaimed_quests_count,
          res.unclaimed_achievements_count
        );
      }
    } catch (e) {
      console.warn("Could not load progression:", e);
    }
  }

  bindEvents() {
    // Navigation Tabs
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        this.switchView(view);
      });
    });

    // Header Pills
    document.getElementById('header-balance-btn')?.addEventListener('click', () => {
      this.switchView('history');
    });
    document.getElementById('header-level-btn')?.addEventListener('click', () => {
      this.switchView('profile');
    });

    // Tour Tab Click
    document.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('.tour-tab-btn');
      if (tabBtn) {
        const tourNum = parseInt(tabBtn.dataset.tour);
        store.setSelectedTour(tourNum);
        tgBridge.hapticImpact('light');
      }
    });

    // Odds Button Click
    document.addEventListener('click', (e) => {
      const oddBtn = e.target.closest('.odd-btn');
      if (oddBtn) {
        const matchId = parseInt(oddBtn.dataset.matchId);
        const outcome = oddBtn.dataset.outcome;
        const odd = parseFloat(oddBtn.dataset.odd);

        const currentTourMatches = store.state.tours.find(t => t.round_number === store.state.selectedTour)?.matches || [];
        const match = currentTourMatches.find(m => m.match_id === matchId);

        if (match) {
          store.toggleSelection(match, outcome, odd);
        }
      }
    });

    // Accordion Toggle for Extra Markets
    document.addEventListener('click', (e) => {
      const toggle = e.target.closest('.extra-markets-toggle');
      if (toggle) {
        const targetId = toggle.dataset.target;
        const panel = document.getElementById(targetId);
        if (panel) {
          const isOpen = panel.classList.toggle('open');
          const arrow = toggle.querySelector('.arrow-icon');
          if (arrow) arrow.textContent = isOpen ? '▲' : '▼';
          tgBridge.hapticImpact('light');
        }
      }
    });

    // Slip Header Click (Expand/Collapse)
    document.getElementById('slip-bar-collapsed')?.addEventListener('click', (e) => {
      if (e.target.closest('.btn-remove-item')) return;
      const drawer = document.getElementById('slip-drawer');
      const isExpanded = drawer.classList.toggle('expanded');
      const label = document.getElementById('slip-toggle-label');
      if (label) label.textContent = isExpanded ? 'Свернуть' : 'Открыть';
      tgBridge.hapticImpact('light');
    });

    // Remove Item from Slip
    document.addEventListener('click', (e) => {
      const removeBtn = e.target.closest('.btn-remove-item');
      if (removeBtn) {
        const matchId = parseInt(removeBtn.dataset.removeMatch);
        store.removeSelection(matchId);
      }
    });

    // Clear Slip
    document.getElementById('btn-clear-slip')?.addEventListener('click', () => {
      store.clearSlip();
      tgBridge.hapticImpact('medium');
    });

    // Quick Stake Chips
    document.querySelectorAll('.stake-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.stake-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');

        const val = chip.dataset.amount;
        if (val === 'all') {
          const bal = store.state.user?.balance || 0;
          store.setStakeAmount(bal);
        } else {
          store.setStakeAmount(parseInt(val));
        }
        tgBridge.hapticImpact('light');
      });
    });

    // Custom Stake Input
    document.getElementById('stake-input')?.addEventListener('input', (e) => {
      store.setStakeAmount(e.target.value);
    });

    // Submit Bet CTA
    document.getElementById('btn-submit-prediction')?.addEventListener('click', () => {
      this.handlePlaceBet();
    });

    // Claim Daily Bonus
    document.addEventListener('click', async (e) => {
      if (e.target && e.target.id === 'btn-claim-daily-bonus') {
        tgBridge.hapticImpact('medium');
        try {
          const res = await api.claimBonus();
          if (res.status === 'ok') {
            tgBridge.hapticNotification('success');
            ParticleEffects.burstConfetti();
            store.setUser({ ...store.state.user, balance: res.new_balance }, { can_claim: false, cooldown_seconds: 86400 });
            this.showSuccessModal("🎁 Бонус получен!", `На ваш баланс зачислено <b>+${res.claimed_amount} 🪙</b>!`);
            this.fetchProgressionData();
          }
        } catch (err) {
          tgBridge.hapticNotification('error');
          alert(err.message);
        }
      }
    });

    // Claim Quest
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-claim-quest');
      if (btn) {
        const questId = parseInt(btn.dataset.questId);
        tgBridge.hapticImpact('medium');
        try {
          const res = await api.claimQuest(questId);
          if (res.status === 'ok') {
            tgBridge.hapticNotification('success');
            ParticleEffects.burstConfetti();
            this.showSuccessModal("🎯 Задание выполнено!", res.message);
            this.fetchProgressionData();
            // Update balance
            if (store.state.user) {
              store.setUser({ ...store.state.user, balance: store.state.user.balance + res.reward.coins });
            }
          }
        } catch (err) {
          tgBridge.hapticNotification('error');
          alert(err.message);
        }
      }
    });

    // Claim Achievement
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-claim-achievement');
      if (btn) {
        const achId = btn.dataset.achId;
        tgBridge.hapticImpact('medium');
        try {
          const res = await api.claimAchievement(achId);
          if (res.status === 'ok') {
            tgBridge.hapticNotification('success');
            ParticleEffects.burstConfetti();
            this.showSuccessModal("🏆 Достижение получено!", res.message);
            this.fetchProgressionData();
            if (store.state.user) {
              store.setUser({ ...store.state.user, balance: store.state.user.balance + res.reward.coins });
            }
          }
        } catch (err) {
          tgBridge.hapticNotification('error');
          alert(err.message);
        }
      }
    });

    // Open Leaderboard Modal
    document.getElementById('btn-toggle-leaderboard-modal')?.addEventListener('click', async () => {
      try {
        const res = await api.getLeaderboard();
        if (res.status === 'ok') {
          store.setLeaderboard(res.leaderboard, res.my_rank);
        }
      } catch (e) {
        console.error(e);
      }
      document.getElementById('leaderboard-modal')?.classList.add('open');
    });

    // Open Create Duel Modal
    document.getElementById('btn-open-create-duel')?.addEventListener('click', () => {
      const currentMatches = store.state.tours.find(t => t.round_number === store.state.selectedTour)?.matches || [];
      const selector = document.getElementById('duel-matches-selector');
      if (selector) {
        selector.innerHTML = currentMatches.map(m => `
          <div style="background: var(--bg-tertiary); padding: 8px; border-radius: var(--radius-xs); margin-bottom: 6px; font-size: 0.8rem;">
            <div style="font-weight: 700; margin-bottom: 4px;">${m.team1_name} vs ${m.team2_name}</div>
            <select class="duel-pick-select" data-match-id="${m.match_id}" style="width: 100%; background: #080a0e; color: #fff; padding: 6px; border-radius: 4px; border: 1px solid var(--border-subtle);">
              <option value="p1">П1 (${m.odd_p1})</option>
              <option value="x">Ничья (${m.odd_x})</option>
              <option value="p2">П2 (${m.odd_p2})</option>
            </select>
          </div>
        `).join('');
      }
      document.getElementById('create-duel-modal')?.classList.add('open');
    });

    // Confirm Create Duel
    document.getElementById('btn-confirm-create-duel')?.addEventListener('click', async () => {
      const stake = parseInt(document.getElementById('duel-stake-input')?.value || 500);
      const picks = {};
      const matchIds = [];
      document.querySelectorAll('.duel-pick-select').forEach(sel => {
        const mId = parseInt(sel.dataset.matchId);
        matchIds.push(mId);
        picks[mId] = sel.value;
      });

      try {
        const res = await api.createDuel({
          stake,
          round_number: store.state.selectedTour || 1,
          match_ids: matchIds,
          picks
        });
        if (res.status === 'ok') {
          tgBridge.hapticNotification('success');
          ParticleEffects.burstConfetti();
          document.getElementById('create-duel-modal')?.classList.remove('open');
          this.showSuccessModal("⚔️ Вызов создан!", `Дуэль на <b>${stake} 🪙</b> опубликована в Арене!`);
          this.switchView('duels');
        }
      } catch (err) {
        tgBridge.hapticNotification('error');
        alert(err.message);
      }
    });

    // Close Modals
    document.querySelectorAll('.btn-modal-close').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('open'));
      });
    });

    // Close locked app
    document.getElementById('btn-close-locked-app')?.addEventListener('click', () => {
      tgBridge.close();
    });
  }

  async handlePlaceBet() {
    if (store.state.slip.length === 0) return;

    const amount = store.state.stakeAmount;
    const balance = store.state.user?.balance || 0;

    if (amount > balance) {
      tgBridge.hapticNotification('error');
      alert("Недостаточно монет на балансе!");
      return;
    }

    const selections = store.state.slip.map(s => ({
      match_id: s.match_id,
      outcome: s.outcome,
      odd: s.odd
    }));

    try {
      const res = await api.placePrediction(amount, selections);
      if (res.status === 'ok') {
        tgBridge.hapticNotification('success');
        ParticleEffects.burstConfetti();
        store.setUser({ ...store.state.user, balance: res.new_balance });
        store.clearSlip();
        this.showSuccessModal("🎉 Прогноз принят!", `Ставка на <b>${amount} 🪙</b> успешно оформлена.<br>Удачи в туре!`);
        this.fetchProgressionData();
      }
    } catch (err) {
      tgBridge.hapticNotification('error');
      alert(err.message);
    }
  }

  async switchView(viewName) {
    tgBridge.hapticImpact('light');
    store.setActiveView(viewName);

    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === viewName);
    });

    document.querySelectorAll('.view-section').forEach(sec => {
      sec.classList.remove('active');
    });

    const target = document.getElementById(`view-${viewName}`);
    if (target) {
      target.classList.add('active');
    }

    // Lazy data fetches
    if (viewName === 'history') {
      try {
        const data = await api.getPredictions();
        if (data.status === 'ok') {
          store.setMyBets(data.bets);
        }
      } catch (err) {
        console.error(err);
      }
    } else if (viewName === 'quests') {
      this.fetchProgressionData();
    } else if (viewName === 'duels') {
      try {
        const res = await api.getDuels();
        if (res.status === 'ok') {
          store.setDuels(res.duels);
        }
      } catch (err) {
        console.error(err);
      }
    } else if (viewName === 'profile') {
      try {
        const uid = store.state.user?.id;
        if (uid) {
          const profRes = await api.getProfile(uid);
          const achRes = await api.getAchievements();
          if (profRes.status === 'ok') store.setProfile(profRes.profile);
          if (achRes.status === 'ok') store.setAchievements(achRes.achievements);
        }
      } catch (err) {
        console.error(err);
      }
    }
  }

  showSuccessModal(title, desc) {
    const modal = document.getElementById('general-success-modal');
    const titleEl = document.getElementById('success-modal-title');
    const descEl = document.getElementById('success-modal-desc');

    if (titleEl) titleEl.innerHTML = title;
    if (descEl) descEl.innerHTML = desc;
    if (modal) modal.classList.add('open');
  }
}

// Bootstrap on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  window.appController = new AppController();
});

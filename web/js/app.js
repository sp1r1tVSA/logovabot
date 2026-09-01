/**
 * web/js/app.js
 * Main App Controller and Event Handlers for Logovo.bet Telegram Mini App.
 */

import { api } from './api.js';
import { store } from './store.js';
import { tgBridge } from './tg.js';
import { UIRenderer } from './ui.js';

class AppController {
  constructor() {
    this.init();
  }

  async init() {
    // 1. Subscribe UI renderer to store changes
    store.subscribe((state) => {
      UIRenderer.renderHeader(state.user);
      UIRenderer.renderBonusBanner(state.bonus);
      UIRenderer.renderTourTabs(state.tours, state.selectedTour);

      const activeTourData = state.tours.find(t => t.round_number === state.selectedTour);
      UIRenderer.renderMatchCards(activeTourData?.matches || [], state.slip);

      UIRenderer.renderBetSlip(
        state.slip,
        store.getTotalOdd(),
        state.stakeAmount,
        store.getPotentialWin()
      );
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
          this.showModal('lab-restricted-modal');
          return;
        }

        // Fetch markets
        const toursData = await api.getTours();
        if (toursData.status === 'ok') {
          store.setTours(toursData.tours);
        }
      }
    } catch (err) {
      console.error("Failed to bootstrap app:", err);
    }
  }

  bindEvents() {
    // Navigation
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const view = btn.dataset.view;
        this.switchView(view);
      });
    });

    // Header Balance Click -> Open History/Wallet
    document.getElementById('header-balance-btn')?.addEventListener('click', () => {
      this.switchView('history');
    });

    // Tour Tab Switching
    document.getElementById('tour-tabs-container')?.addEventListener('click', (e) => {
      const tab = e.target.closest('.tour-tab-btn');
      if (tab) {
        tgBridge.hapticImpact('light');
        const tourNum = parseInt(tab.dataset.tour);
        store.setSelectedTour(tourNum);
      }
    });

    // Match Card Clicks (Odds and Extra Markets Toggle)
    document.getElementById('matches-list-container')?.addEventListener('click', (e) => {
      // Toggle extra markets
      const toggleBtn = e.target.closest('.extra-markets-toggle');
      if (toggleBtn) {
        const mId = toggleBtn.dataset.matchToggle;
        const panel = document.getElementById(`extra-panel-${mId}`);
        if (panel) panel.classList.toggle('open');
        return;
      }

      // Odd button click
      const oddBtn = e.target.closest('.odd-btn');
      if (oddBtn) {
        const matchCard = oddBtn.closest('.match-card');
        const matchId = parseInt(matchCard.dataset.matchId);
        const outcome = oddBtn.dataset.outcome;
        const odd = parseFloat(oddBtn.dataset.odd);

        // Find match in current tour
        const curTour = store.state.tours.find(t => t.round_number === store.state.selectedTour);
        const match = curTour?.matches?.find(m => m.match_id === matchId);

        if (match) {
          store.toggleSelection(match, outcome, odd);
        }
      }
    });

    // Bet Slip Bar Expand / Collapse
    document.getElementById('slip-bar-collapsed')?.addEventListener('click', () => {
      tgBridge.hapticImpact('light');
      const drawer = document.getElementById('slip-drawer');
      if (drawer) {
        drawer.classList.toggle('expanded');
        const isExp = drawer.classList.contains('expanded');
        const label = document.getElementById('slip-toggle-label');
        if (label) label.textContent = isExp ? 'Свернуть' : 'Открыть';
      }
    });

    // Remove item from slip
    document.getElementById('slip-items-container')?.addEventListener('click', (e) => {
      const removeBtn = e.target.closest('.btn-remove-item');
      if (removeBtn) {
        const matchId = parseInt(removeBtn.dataset.removeId);
        store.removeSelection(matchId);
      }
    });

    // Clear slip
    document.getElementById('btn-clear-slip')?.addEventListener('click', () => {
      store.clearSlip();
      const drawer = document.getElementById('slip-drawer');
      drawer?.classList.remove('expanded');
    });

    // Stake chips
    document.querySelectorAll('.stake-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        tgBridge.hapticImpact('light');
        document.querySelectorAll('.stake-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');

        const val = chip.dataset.amount;
        if (val === 'all') {
          store.setStakeAmount(store.state.user?.balance || 0);
        } else {
          store.setStakeAmount(parseInt(val));
        }

        const input = document.getElementById('stake-input');
        if (input) input.value = store.state.stakeAmount;
      });
    });

    // Stake custom input
    document.getElementById('stake-input')?.addEventListener('input', (e) => {
      const val = parseInt(e.target.value) || 0;
      store.setStakeAmount(val);
    });

    // Submit Bet
    document.getElementById('btn-submit-prediction')?.addEventListener('click', async () => {
      await this.handlePlaceBet();
    });

    // Claim Daily Bonus
    document.addEventListener('click', async (e) => {
      if (e.target && e.target.id === 'btn-claim-daily-bonus') {
        tgBridge.hapticImpact('medium');
        try {
          const res = await api.claimBonus();
          if (res.status === 'ok') {
            tgBridge.hapticNotification('success');
            store.setUser({ ...store.state.user, balance: res.new_balance }, { can_claim: false, cooldown_seconds: 86400 });
            this.showSuccessModal("🎁 Бонус получен!", `На ваш баланс зачислено <b>+${res.claimed_amount} 🪙</b>!`);
          }
        } catch (err) {
          tgBridge.hapticNotification('error');
          alert(err.message);
        }
      }
    });

    // Close Modals
    document.querySelectorAll('.btn-modal-close').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('open'));
      });
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
        store.setUser({ ...store.state.user, balance: res.new_balance });
        store.clearSlip();
        this.showSuccessModal("🎉 Прогноз принят!", `Ставка на <b>${amount} 🪙</b> успешно оформлена.<br>Удачи в туре!`);
      }
    } catch (err) {
      tgBridge.hapticNotification('error');
      alert(err.message);
    }
  }

  async switchView(viewName) {
    tgBridge.hapticImpact('light');
    document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    const targetView = document.getElementById(`view-${viewName}`);
    const targetNav = document.querySelector(`.nav-item[data-view="${viewName}"]`);

    if (targetView) targetView.classList.add('active');
    if (targetNav) targetNav.classList.add('active');

    // Load dynamic data on view open
    if (viewName === 'history') {
      try {
        const betsData = await api.getPredictions();
        if (betsData.status === 'ok') {
          UIRenderer.renderPredictions(betsData.bets);
        }
      } catch (e) {}
    } else if (viewName === 'leaderboard') {
      try {
        const lbData = await api.getLeaderboard();
        if (lbData.status === 'ok') {
          UIRenderer.renderLeaderboard(lbData.leaders, lbData.my_rank);
        }
      } catch (e) {}
    }
  }

  showModal(modalId) {
    document.getElementById(modalId)?.classList.add('open');
  }

  showSuccessModal(title, message) {
    const modal = document.getElementById('general-success-modal');
    if (modal) {
      document.getElementById('success-modal-title').innerHTML = title;
      document.getElementById('success-modal-desc').innerHTML = message;
      modal.classList.add('open');
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  new AppController();
});

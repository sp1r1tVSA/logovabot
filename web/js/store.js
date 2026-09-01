/**
 * web/js/store.js
 * Centralized Reactive State Store for Logovo.bet.
 */

import { tgBridge } from './tg.js';

class StateStore {
  constructor() {
    this.state = {
      user: null,
      bonus: { can_claim: false, cooldown_seconds: 0, reward_amount: 250 },
      tours: [],
      selectedTour: null,
      slip: [], // [ { match_id, outcome, odd, team1_name, team2_name, tour }, ... ]
      stakeAmount: 100,
      activeView: 'lobby',
      myBets: [],
      leaderboard: [],
      myRank: null
    };
    this.listeners = new Set();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch (e) {
        console.error("Store listener error:", e);
      }
    }
  }

  setUser(user, bonus) {
    this.state.user = user;
    if (bonus) this.state.bonus = bonus;
    this.notify();
  }

  setTours(tours) {
    this.state.tours = tours || [];
    if (this.state.tours.length > 0 && !this.state.selectedTour) {
      this.state.selectedTour = this.state.tours[0].round_number;
    }
    this.notify();
  }

  setSelectedTour(tourNumber) {
    this.state.selectedTour = tourNumber;
    this.notify();
  }

  setActiveView(viewName) {
    this.state.activeView = viewName;
    this.notify();
  }

  setStakeAmount(amount) {
    const bal = this.state.user?.balance || 0;
    this.state.stakeAmount = Math.max(10, Math.min(amount, bal));
    this.notify();
  }

  toggleSelection(match, outcome, odd) {
    tgBridge.hapticImpact('medium');
    const existingIdx = this.state.slip.findIndex(s => s.match_id === match.match_id);

    if (existingIdx >= 0) {
      if (this.state.slip[existingIdx].outcome === outcome) {
        // Toggle OFF
        this.state.slip.splice(existingIdx, 1);
      } else {
        // Replace outcome for the same match
        this.state.slip[existingIdx] = {
          match_id: match.match_id,
          outcome,
          odd,
          team1_name: match.team1_name,
          team2_name: match.team2_name,
          tour: match.tour
        };
      }
    } else {
      // Add new selection
      this.state.slip.push({
        match_id: match.match_id,
        outcome,
        odd,
        team1_name: match.team1_name,
        team2_name: match.team2_name,
        tour: match.tour
      });
    }

    this.notify();
  }

  removeSelection(matchId) {
    tgBridge.hapticImpact('light');
    this.state.slip = this.state.slip.filter(s => s.match_id !== matchId);
    this.notify();
  }

  clearSlip() {
    tgBridge.hapticImpact('light');
    this.state.slip = [];
    this.notify();
  }

  getTotalOdd() {
    if (this.state.slip.length === 0) return 1.0;
    if (this.state.slip.length === 1) return this.state.slip[0].odd;
    
    // Express multiplication
    let total = 1.0;
    for (const item of this.state.slip) {
      total *= item.odd;
    }
    return Math.min(100.0, Math.round(total * 100) / 100);
  }

  getPotentialWin() {
    const totalOdd = this.getTotalOdd();
    return Math.floor(this.state.stakeAmount * totalOdd);
  }
}

export const store = new StateStore();

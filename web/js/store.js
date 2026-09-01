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
      myRank: null,
      progression: { level: 1, current_xp: 0, total_xp_earned: 0, equipped_title: 'Новичок' },
      streak: { streak: 1, best_streak: 1, streak_shield_count: 1 },
      quests: [],
      achievements: [],
      duels: [],
      profile: null,
      unclaimedQuestsCount: 0,
      unclaimedAchievementsCount: 0
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

  setProgression(progression, streak, quests, unclaimedQuests, unclaimedAch) {
    if (progression) this.state.progression = progression;
    if (streak) this.state.streak = streak;
    if (quests) this.state.quests = quests;
    this.state.unclaimedQuestsCount = unclaimedQuests || 0;
    this.state.unclaimedAchievementsCount = unclaimedAch || 0;
    this.notify();
  }

  setAchievements(achievements) {
    this.state.achievements = achievements || [];
    this.notify();
  }

  setDuels(duels) {
    this.state.duels = duels || [];
    this.notify();
  }

  setProfile(profile) {
    this.state.profile = profile;
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

  setMyBets(bets) {
    this.state.myBets = bets || [];
    this.notify();
  }

  setLeaderboard(leaderboard, myRank) {
    this.state.leaderboard = leaderboard || [];
    this.state.myRank = myRank;
    this.notify();
  }

  // --- Bet Slip Operations ---
  toggleSelection(match, outcome, odd) {
    const existingIndex = this.state.slip.findIndex(s => s.match_id === match.match_id);

    if (existingIndex >= 0) {
      const currentSelection = this.state.slip[existingIndex];
      if (currentSelection.outcome === outcome) {
        // Deselect
        this.state.slip.splice(existingIndex, 1);
        tgBridge.hapticImpact('light');
      } else {
        // Switch outcome in same match
        this.state.slip[existingIndex] = {
          match_id: match.match_id,
          outcome,
          odd: parseFloat(odd),
          team1_name: match.team1_name,
          team2_name: match.team2_name,
          tour: match.tour
        };
        tgBridge.hapticImpact('medium');
      }
    } else {
      // Add new selection
      this.state.slip.push({
        match_id: match.match_id,
        outcome,
        odd: parseFloat(odd),
        team1_name: match.team1_name,
        team2_name: match.team2_name,
        tour: match.tour
      });
      tgBridge.hapticImpact('medium');
    }

    this.notify();
  }

  removeSelection(matchId) {
    this.state.slip = this.state.slip.filter(s => s.match_id !== matchId);
    tgBridge.hapticImpact('light');
    this.notify();
  }

  clearSlip() {
    this.state.slip = [];
    this.notify();
  }

  setStakeAmount(amount) {
    this.state.stakeAmount = Math.max(10, parseInt(amount) || 10);
    this.notify();
  }

  // --- Computed Getters ---
  getTotalOdd() {
    if (this.state.slip.length === 0) return 1.0;
    const prod = this.state.slip.reduce((acc, s) => acc * s.odd, 1.0);
    return Math.round(prod * 100) / 100;
  }

  getPotentialWin() {
    const odd = this.getTotalOdd();
    return Math.round(this.state.stakeAmount * odd);
  }
}

export const store = new StateStore();

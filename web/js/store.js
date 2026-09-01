/**
 * web/js/store.js
 * Centralized Reactive State Store for Logovo.bet (v2.0).
 */

import { tgBridge } from './tg.js';

class StateStore {
  constructor() {
    this.state = {
      user: null,
      bonus: { can_claim: false, cooldown_seconds: 0, reward_amount: 250 },
      tours: [],
      selectedTour: null,
      marketCategoryFilter: 'all',
      searchQuery: '',
      slip: [], // [ { match_id, outcome, odd, market_id, selection_id, selection_name, team1_name, team2_name, tour }, ... ]
      stakeAmount: 100,
      activeView: 'lobby', // 'lobby' | 'match_center' | 'tournaments' | 'history' | 'profile'
      selectedMatchId: null,
      matchCenterSubTab: 'markets', // 'markets' | 'stats' | 'insights'
      matchDetail: null,
      matchStats: null,
      matchH2H: null,
      matchInsights: null,
      matchLive: null,
      matchMarkets: [],
      standings: [],
      results: [],
      topScorers: [],
      myBets: [],
      myBetsFilter: 'all',
      savedCoupons: [],
      favorites: [],
      notifications: [],
      myStats: null,
      leaderboard: [],
      myRank: null,
      progression: { level: 1, current_xp: 0, total_xp_earned: 0, equipped_title: 'Новичок' },
      streak: { streak: 1, best_streak: 1, streak_shield_count: 1 },
      achievements: [],
      profile: null,
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

  setProgression(progression, streak, unclaimedAch) {
    if (progression) this.state.progression = progression;
    if (streak) this.state.streak = streak;
    this.state.unclaimedAchievementsCount = unclaimedAch || 0;
    this.notify();
  }

  setAchievements(achievements) {
    this.state.achievements = achievements || [];
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

  setMarketCategoryFilter(category) {
    this.state.marketCategoryFilter = category;
    this.notify();
  }

  setSearchQuery(query) {
    this.state.searchQuery = query || '';
    this.notify();
  }

  setActiveView(viewName) {
    this.state.activeView = viewName;
    this.notify();
  }

  setSelectedMatch(matchId, detail, stats, h2h, insights, live, markets) {
    this.state.selectedMatchId = matchId;
    if (detail) this.state.matchDetail = detail;
    if (stats) this.state.matchStats = stats;
    if (h2h) this.state.matchH2H = h2h;
    if (insights) this.state.matchInsights = insights;
    if (live) this.state.matchLive = live;
    if (markets) this.state.matchMarkets = markets;
    this.state.matchCenterSubTab = 'markets';
    this.notify();
  }

  setMatchCenterSubTab(tab) {
    this.state.matchCenterSubTab = tab || 'markets';
    this.notify();
  }

  setTournamentData(standings, results, topScorers) {
    if (standings) this.state.standings = standings;
    if (results) this.state.results = results;
    if (topScorers) this.state.topScorers = topScorers;
    this.notify();
  }

  setMyBets(bets, filter = null) {
    this.state.myBets = bets || [];
    if (filter) this.state.myBetsFilter = filter;
    this.notify();
  }

  setSavedCoupons(saved) {
    this.state.savedCoupons = saved || [];
    this.notify();
  }

  setFavorites(favorites) {
    this.state.favorites = favorites || [];
    this.notify();
  }

  setNotifications(notifications) {
    this.state.notifications = notifications || [];
    this.notify();
  }

  setMyStats(stats) {
    this.state.myStats = stats;
    this.notify();
  }

  setLeaderboard(leaderboard, myRank) {
    this.state.leaderboard = leaderboard || [];
    this.state.myRank = myRank;
    this.notify();
  }

  // --- Smart Bet Slip Operations ---
  toggleSelection(match, outcome, odd, extra = {}) {
    const mId = match.match_id || match.id;
    const existingIndex = this.state.slip.findIndex(s => s.match_id === mId);

    if (existingIndex >= 0) {
      const current = this.state.slip[existingIndex];
      if (current.outcome === outcome) {
        // Deselect
        this.state.slip.splice(existingIndex, 1);
        tgBridge.hapticImpact('light');
      } else {
        // Switch pick in same match
        this.state.slip[existingIndex] = {
          match_id: mId,
          outcome,
          odd: parseFloat(odd),
          market_id: extra.market_id || null,
          selection_id: extra.selection_id || null,
          selection_name: extra.selection_name || outcome.toUpperCase(),
          team1_name: match.team1_name || match.player1_team || 'Хозяева',
          team2_name: match.team2_name || match.player2_team || 'Гости',
          tour: match.tour || match.round_number || 1
        };
        tgBridge.hapticImpact('medium');
      }
    } else {
      // Add new selection
      this.state.slip.push({
        match_id: mId,
        outcome,
        odd: parseFloat(odd),
        market_id: extra.market_id || null,
        selection_id: extra.selection_id || null,
        selection_name: extra.selection_name || outcome.toUpperCase(),
        team1_name: match.team1_name || match.player1_team || 'Хозяева',
        team2_name: match.team2_name || match.player2_team || 'Гости',
        tour: match.tour || match.round_number || 1
      });
      tgBridge.hapticImpact('medium');
    }
    this.notify();
  }

  loadCouponSelections(selections) {
    this.state.slip = selections || [];
    tgBridge.hapticNotification('success');
    this.notify();
  }

  removeSelection(matchId) {
    this.state.slip = this.state.slip.filter(s => s.match_id !== matchId);
    tgBridge.hapticImpact('light');
    this.notify();
  }

  clearSlip() {
    this.state.slip = [];
    tgBridge.hapticImpact('light');
    this.notify();
  }

  setStakeAmount(amount) {
    this.state.stakeAmount = Math.max(10, parseInt(amount) || 0);
    this.notify();
  }

  // --- Derived Calculations ---
  getTotalOdd() {
    if (this.state.slip.length === 0) return 1.0;
    const rawOdd = this.state.slip.reduce((acc, item) => acc * item.odd, 1.0);
    return Math.round(rawOdd * 100) / 100;
  }

  getPotentialWin() {
    const totalOdd = this.getTotalOdd();
    return Math.floor(this.state.stakeAmount * totalOdd);
  }

  isSelectionActive(matchId, outcome) {
    return this.state.slip.some(s => s.match_id === matchId && s.outcome === outcome);
  }
}

export const store = new StateStore();

export interface MarketSummary {
  ist_time: string;
  market_status: string;
  market_open: boolean;
  nifty: {
    symbol: string;
    price: number | null;
    change: number | null;
    pchange: number | null;
    collected_at: string;
  };
  banknifty: {
    symbol: string;
    price: number | null;
    change: number | null;
    pchange: number | null;
    collected_at: string;
  };
  india_vix: {
    symbol: string;
    price: number | null;
    change: number | null;
    pchange: number | null;
  };
  cloud_sync: {
    status: string;
    last_snapshot: string;
    today_count: number;
  };
}

export interface ExpiryItem {
  expiry: string;
  dte: number;
}

export interface OptionLegGreeks {
  ltp: number;
  iv: number;
  delta: number;
  theta: number;
  volume: number;
  oi: number;
}

export interface OptionChainRow {
  strike: number;
  is_atm: boolean;
  call: OptionLegGreeks;
  put: OptionLegGreeks;
}

export interface OptionChainPayload {
  symbol: string;
  spot: number;
  expiry: string;
  dte: number;
  atm_strike: number;
  pcr: number;
  max_pain: number;
  straddle_ltp: number;
  call_total_oi: number;
  put_total_oi: number;
  rows: OptionChainRow[];
}

export interface VolSurfacePayload {
  symbol: string;
  spot: number;
  x_grid: number[];
  y_grid: number[];
  z_matrix: number[][];
  atm_ridge: {
    x: number[];
    y: number[];
    z: number[];
    expiries: string[];
  };
  smile_curves: Array<{
    expiry: string;
    dte: number;
    strikes: number[];
    ivs: number[];
  }>;
  term_structure: {
    dtes: number[];
    ivs: number[];
    expiries: string[];
    regime: string;
  };
  diagnostics: {
    interp_method: string;
    space_mode: string;
    iv_mode: string;
    range_pct: number;
  };
}

export interface TradeRadar {
  symbol: string;
  spot: number;
  expiry: string;
  dte: number;
  atm_strike: number;
  atm_iv: number;
  expected_move_pts: number;
  expected_move_pct: number;
  expected_range_low: number;
  expected_range_high: number;
  straddle_be_low: number;
  straddle_be_high: number;
  straddle_ltp: number;
  bias_title: string;
  bias_action: string;
  synthetic_basis_pts: number;
  notes: string[];
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface CandleVolume {
  time: string;
  value: number;
  color: string;
}

export interface CandlePayload {
  symbol: string;
  count: number;
  last_price: number;
  candles: Candle[];
  volumes: CandleVolume[];
}

export interface BacktestRun {
  id: string;
  name: string;
  symbol: string;
  start: string;
  end: string;
  engine: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  total_net_pnl: number;
  total_gross_pnl: number;
  total_charges: number;
  total_slippage: number;
  trades: any[];
}

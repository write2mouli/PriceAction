#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
using NinjaTrader.NinjaScript.Indicators;
#endregion

// Mirror of docs/strategy-spec.md. When this file disagrees with the spec, fix the
// spec first, validate in python/, then propagate here. Default chart: /ES 2000 tick.
namespace NinjaTrader.NinjaScript.Strategies
{
    public class ESPriceAction : Strategy
    {
        // ------------------------------------------------------------------
        // Internal types
        // ------------------------------------------------------------------
        private class Pivot { public int Bar; public double Price; public bool IsHigh; }

        private class Trendline
        {
            public int X0; public double Y0;
            public int X1; public double Y1;
            public bool IsBull;
            public int? BrokenAt;
            public double Slope => (X1 == X0) ? 0 : (Y1 - Y0) / (X1 - X0);
            public double ValueAt(int x) => Y0 + Slope * (x - X0);
        }

        private class Range
        {
            public int Start; public double Top; public double Bottom; public int? BrokenAt;
        }

        private enum Trend { Undefined, Bull, Bear, TradingRange }

        // ------------------------------------------------------------------
        // State
        // ------------------------------------------------------------------
        private EMA emaInd;
        private ATR atrInd;
        private readonly List<Pivot> pivots = new List<Pivot>();
        private Trendline activeBullTL;
        private Trendline activeBearTL;
        private int? brokenBullTLAt;
        private int? brokenBearTLAt;
        private Range activeRange;
        private readonly List<double> srLevels = new List<double>();

        // Pullback state — bull
        private bool bullPbActive;
        private int bullPbStart;
        private double bullPbExtremeLow;
        private bool bullPbH1Seen;
        private bool bullPbH2Seen;
        private double bullPbLastHigh;

        // Pullback state — bear
        private bool bearPbActive;
        private int bearPbStart;
        private double bearPbExtremeHigh;
        private bool bearPbL1Seen;
        private bool bearPbL2Seen;
        private double bearPbLastLow;

        // Tick size (refresh in DataLoaded)
        private double tick = 0.25;

        // Risk tracking
        private double dailyLossR;
        private int dailyTradeCount;
        private DateTime lastSessionDate = DateTime.MinValue;
        private int cooldownUntilBar = -1;

        // New-extreme cooldown
        private int lastNewExtremeBarLong = -10000;
        private int lastNewExtremeBarShort = -10000;
        private double lastConfirmedShPrice = double.MinValue;
        private double lastConfirmedSlPrice = double.MaxValue;

        // FB-armed state (§9.3.1)
        private int fbArmedLongBar = -1;  private double fbArmedLongExcLow = 0;
        private int fbArmedShortBar = -1; private double fbArmedShortExcHigh = 0;

        // ------------------------------------------------------------------
        // Inputs (spec §10)
        // ------------------------------------------------------------------
        #region Detection inputs
        [NinjaScriptProperty, Range(5, 200), Display(Name="EMA length", Order=1, GroupName="01 Detection")]
        public int EmaLength { get; set; } = 21;

        [NinjaScriptProperty, Range(5, 50), Display(Name="ATR length", Order=2, GroupName="01 Detection")]
        public int AtrLength { get; set; } = 14;

        [NinjaScriptProperty, Range(1, 10), Display(Name="Swing strength", Order=3, GroupName="01 Detection")]
        public int SwingStrength { get; set; } = 3;

        [NinjaScriptProperty, Range(0, 40), Display(Name="EMA proximity (ticks)", Order=4, GroupName="01 Detection")]
        public int EmaProximityTicks { get; set; } = 4;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="Min close strength", Order=5, GroupName="01 Detection")]
        public double MinCloseStrength { get; set; } = 0.60;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="Min body fraction", Order=6, GroupName="01 Detection")]
        public double MinBodyFraction { get; set; } = 0.40;

        [NinjaScriptProperty, Range(1.0, 5.0), Display(Name="Max signal-bar range (xATR)", Order=7, GroupName="01 Detection")]
        public double MaxSignalRangeAtr { get; set; } = 2.5;

        [NinjaScriptProperty, Range(0, 80), Display(Name="Min pullback (ticks)", Order=8, GroupName="01 Detection")]
        public int MinPullbackTicks { get; set; } = 8;

        [NinjaScriptProperty, Range(3, 50), Display(Name="Max pullback (bars)", Order=9, GroupName="01 Detection")]
        public int MaxPullbackBars { get; set; } = 15;

        [NinjaScriptProperty, Range(1, 10), Display(Name="Entry expiry (bars)", Order=10, GroupName="01 Detection")]
        public int EntryExpiryBars { get; set; } = 2;
        #endregion

        #region Trendline / Range / SR inputs
        [NinjaScriptProperty, Range(0, 40), Display(Name="Trendline pierce (ticks)", Order=20, GroupName="02 TL/Range/SR")]
        public int TlPierceTicks { get; set; } = 6;

        [NinjaScriptProperty, Range(0, 40), Display(Name="Trendline break (ticks)", Order=21, GroupName="02 TL/Range/SR")]
        public int TlBreakTicks { get; set; } = 4;

        [NinjaScriptProperty, Range(0, 40), Display(Name="Trendline violation (ticks)", Order=22, GroupName="02 TL/Range/SR")]
        public int TlViolationTicks { get; set; } = 4;

        [NinjaScriptProperty, Range(5, 100), Display(Name="Trendline test window", Order=23, GroupName="02 TL/Range/SR")]
        public int TlTestWindow { get; set; } = 20;

        [NinjaScriptProperty, Range(0, 40), Display(Name="SR proximity (ticks)", Order=24, GroupName="02 TL/Range/SR")]
        public int SrProximityTicks { get; set; } = 4;

        [NinjaScriptProperty, Range(10, 200), Display(Name="Range lookback (bars)", Order=25, GroupName="02 TL/Range/SR")]
        public int RangeLookback { get; set; } = 30;

        [NinjaScriptProperty, Range(0, 40), Display(Name="Range band (ticks)", Order=26, GroupName="02 TL/Range/SR")]
        public int RangeBandTicks { get; set; } = 6;

        [NinjaScriptProperty, Range(0, 40), Display(Name="Range break (ticks)", Order=27, GroupName="02 TL/Range/SR")]
        public int RangeBreakTicks { get; set; } = 6;

        [NinjaScriptProperty, Range(1, 20), Display(Name="Range break bars", Order=28, GroupName="02 TL/Range/SR")]
        public int RangeBreakBars { get; set; } = 3;

        [NinjaScriptProperty, Range(0, 200), Display(Name="Failed BO max pierce (ticks)", Order=29, GroupName="02 TL/Range/SR")]
        public int FailBreakoutMaxTicks { get; set; } = 20;

        [NinjaScriptProperty, Range(1, 20), Display(Name="Failed BO window", Order=30, GroupName="02 TL/Range/SR")]
        public int FailBreakoutWindow { get; set; } = 5;

        [NinjaScriptProperty, Range(1, 10), Display(Name="F2E reversal bars", Order=31, GroupName="02 TL/Range/SR")]
        public int F2EReversalBars { get; set; } = 2;

        [NinjaScriptProperty, Range(1, 20), Display(Name="Trend bars above EMA", Order=32, GroupName="02 TL/Range/SR")]
        public int TrendBarsAboveEma { get; set; } = 14;
        #endregion

        #region Regime gates (spec §7.5, §13.5)
        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="Min pullback depth (frac of leg)", Order=33, GroupName="02b Regime gates")]
        public double MinPullbackDepthFrac { get; set; } = 0.30;

        [NinjaScriptProperty, Range(0, 40), Display(Name="New-extreme threshold (ticks)", Order=34, GroupName="02b Regime gates")]
        public int NewExtremeMinTicks { get; set; } = 4;

        [NinjaScriptProperty, Range(0, 50), Display(Name="New-extreme cooldown (bars)", Order=35, GroupName="02b Regime gates")]
        public int NewExtremeCooldownBars { get; set; } = 5;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="Mid-range low frac (block shorts below)", Order=36, GroupName="02b Regime gates")]
        public double MidRangeLowFrac { get; set; } = 0.40;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="Mid-range high frac (block longs above)", Order=37, GroupName="02b Regime gates")]
        public double MidRangeHighFrac { get; set; } = 0.60;

        [NinjaScriptProperty, Display(Name="Enable congestion filter", Order=38, GroupName="02b Regime gates")]
        public bool EnableCongestionFilter { get; set; } = true;

        [NinjaScriptProperty, Range(2, 50), Display(Name="Congestion lookback (bars)", Order=39, GroupName="02b Regime gates")]
        public int CongestionLookback { get; set; } = 10;

        [NinjaScriptProperty, Range(1, 80), Display(Name="Congestion max range (ticks)", Order=40, GroupName="02b Regime gates")]
        public int CongestionMaxRangeTicks { get; set; } = 16;

        [NinjaScriptProperty, Range(0, 80), Display(Name="Congestion max drift (ticks)", Order=41, GroupName="02b Regime gates")]
        public int CongestionMaxDriftTicks { get; set; } = 8;

        [NinjaScriptProperty, Display(Name="Enable PB counter-TL KEP", Order=42, GroupName="02b Regime gates")]
        public bool EnablePbTl { get; set; } = true;

        [NinjaScriptProperty, Display(Name="FB require pullback confirmation", Order=43, GroupName="02b Regime gates")]
        public bool FbRequirePullbackConfirmation { get; set; } = true;

        [NinjaScriptProperty, Range(1, 30), Display(Name="FB pullback window (bars)", Order=44, GroupName="02b Regime gates")]
        public int FbPullbackWindowBars { get; set; } = 8;

        [NinjaScriptProperty, Range(0, 40), Display(Name="FB LH/HL min ticks", Order=45, GroupName="02b Regime gates")]
        public int FbLhHlMinTicks { get; set; } = 4;
        #endregion

        #region Setup toggles
        [NinjaScriptProperty, Display(Name="Enable 2EL / 2ES", Order=40, GroupName="03 Setups")]
        public bool Enable2EL2ES { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Enable F2EL / F2ES", Order=41, GroupName="03 Setups")]
        public bool EnableF2ELF2ES { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Enable Failed Breakout", Order=42, GroupName="03 Setups")]
        public bool EnableFailedBreakout { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Enable HL/LH reversal", Order=43, GroupName="03 Setups")]
        public bool EnableHLLH { get; set; } = true;
        #endregion

        #region Stop / Target / Trail
        public enum StopMode { BEYOND_SIGNAL_BAR, FIXED_POINTS, FIXED_TICKS, ATR, BEYOND_SWING }
        public enum TargetMode { FIXED_POINTS, FIXED_TICKS, R_MULTIPLE, MEASURED_MOVE, ATR, OPPOSITE_KEP }
        public enum TrailMode { NONE, EMA, SWING, CHANDELIER }

        [NinjaScriptProperty, Display(Name="Stop mode", Order=50, GroupName="04 Exits")]
        public StopMode StopType { get; set; } = StopMode.BEYOND_SIGNAL_BAR;

        [NinjaScriptProperty, Range(0.25, 100.0), Display(Name="Stop points", Order=51, GroupName="04 Exits")]
        public double StopPoints { get; set; } = 4.0;

        [NinjaScriptProperty, Range(1, 200), Display(Name="Stop ticks", Order=52, GroupName="04 Exits")]
        public int StopTicks { get; set; } = 16;

        [NinjaScriptProperty, Range(0.1, 10.0), Display(Name="Stop ATR mult", Order=53, GroupName="04 Exits")]
        public double StopAtrMult { get; set; } = 1.5;

        [NinjaScriptProperty, Display(Name="Target mode", Order=54, GroupName="04 Exits")]
        public TargetMode TargetType { get; set; } = TargetMode.R_MULTIPLE;

        [NinjaScriptProperty, Range(0.25, 100.0), Display(Name="Target points", Order=55, GroupName="04 Exits")]
        public double TargetPoints { get; set; } = 2.0;

        [NinjaScriptProperty, Range(1, 200), Display(Name="Target ticks", Order=56, GroupName="04 Exits")]
        public int TargetTicks { get; set; } = 8;

        [NinjaScriptProperty, Range(0.25, 10.0), Display(Name="Target R-multiple", Order=57, GroupName="04 Exits")]
        public double TargetR { get; set; } = 2.0;

        [NinjaScriptProperty, Range(0.1, 10.0), Display(Name="Target ATR mult", Order=58, GroupName="04 Exits")]
        public double TargetAtrMult { get; set; } = 3.0;

        [NinjaScriptProperty, Display(Name="Trail mode", Order=59, GroupName="04 Exits")]
        public TrailMode TrailType { get; set; } = TrailMode.NONE;

        [NinjaScriptProperty, Range(0.5, 10.0), Display(Name="Trail ATR mult", Order=60, GroupName="04 Exits")]
        public double TrailAtrMult { get; set; } = 2.0;

        [NinjaScriptProperty, Range(0.25, 10.0), Display(Name="Min RR to enter", Order=61, GroupName="04 Exits")]
        public double MinRrToEnter { get; set; } = 1.0;
        #endregion

        #region Risk inputs
        [NinjaScriptProperty, Range(1, 50), Display(Name="Contracts", Order=70, GroupName="05 Risk")]
        public int Contracts { get; set; } = 1;

        [NinjaScriptProperty, Display(Name="Allow longs", Order=71, GroupName="05 Risk")]
        public bool AllowLongs { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Allow shorts", Order=72, GroupName="05 Risk")]
        public bool AllowShorts { get; set; } = true;

        [NinjaScriptProperty, Range(0.5, 20.0), Display(Name="Max daily loss (R)", Order=73, GroupName="05 Risk")]
        public double MaxDailyLossR { get; set; } = 3.0;

        [NinjaScriptProperty, Range(1, 50), Display(Name="Max daily trades", Order=74, GroupName="05 Risk")]
        public int MaxDailyTrades { get; set; } = 6;

        [NinjaScriptProperty, Range(0, 50), Display(Name="Cooldown bars after loss", Order=75, GroupName="05 Risk")]
        public int CooldownBarsAfterLoss { get; set; } = 5;

        [NinjaScriptProperty, Display(Name="RTH session only", Order=76, GroupName="05 Risk")]
        public bool RthOnly { get; set; } = true;
        #endregion

        #region Visual inputs
        [NinjaScriptProperty, Display(Name="Draw EMA", Order=80, GroupName="06 Visuals")]
        public bool DrawEma { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Draw trendlines", Order=81, GroupName="06 Visuals")]
        public bool DrawTrendlines { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Draw range box", Order=82, GroupName="06 Visuals")]
        public bool DrawRangeBox { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Draw signal markers", Order=83, GroupName="06 Visuals")]
        public bool DrawSignals { get; set; } = true;
        #endregion

        // ------------------------------------------------------------------
        // State machine
        // ------------------------------------------------------------------
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = @"Brooks-style price action strategy for /ES (2000 tick). Detects 2EL/2ES, F2EL/F2ES, Failed Breakout, HL/LH. Mirrors docs/strategy-spec.md.";
                Name = "ESPriceAction";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 1;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 60;
                IsInstantiatedOnEachOptimizationIteration = true;
            }
            else if (State == State.Configure)
            {
                // Nothing additional needed
            }
            else if (State == State.DataLoaded)
            {
                emaInd = EMA(EmaLength);
                atrInd = ATR(AtrLength);
                if (DrawEma) AddChartIndicator(emaInd);
                tick = TickSize;
            }
        }

        // ------------------------------------------------------------------
        // OnBarUpdate — main loop
        // ------------------------------------------------------------------
        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade) return;

            // Session-day rollover
            DateTime sessionDate = Time[0].Date;
            if (sessionDate != lastSessionDate)
            {
                dailyLossR = 0;
                dailyTradeCount = 0;
                lastSessionDate = sessionDate;
            }

            // 1. Detect swings, trendlines, ranges, S/R
            UpdateSwings();
            UpdateTrendlines();
            UpdateRange();

            // 2. Trend state + pullback state
            Trend trend = TrendState();
            UpdatePullbacks(trend);

            // 3. Session gate for new entries
            bool inSession = !RthOnly || IsRthBar();
            if (!inSession) return;

            // 4. Risk gates
            if (dailyLossR >= MaxDailyLossR) return;
            if (dailyTradeCount >= MaxDailyTrades) return;
            if (CurrentBar < cooldownUntilBar) return;

            // 5. Already in a position → no new entries
            if (Position.MarketPosition != MarketPosition.Flat) return;

            // Update new-extreme tracker
            UpdateNewExtreme();

            // Congestion blocks ALL new entries (spec §7.5)
            if (InCongestion()) return;

            // 6. Look for setups
            TryDetectSetups(trend);
        }

        // ------------------------------------------------------------------
        // Regime gates
        // ------------------------------------------------------------------
        private bool InCongestion()
        {
            if (!EnableCongestionFilter) return false;
            int lb = CongestionLookback;
            if (CurrentBar < lb) return false;
            double hi = double.MinValue, lo = double.MaxValue;
            for (int b = 0; b < lb; b++)
            {
                if (High[b] > hi) hi = High[b];
                if (Low[b]  < lo) lo = Low[b];
            }
            if ((hi - lo) > CongestionMaxRangeTicks * tick) return false;
            double drift = Math.Abs(Close[0] - Close[lb]);
            if (drift > CongestionMaxDriftTicks * tick) return false;
            // No confirmed swing pivot inside window
            int cutoff = CurrentBar - SwingStrength;
            foreach (var p in pivots)
            {
                if (p.Bar >= CurrentBar - lb + 1 && p.Bar <= cutoff) return false;
            }
            return true;
        }

        private void UpdateNewExtreme()
        {
            var sh = SwingHighsAt(CurrentBar);
            var sl = SwingLowsAt(CurrentBar);
            double thr = NewExtremeMinTicks * tick;
            if (sh.Count > 0)
            {
                var p = sh[sh.Count - 1];
                if (p.Price > lastConfirmedShPrice + thr)
                {
                    lastNewExtremeBarLong = p.Bar;
                    lastConfirmedShPrice = p.Price;
                }
            }
            if (sl.Count > 0)
            {
                var p = sl[sl.Count - 1];
                if (p.Price < lastConfirmedSlPrice - thr)
                {
                    lastNewExtremeBarShort = p.Bar;
                    lastConfirmedSlPrice = p.Price;
                }
            }
        }

        private bool NewExtremeBlock(bool isLong)
        {
            int lastBar = isLong ? lastNewExtremeBarLong : lastNewExtremeBarShort;
            return (CurrentBar - lastBar) < NewExtremeCooldownBars;
        }

        private bool MidRangeBlock(bool isLong)
        {
            if (activeRange == null) return false;
            double h = activeRange.Top - activeRange.Bottom;
            if (h <= 0) return false;
            double pos = (Close[0] - activeRange.Bottom) / h;
            if (isLong  && pos > MidRangeHighFrac) return true;
            if (!isLong && pos < MidRangeLowFrac)  return true;
            return false;
        }

        private bool PullbackDepthOk(bool isLong)
        {
            var sh = SwingHighsAt(CurrentBar);
            var sl = SwingLowsAt(CurrentBar);
            if (sh.Count == 0 || sl.Count == 0) return true;
            if (isLong)
            {
                var swingHigh = sh[sh.Count - 1];
                Pivot prior = null;
                for (int j = sl.Count - 1; j >= 0; j--)
                {
                    if (sl[j].Bar < swingHigh.Bar) { prior = sl[j]; break; }
                }
                if (prior == null) return true;
                double leg = swingHigh.Price - prior.Price;
                if (leg <= 0) return true;
                double depth = swingHigh.Price - bullPbExtremeLow;
                return (depth / leg) >= MinPullbackDepthFrac;
            }
            else
            {
                var swingLow = sl[sl.Count - 1];
                Pivot prior = null;
                for (int j = sh.Count - 1; j >= 0; j--)
                {
                    if (sh[j].Bar < swingLow.Bar) { prior = sh[j]; break; }
                }
                if (prior == null) return true;
                double leg = prior.Price - swingLow.Price;
                if (leg <= 0) return true;
                double depth = bearPbExtremeHigh - swingLow.Price;
                return (depth / leg) >= MinPullbackDepthFrac;
            }
        }

        private bool PbTlBroken(bool isLong)
        {
            if (!EnablePbTl) return false;
            double brkTol = TlBreakTicks * tick;
            if (isLong && bullPbActive && CurrentBar - bullPbStart >= 4)
            {
                int start = bullPbStart;
                // walk forward through pullback collecting LOWER highs
                var lhs = new List<(int Bar, double Price)>();
                for (int k = start; k < CurrentBar; k++)
                {
                    int barsAgo = CurrentBar - k;
                    if (barsAgo < 0 || barsAgo > CurrentBar) continue;
                    double hk = High[barsAgo];
                    if (lhs.Count == 0 || hk < lhs[lhs.Count - 1].Price) lhs.Add((k, hk));
                }
                if (lhs.Count >= 2)
                {
                    var lh2 = lhs[lhs.Count - 1]; var lh1 = lhs[lhs.Count - 2];
                    if (lh2.Price < lh1.Price && lh2.Bar > lh1.Bar)
                    {
                        double slope = (lh2.Price - lh1.Price) / Math.Max(1, lh2.Bar - lh1.Bar);
                        double tlNow = lh2.Price + slope * (CurrentBar - lh2.Bar);
                        return Close[0] > tlNow + brkTol;
                    }
                }
            }
            if (!isLong && bearPbActive && CurrentBar - bearPbStart >= 4)
            {
                int start = bearPbStart;
                var hls = new List<(int Bar, double Price)>();
                for (int k = start; k < CurrentBar; k++)
                {
                    int barsAgo = CurrentBar - k;
                    if (barsAgo < 0 || barsAgo > CurrentBar) continue;
                    double lk = Low[barsAgo];
                    if (hls.Count == 0 || lk > hls[hls.Count - 1].Price) hls.Add((k, lk));
                }
                if (hls.Count >= 2)
                {
                    var hl2 = hls[hls.Count - 1]; var hl1 = hls[hls.Count - 2];
                    if (hl2.Price > hl1.Price && hl2.Bar > hl1.Bar)
                    {
                        double slope = (hl2.Price - hl1.Price) / Math.Max(1, hl2.Bar - hl1.Bar);
                        double tlNow = hl2.Price + slope * (CurrentBar - hl2.Bar);
                        return Close[0] < tlNow - brkTol;
                    }
                }
            }
            return false;
        }

        // ------------------------------------------------------------------
        // Helpers — bar features
        // ------------------------------------------------------------------
        private bool IsRthBar()
        {
            // 9:30 - 16:00 ET, Mon-Fri
            var t = Time[0];
            if (t.DayOfWeek == DayOfWeek.Saturday || t.DayOfWeek == DayOfWeek.Sunday) return false;
            int minutes = t.Hour * 60 + t.Minute;
            return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
        }

        private bool IsBull(int barsAgo) => Close[barsAgo] > Open[barsAgo];
        private bool IsBear(int barsAgo) => Close[barsAgo] < Open[barsAgo];
        private double Body(int barsAgo) => Math.Abs(Close[barsAgo] - Open[barsAgo]);
        private double Rng(int barsAgo) => High[barsAgo] - Low[barsAgo];

        private bool ValidSignalBar(int barsAgo, bool isLong)
        {
            double r = Rng(barsAgo);
            if (r <= 0) return false;
            double a = atrInd[barsAgo];
            if (!double.IsNaN(a) && a > 0 && r > MaxSignalRangeAtr * a) return false;
            if (Body(barsAgo) < MinBodyFraction * r) return false;
            if (isLong)
            {
                if (!IsBull(barsAgo)) return false;
                double cs = (Close[barsAgo] - Low[barsAgo]) / r;
                return cs >= MinCloseStrength;
            }
            else
            {
                if (!IsBear(barsAgo)) return false;
                double cs = (High[barsAgo] - Close[barsAgo]) / r;
                return cs >= MinCloseStrength;
            }
        }

        // ------------------------------------------------------------------
        // Swings
        // ------------------------------------------------------------------
        private void UpdateSwings()
        {
            int k = SwingStrength;
            if (CurrentBar < 2 * k) return;
            int candidate = CurrentBar - k;       // bar index being confirmed now
            int barsAgo = k;                       // CurrentBars[0] - candidate
            double h = High[barsAgo];
            double l = Low[barsAgo];
            bool isSH = true, isSL = true;
            for (int j = 1; j <= k; j++)
            {
                if (!(h > High[barsAgo + j] && h > High[barsAgo - j])) isSH = false;
                if (!(l < Low[barsAgo + j] && l < Low[barsAgo - j])) isSL = false;
            }
            if (isSH) pivots.Add(new Pivot { Bar = candidate, Price = h, IsHigh = true });
            if (isSL) pivots.Add(new Pivot { Bar = candidate, Price = l, IsHigh = false });

            // Cap memory
            if (pivots.Count > 500) pivots.RemoveRange(0, pivots.Count - 500);
        }

        private List<Pivot> SwingHighsAt(int barIdx)
        {
            int cutoff = barIdx - SwingStrength;
            return pivots.Where(p => p.IsHigh && p.Bar <= cutoff).ToList();
        }

        private List<Pivot> SwingLowsAt(int barIdx)
        {
            int cutoff = barIdx - SwingStrength;
            return pivots.Where(p => !p.IsHigh && p.Bar <= cutoff).ToList();
        }

        // ------------------------------------------------------------------
        // Trendlines
        // ------------------------------------------------------------------
        private double CloseAtBarIndex(int barIdx)
        {
            int barsAgo = CurrentBar - barIdx;
            if (barsAgo < 0 || barsAgo > CurrentBar) return Close[0];
            return Close[barsAgo];
        }

        private double HighAtBarIndex(int barIdx) => High[Math.Max(0, CurrentBar - barIdx)];
        private double LowAtBarIndex(int barIdx) => Low[Math.Max(0, CurrentBar - barIdx)];

        private bool TrendlineValid(Trendline tl, int upToBar, double tolerance)
        {
            for (int b = tl.X0; b <= upToBar; b++)
            {
                if (tl.IsBull)
                {
                    if (CloseAtBarIndex(b) < tl.ValueAt(b) - tolerance) return false;
                }
                else
                {
                    if (CloseAtBarIndex(b) > tl.ValueAt(b) + tolerance) return false;
                }
            }
            return true;
        }

        private void UpdateTrendlines()
        {
            int idx = CurrentBar;
            double violTol = TlViolationTicks * tick;

            var sh = SwingHighsAt(idx);
            var sl = SwingLowsAt(idx);

            Trendline newBull = null;
            if (sl.Count >= 2)
            {
                for (int j = sl.Count - 1; j > 0; j--)
                {
                    var p2 = sl[j]; var p1 = sl[j - 1];
                    if (p2.Price <= p1.Price) continue;
                    var tl = new Trendline { X0 = p1.Bar, Y0 = p1.Price, X1 = p2.Bar, Y1 = p2.Price, IsBull = true };
                    if (TrendlineValid(tl, idx, violTol)) { newBull = tl; break; }
                }
            }

            Trendline newBear = null;
            if (sh.Count >= 2)
            {
                for (int j = sh.Count - 1; j > 0; j--)
                {
                    var p2 = sh[j]; var p1 = sh[j - 1];
                    if (p2.Price >= p1.Price) continue;
                    var tl = new Trendline { X0 = p1.Bar, Y0 = p1.Price, X1 = p2.Bar, Y1 = p2.Price, IsBull = false };
                    if (TrendlineValid(tl, idx, violTol)) { newBear = tl; break; }
                }
            }

            double brkTol = TlBreakTicks * tick;
            double c = Close[0];
            if (activeBullTL != null)
            {
                if (c < activeBullTL.ValueAt(idx) - brkTol)
                {
                    activeBullTL.BrokenAt = idx;
                    brokenBullTLAt = idx;
                    activeBullTL = null;
                    RemoveDrawObject("BullTL");
                }
            }
            if (activeBearTL != null)
            {
                if (c > activeBearTL.ValueAt(idx) + brkTol)
                {
                    activeBearTL.BrokenAt = idx;
                    brokenBearTLAt = idx;
                    activeBearTL = null;
                    RemoveDrawObject("BearTL");
                }
            }

            if (activeBullTL == null && newBull != null)
            {
                activeBullTL = newBull;
                if (DrawTrendlines)
                    Draw.Line(this, "BullTL", false,
                        CurrentBar - newBull.X0, newBull.Y0,
                        CurrentBar - newBull.X1, newBull.Y1, Brushes.LimeGreen, DashStyleHelper.Solid, 2);
            }
            if (activeBearTL == null && newBear != null)
            {
                activeBearTL = newBear;
                if (DrawTrendlines)
                    Draw.Line(this, "BearTL", false,
                        CurrentBar - newBear.X0, newBear.Y0,
                        CurrentBar - newBear.X1, newBear.Y1, Brushes.OrangeRed, DashStyleHelper.Solid, 2);
            }
        }

        private bool TrendlineInPlay(bool bull, int idx)
        {
            if (bull)
            {
                if (activeBullTL != null) return true;
                if (brokenBullTLAt.HasValue && idx - brokenBullTLAt.Value < TlTestWindow) return false;
                return false;
            }
            else
            {
                if (activeBearTL != null) return true;
                if (brokenBearTLAt.HasValue && idx - brokenBearTLAt.Value < TlTestWindow) return false;
                return false;
            }
        }

        // ------------------------------------------------------------------
        // Trend
        // ------------------------------------------------------------------
        private Trend TrendState()
        {
            if (CurrentBar < 20) return Trend.Undefined;
            int above = 0, below = 0;
            for (int b = 0; b < 20; b++)
            {
                if (Close[b] > emaInd[b]) above++;
                else if (Close[b] < emaInd[b]) below++;
            }
            int idx = CurrentBar;
            var sh = SwingHighsAt(idx);
            var sl = SwingLowsAt(idx);
            bool hhHl = false, llLh = false;
            if (sh.Count >= 2 && sl.Count >= 2)
            {
                hhHl = sh[sh.Count - 1].Price > sh[sh.Count - 2].Price && sl[sl.Count - 1].Price > sl[sl.Count - 2].Price;
                llLh = sh[sh.Count - 1].Price < sh[sh.Count - 2].Price && sl[sl.Count - 1].Price < sl[sl.Count - 2].Price;
            }
            if (above >= TrendBarsAboveEma && hhHl && activeBullTL != null) return Trend.Bull;
            if (below >= TrendBarsAboveEma && llLh && activeBearTL != null) return Trend.Bear;
            return Trend.TradingRange;
        }

        // ------------------------------------------------------------------
        // Trading range
        // ------------------------------------------------------------------
        private void UpdateRange()
        {
            if (CurrentBar < RangeLookback) return;
            if (activeRange != null)
            {
                double brkTol = RangeBreakTicks * tick;
                double c = Close[0];
                if (c > activeRange.Top + brkTol || c < activeRange.Bottom - brkTol)
                {
                    bool allAbove = true, allBelow = true;
                    for (int b = 0; b < RangeBreakBars && b <= CurrentBar; b++)
                    {
                        if (Close[b] <= activeRange.Top + brkTol) allAbove = false;
                        if (Close[b] >= activeRange.Bottom - brkTol) allBelow = false;
                    }
                    if (allAbove || allBelow)
                    {
                        activeRange.BrokenAt = CurrentBar;
                        activeRange = null;
                        RemoveDrawObject("RangeBox");
                    }
                }
                return;
            }

            double hi = double.MinValue, lo = double.MaxValue;
            for (int b = 0; b < RangeLookback && b <= CurrentBar; b++)
            {
                if (High[b] > hi) hi = High[b];
                if (Low[b] < lo) lo = Low[b];
            }
            double band = RangeBandTicks * tick;
            int idx = CurrentBar;
            var sh = SwingHighsAt(idx);
            var sl = SwingLowsAt(idx);
            int startBar = idx - RangeLookback + 1;
            int shIn = sh.Count(p => p.Bar >= startBar && Math.Abs(p.Price - hi) <= band);
            int slIn = sl.Count(p => p.Bar >= startBar && Math.Abs(p.Price - lo) <= band);
            if (shIn >= 2 && slIn >= 2)
            {
                activeRange = new Range { Start = startBar, Top = hi, Bottom = lo };
                if (!srLevels.Contains(hi)) srLevels.Add(hi);
                if (!srLevels.Contains(lo)) srLevels.Add(lo);
                if (DrawRangeBox)
                    Draw.Rectangle(this, "RangeBox", false,
                        CurrentBar - startBar, hi, 0, lo,
                        Brushes.SteelBlue, Brushes.SteelBlue, 15);
            }
        }

        // ------------------------------------------------------------------
        // KEPs
        // ------------------------------------------------------------------
        private string AtKep(int barsAgo, bool isLong)
        {
            double ema = emaInd[barsAgo];
            if (double.IsNaN(ema)) return null;
            var keps = new List<string>();
            double proxE = EmaProximityTicks * tick;
            if (isLong) { if (Low[barsAgo] <= ema + proxE) keps.Add("EMA"); }
            else        { if (High[barsAgo] >= ema - proxE) keps.Add("EMA"); }

            int absBar = CurrentBar - barsAgo;
            double proxT = TlPierceTicks * tick;
            if (isLong && activeBullTL != null)
            {
                double tlv = activeBullTL.ValueAt(absBar);
                if (Low[barsAgo] <= tlv + proxT && Close[barsAgo] >= tlv - proxT) keps.Add("TRENDLINE");
            }
            if (!isLong && activeBearTL != null)
            {
                double tlv = activeBearTL.ValueAt(absBar);
                if (High[barsAgo] >= tlv - proxT && Close[barsAgo] <= tlv + proxT) keps.Add("TRENDLINE");
            }

            double proxS = SrProximityTicks * tick;
            foreach (var lv in srLevels)
            {
                if (isLong && Math.Abs(Low[barsAgo] - lv) <= proxS) { keps.Add("SR"); break; }
                if (!isLong && Math.Abs(High[barsAgo] - lv) <= proxS) { keps.Add("SR"); break; }
            }
            // PB_TL — short counter-trend trendline break (§6.4)
            if (barsAgo == 0 && PbTlBroken(isLong)) keps.Add("PB_TL");
            if (keps.Count == 0) return null;
            return keps.Count >= 2 ? "CONFLUENCE" : keps[0];
        }

        // ------------------------------------------------------------------
        // Pullback tracking
        // ------------------------------------------------------------------
        private void UpdatePullbacks(Trend trend)
        {
            if (trend != Trend.Bull) bullPbActive = false;
            if (trend != Trend.Bear) bearPbActive = false;
            if (CurrentBar < 1) return;

            int idx = CurrentBar;

            if (trend == Trend.Bull)
            {
                if (!bullPbActive)
                {
                    if (High[0] <= High[1] && Close[0] < High[1])
                    {
                        bullPbActive = true; bullPbStart = idx;
                        bullPbExtremeLow = Low[0];
                        bullPbH1Seen = false; bullPbH2Seen = false;
                        bullPbLastHigh = High[0];
                    }
                }
                else
                {
                    if (Low[0] < bullPbExtremeLow)
                    {
                        bullPbExtremeLow = Low[0];
                        bullPbH1Seen = false;
                    }
                    if (!bullPbH1Seen)
                    {
                        if (High[0] > High[1] && idx - bullPbStart >= 1)
                        {
                            bullPbH1Seen = true; bullPbLastHigh = High[0];
                        }
                    }
                    else
                    {
                        if (High[0] > High[1] && High[1] <= bullPbLastHigh && High[1] < bullPbLastHigh)
                            bullPbH2Seen = true;
                    }
                    if (idx - bullPbStart > MaxPullbackBars) bullPbActive = false;
                }
            }

            if (trend == Trend.Bear)
            {
                if (!bearPbActive)
                {
                    if (Low[0] >= Low[1] && Close[0] > Low[1])
                    {
                        bearPbActive = true; bearPbStart = idx;
                        bearPbExtremeHigh = High[0];
                        bearPbL1Seen = false; bearPbL2Seen = false;
                        bearPbLastLow = Low[0];
                    }
                }
                else
                {
                    if (High[0] > bearPbExtremeHigh)
                    {
                        bearPbExtremeHigh = High[0];
                        bearPbL1Seen = false;
                    }
                    if (!bearPbL1Seen)
                    {
                        if (Low[0] < Low[1] && idx - bearPbStart >= 1)
                        {
                            bearPbL1Seen = true; bearPbLastLow = Low[0];
                        }
                    }
                    else
                    {
                        if (Low[0] < Low[1] && Low[1] >= bearPbLastLow && Low[1] > bearPbLastLow)
                            bearPbL2Seen = true;
                    }
                    if (idx - bearPbStart > MaxPullbackBars) bearPbActive = false;
                }
            }
        }

        // ------------------------------------------------------------------
        // Setup detection + order placement
        // ------------------------------------------------------------------
        private void TryDetectSetups(Trend trend)
        {
            // 2EL
            if (Enable2EL2ES && AllowLongs && trend == Trend.Bull
                && bullPbActive && bullPbH2Seen
                && (High[0] - bullPbExtremeLow) >= MinPullbackTicks * tick
                && PullbackDepthOk(true)
                && !NewExtremeBlock(true)
                && !MidRangeBlock(true))
            {
                string kep = AtKep(0, true);
                if (kep != null && ValidSignalBar(0, true))
                {
                    PlaceLong("2EL", kep);
                    bullPbActive = false;
                    return;
                }
            }
            // 2ES
            if (Enable2EL2ES && AllowShorts && trend == Trend.Bear
                && bearPbActive && bearPbL2Seen
                && (bearPbExtremeHigh - Low[0]) >= MinPullbackTicks * tick
                && PullbackDepthOk(false)
                && !NewExtremeBlock(false)
                && !MidRangeBlock(false))
            {
                string kep = AtKep(0, false);
                if (kep != null && ValidSignalBar(0, false))
                {
                    PlaceShort("2ES", kep);
                    bearPbActive = false;
                    return;
                }
            }

            // F2EL / F2ES
            if (EnableF2ELF2ES && CurrentBar > F2EReversalBars + 1)
            {
                if (AllowLongs && trend == Trend.Bull && TrendlineInPlay(true, CurrentBar)
                    && !NewExtremeBlock(true) && !MidRangeBlock(true))
                {
                    for (int k = 1; k <= F2EReversalBars + 1; k++)
                    {
                        if (ValidSignalBar(k, false))
                        {
                            bool brokeLow = false;
                            double trigger = Low[k] - tick;
                            for (int m = k - 1; m >= 0; m--)
                                if (Low[m] < trigger) { brokeLow = true; break; }
                            if (!brokeLow)
                            {
                                string kep = AtKep(0, true);
                                if (kep != null && ValidSignalBar(0, true))
                                {
                                    PlaceLong("F2EL", kep);
                                    return;
                                }
                            }
                        }
                    }
                }
                if (AllowShorts && trend == Trend.Bear && TrendlineInPlay(false, CurrentBar)
                    && !NewExtremeBlock(false) && !MidRangeBlock(false))
                {
                    for (int k = 1; k <= F2EReversalBars + 1; k++)
                    {
                        if (ValidSignalBar(k, true))
                        {
                            bool brokeHigh = false;
                            double trigger = High[k] + tick;
                            for (int m = k - 1; m >= 0; m--)
                                if (High[m] > trigger) { brokeHigh = true; break; }
                            if (!brokeHigh)
                            {
                                string kep = AtKep(0, false);
                                if (kep != null && ValidSignalBar(0, false))
                                {
                                    PlaceShort("F2ES", kep);
                                    return;
                                }
                            }
                        }
                    }
                }
            }

            // Failed Breakout
            if (EnableFailedBreakout && activeRange != null)
            {
                double maxPierce = FailBreakoutMaxTicks * tick;
                double loEx = Low[0], hiEx = High[0];
                for (int b = 1; b < FailBreakoutWindow && b <= CurrentBar; b++)
                {
                    if (Low[b] < loEx) loEx = Low[b];
                    if (High[b] > hiEx) hiEx = High[b];
                }

                if (FbRequirePullbackConfirmation)
                {
                    // Arm long fade
                    if (AllowLongs && loEx < activeRange.Bottom && (activeRange.Bottom - loEx) <= maxPierce
                        && Close[0] > activeRange.Bottom)
                    {
                        fbArmedLongBar = CurrentBar; fbArmedLongExcLow = loEx;
                    }
                    if (AllowShorts && hiEx > activeRange.Top && (hiEx - activeRange.Top) <= maxPierce
                        && Close[0] < activeRange.Top)
                    {
                        fbArmedShortBar = CurrentBar; fbArmedShortExcHigh = hiEx;
                    }

                    // Confirm
                    double minOff = FbLhHlMinTicks * tick;
                    if (fbArmedLongBar >= 0)
                    {
                        if (CurrentBar - fbArmedLongBar > FbPullbackWindowBars) fbArmedLongBar = -1;
                        else if (CurrentBar > fbArmedLongBar
                                 && Low[0] > fbArmedLongExcLow + minOff
                                 && Low[0] > Low[1]
                                 && ValidSignalBar(0, true))
                        {
                            PlaceLong("FB_PB_LONG", "RANGE_EDGE");
                            fbArmedLongBar = -1;
                            return;
                        }
                    }
                    if (fbArmedShortBar >= 0)
                    {
                        if (CurrentBar - fbArmedShortBar > FbPullbackWindowBars) fbArmedShortBar = -1;
                        else if (CurrentBar > fbArmedShortBar
                                 && High[0] < fbArmedShortExcHigh - minOff
                                 && High[0] < High[1]
                                 && ValidSignalBar(0, false))
                        {
                            PlaceShort("FB_PB_SHORT", "RANGE_EDGE");
                            fbArmedShortBar = -1;
                            return;
                        }
                    }
                }
                else
                {
                    if (AllowLongs && loEx < activeRange.Bottom && (activeRange.Bottom - loEx) <= maxPierce
                        && Close[0] > activeRange.Bottom && ValidSignalBar(0, true))
                    {
                        PlaceLong("FB_LONG", "RANGE_EDGE");
                        return;
                    }
                    if (AllowShorts && hiEx > activeRange.Top && (hiEx - activeRange.Top) <= maxPierce
                        && Close[0] < activeRange.Top && ValidSignalBar(0, false))
                    {
                        PlaceShort("FB_SHORT", "RANGE_EDGE");
                        return;
                    }
                }
            }

            // HL / LH reversal
            if (EnableHLLH)
            {
                var sh = SwingHighsAt(CurrentBar);
                var sl = SwingLowsAt(CurrentBar);
                if (AllowLongs && brokenBearTLAt.HasValue
                    && CurrentBar - brokenBearTLAt.Value < TlTestWindow
                    && sl.Count >= 2 && sl[sl.Count - 1].Bar > brokenBearTLAt.Value
                    && sl[sl.Count - 1].Price > sl[sl.Count - 2].Price
                    && ValidSignalBar(0, true))
                {
                    PlaceLong("HLLH_LONG", "SR");
                    return;
                }
                if (AllowShorts && brokenBullTLAt.HasValue
                    && CurrentBar - brokenBullTLAt.Value < TlTestWindow
                    && sh.Count >= 2 && sh[sh.Count - 1].Bar > brokenBullTLAt.Value
                    && sh[sh.Count - 1].Price < sh[sh.Count - 2].Price
                    && ValidSignalBar(0, false))
                {
                    PlaceShort("HLLH_SHORT", "SR");
                    return;
                }
            }
        }

        // ------------------------------------------------------------------
        // Stops / Targets / Orders
        // ------------------------------------------------------------------
        private double ComputeStop(bool isLong, double entry, double signalHigh, double signalLow)
        {
            double atrv = atrInd[0];
            double swingPrice = isLong
                ? (SwingLowsAt(CurrentBar).LastOrDefault()?.Price ?? signalLow)
                : (SwingHighsAt(CurrentBar).LastOrDefault()?.Price ?? signalHigh);

            switch (StopType)
            {
                case StopMode.BEYOND_SIGNAL_BAR:
                    return isLong ? signalLow - tick : signalHigh + tick;
                case StopMode.FIXED_POINTS:
                    return isLong ? entry - StopPoints : entry + StopPoints;
                case StopMode.FIXED_TICKS:
                    return isLong ? entry - StopTicks * tick : entry + StopTicks * tick;
                case StopMode.ATR:
                    return isLong ? entry - StopAtrMult * atrv : entry + StopAtrMult * atrv;
                case StopMode.BEYOND_SWING:
                    return isLong ? swingPrice - tick : swingPrice + tick;
            }
            return isLong ? signalLow - tick : signalHigh + tick;
        }

        private double ComputeTarget(bool isLong, double entry, double stop)
        {
            double risk = Math.Abs(entry - stop);
            double atrv = atrInd[0];
            switch (TargetType)
            {
                case TargetMode.FIXED_POINTS:
                    return isLong ? entry + TargetPoints : entry - TargetPoints;
                case TargetMode.FIXED_TICKS:
                    return isLong ? entry + TargetTicks * tick : entry - TargetTicks * tick;
                case TargetMode.R_MULTIPLE:
                    return isLong ? entry + TargetR * risk : entry - TargetR * risk;
                case TargetMode.ATR:
                    return isLong ? entry + TargetAtrMult * atrv : entry - TargetAtrMult * atrv;
                case TargetMode.MEASURED_MOVE:
                {
                    var sh = SwingHighsAt(CurrentBar);
                    var sl = SwingLowsAt(CurrentBar);
                    if (sh.Count > 0 && sl.Count > 0)
                    {
                        double leg = sh[sh.Count - 1].Price - sl[sl.Count - 1].Price;
                        return isLong ? entry + Math.Abs(leg) : entry - Math.Abs(leg);
                    }
                    return isLong ? entry + 2 * risk : entry - 2 * risk;
                }
                case TargetMode.OPPOSITE_KEP:
                    if (activeRange != null)
                        return isLong ? activeRange.Top : activeRange.Bottom;
                    return isLong ? entry + 2 * risk : entry - 2 * risk;
            }
            return isLong ? entry + 2 * risk : entry - 2 * risk;
        }

        private void PlaceLong(string setup, string kep)
        {
            double entry = High[0] + tick;
            double stop = ComputeStop(true, entry, High[0], Low[0]);
            double target = ComputeTarget(true, entry, stop);
            double risk = entry - stop;
            if (risk <= 0) return;
            if ((target - entry) / risk < MinRrToEnter) return;

            string tag = setup + "_" + CurrentBar;
            SetStopLoss(tag, CalculationMode.Price, stop, false);
            SetProfitTarget(tag, CalculationMode.Price, target);
            EnterLongStopMarket(0, true, Contracts, entry, tag);
            dailyTradeCount++;

            if (DrawSignals)
                Draw.TriangleUp(this, "L_" + CurrentBar, true, 0, Low[0] - 2 * tick,
                    BrushForSetup(setup, true));
            Print(string.Format("[{0}] {1} LONG  setup={2}  kep={3}  entry={4:F2}  stop={5:F2}  tgt={6:F2}  risk={7:F2}",
                Time[0], setup, setup, kep, entry, stop, target, risk));
        }

        private void PlaceShort(string setup, string kep)
        {
            double entry = Low[0] - tick;
            double stop = ComputeStop(false, entry, High[0], Low[0]);
            double target = ComputeTarget(false, entry, stop);
            double risk = stop - entry;
            if (risk <= 0) return;
            if ((entry - target) / risk < MinRrToEnter) return;

            string tag = setup + "_" + CurrentBar;
            SetStopLoss(tag, CalculationMode.Price, stop, false);
            SetProfitTarget(tag, CalculationMode.Price, target);
            EnterShortStopMarket(0, true, Contracts, entry, tag);
            dailyTradeCount++;

            if (DrawSignals)
                Draw.TriangleDown(this, "S_" + CurrentBar, true, 0, High[0] + 2 * tick,
                    BrushForSetup(setup, false));
            Print(string.Format("[{0}] {1} SHORT setup={2}  kep={3}  entry={4:F2}  stop={5:F2}  tgt={6:F2}  risk={7:F2}",
                Time[0], setup, setup, kep, entry, stop, target, risk));
        }

        private Brush BrushForSetup(string setup, bool isLong)
        {
            switch (setup)
            {
                case "2EL":        return Brushes.Lime;
                case "2ES":        return Brushes.Red;
                case "F2EL":       return Brushes.Yellow;
                case "F2ES":       return Brushes.Orange;
                case "FB_LONG":    return Brushes.Cyan;
                case "FB_SHORT":   return Brushes.Magenta;
                case "FB_PB_LONG": return Brushes.Cyan;
                case "FB_PB_SHORT":return Brushes.Magenta;
                case "HLLH_LONG":  return Brushes.DeepSkyBlue;
                case "HLLH_SHORT": return Brushes.DeepPink;
            }
            return isLong ? Brushes.Lime : Brushes.Red;
        }

        // ------------------------------------------------------------------
        // Trade lifecycle hooks for daily-loss tracking
        // ------------------------------------------------------------------
        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            // No-op — covered by OnPositionUpdate
        }

        protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
        {
            if (marketPosition != MarketPosition.Flat) return;
            if (SystemPerformance == null || SystemPerformance.AllTrades.Count == 0) return;
            var trade = SystemPerformance.AllTrades[SystemPerformance.AllTrades.Count - 1];
            double r = 0;
            // Approximate R: realized $ / (initial risk in $) — but easier in points using StopType etc.
            // Track losses only here:
            if (trade.ProfitCurrency < 0)
            {
                double riskApprox = Math.Max(0.01, StopType == StopMode.FIXED_POINTS ? StopPoints : 4.0);
                dailyLossR += Math.Abs(trade.ProfitCurrency) / (riskApprox * Instrument.MasterInstrument.PointValue * Contracts);
                cooldownUntilBar = CurrentBar + CooldownBarsAfterLoss;
            }
        }
    }
}

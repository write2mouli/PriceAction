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

// Focused 2-legged-pullback strategy for /ES 2000-tick.
// Mirrors pinescript/es_pa_strategy.pine (v1).
//
// State machine: IDLE -> LEG1 -> BETWEEN -> LEG2 -> trigger on high > prior high (bull),
//                                                   low < prior low  (bear).
// Quality score (Brooks-textbook scoring; flipped from the original v3):
//   Leg 1 momentum (0-3): fraction of leg-1 bars with low < prior low
//   Leg 2 momentum (0-3): same
//   Leg 2 depth   (0,1,3): 3 if leg2_low > leg1_low (HL/double-bottom = BEST)
//                          1 if within 2 ticks of leg1_low
//                          0 if leg2_low < leg1_low (continuation, weakest)
//   Both legs >=2 bars (0-1)
// Grade: A 8-10, B 6-7, C 4-5, D 0-3.
//
// Defaults reflect the best cell from the 60-day Python backtest:
//   LONG only, min_quality = 6 (A+B), structural stop, swing-high target, RTH only.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class ESPullback2L : Strategy
    {
        public enum StopType  { Structural, FixedTicks }
        public enum TargetType { Swing, Fixed1Pt, Fixed2Pt }

        // -------------------------------------------------------------------
        // Inputs - Detection
        // -------------------------------------------------------------------
        [NinjaScriptProperty, Range(5, 200),  Display(Name="EMA length",            Order=1, GroupName="01 Detection")]
        public int EmaLength { get; set; } = 21;

        [NinjaScriptProperty, Range(1, 30),   Display(Name="EMA slope lookback",    Order=2, GroupName="01 Detection")]
        public int EmaSlopeLookback { get; set; } = 5;

        [NinjaScriptProperty, Range(0, 80),   Display(Name="EMA proximity (ticks)", Order=3, GroupName="01 Detection")]
        public int EmaProxTicks { get; set; } = 8;

        [NinjaScriptProperty, Range(0, 80),   Display(Name="Min pullback (ticks)",  Order=4, GroupName="01 Detection")]
        public int MinPullbackTicks { get; set; } = 8;

        [NinjaScriptProperty, Range(2, 50),   Display(Name="Max bars per leg",      Order=5, GroupName="01 Detection")]
        public int MaxLegBars { get; set; } = 15;

        [NinjaScriptProperty,                 Display(Name="Require signal bar",    Order=6, GroupName="01 Detection")]
        public bool RequireSignalBar { get; set; } = false;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="  Min close strength", Order=7, GroupName="01 Detection")]
        public double MinCloseStrength { get; set; } = 0.55;

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name="  Min body fraction",  Order=8, GroupName="01 Detection")]
        public double MinBodyFraction { get; set; } = 0.40;

        // -------------------------------------------------------------------
        // Inputs - Quality / sides
        // -------------------------------------------------------------------
        [NinjaScriptProperty, Range(0, 10), Display(Name="Min quality score (0-10)", Order=10, GroupName="02 Quality / sides")]
        public int MinQualityScore { get; set; } = 6;

        [NinjaScriptProperty, Display(Name="Allow longs",  Order=11, GroupName="02 Quality / sides")]
        public bool AllowLongs { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Allow shorts", Order=12, GroupName="02 Quality / sides")]
        public bool AllowShorts { get; set; } = false;

        // -------------------------------------------------------------------
        // Inputs - Exits
        // -------------------------------------------------------------------
        [NinjaScriptProperty, Display(Name="Stop mode",   Order=20, GroupName="03 Exits")]
        public StopType StopMode { get; set; } = StopType.Structural;

        [NinjaScriptProperty, Range(1, 80), Display(Name="Stop ticks (if fixed)", Order=21, GroupName="03 Exits")]
        public int StopTicks { get; set; } = 8;

        [NinjaScriptProperty, Display(Name="Target mode", Order=22, GroupName="03 Exits")]
        public TargetType TargetMode { get; set; } = TargetType.Swing;

        [NinjaScriptProperty, Range(1, 20), Display(Name="Entry expiry (bars)", Order=23, GroupName="03 Exits")]
        public int EntryExpiryBars { get; set; } = 2;

        [NinjaScriptProperty, Range(0.1, 10.0), Display(Name="Min RR to enter", Order=24, GroupName="03 Exits")]
        public double MinRrToEnter { get; set; } = 1.0;

        // -------------------------------------------------------------------
        // Inputs - Risk / session
        // -------------------------------------------------------------------
        [NinjaScriptProperty, Range(1, 50), Display(Name="Contracts", Order=30, GroupName="04 Risk / session")]
        public int Contracts { get; set; } = 1;

        [NinjaScriptProperty, Display(Name="RTH only (09:30-16:00 ET)", Order=31, GroupName="04 Risk / session")]
        public bool RthOnly { get; set; } = true;

        [NinjaScriptProperty, Range(1, 50), Display(Name="Max trades per day", Order=32, GroupName="04 Risk / session")]
        public int MaxDailyTrades { get; set; } = 6;

        [NinjaScriptProperty, Range(50, 100000), Display(Name="Max daily loss ($)", Order=33, GroupName="04 Risk / session")]
        public double MaxDailyLoss { get; set; } = 750;

        // -------------------------------------------------------------------
        // Inputs - Visuals
        // -------------------------------------------------------------------
        [NinjaScriptProperty, Display(Name="Draw EMA",          Order=40, GroupName="05 Visuals")]
        public bool DrawEma { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Draw leg markers (debug)", Order=41, GroupName="05 Visuals")]
        public bool DrawLegMarkers { get; set; } = false;

        [NinjaScriptProperty, Display(Name="Draw entry/stop/target lines", Order=42, GroupName="05 Visuals")]
        public bool DrawEntryLines { get; set; } = true;

        [NinjaScriptProperty, Range(5, 200), Display(Name="Lines extend N bars", Order=43, GroupName="05 Visuals")]
        public int LineExtendBars { get; set; } = 40;

        [NinjaScriptProperty, Display(Name="Show status label on chart", Order=44, GroupName="05 Visuals")]
        public bool ShowStatusLabel { get; set; } = true;

        [NinjaScriptProperty, Display(Name="Verbose debug print", Order=45, GroupName="05 Visuals")]
        public bool VerbosePrint { get; set; } = false;

        [NinjaScriptProperty, Display(Name="Diagnostic background tint", Order=46, GroupName="05 Visuals")]
        public bool DiagnosticTint { get; set; } = false;

        // -------------------------------------------------------------------
        // Internal state
        // -------------------------------------------------------------------
        private EMA emaInd;
        private double tick = 0.25;

        // BULL state machine
        private int    bullState;
        private int    bullLegStart;
        private double bullSwingHigh;
        private bool   bullEmaTouched;
        private int    bullL1Bars, bullL1Ll;
        private double bullL1Low;
        private int    bullL2Bars, bullL2Ll;
        private double bullL2Low;

        // BEAR state machine
        private int    bearState;
        private int    bearLegStart;
        private double bearSwingLow;
        private bool   bearEmaTouched;
        private int    bearL1Bars, bearL1Hh;
        private double bearL1High;
        private int    bearL2Bars, bearL2Hh;
        private double bearL2High;

        // Pending entry tracking
        private int pendingLongBar  = -1;
        private int pendingShortBar = -1;

        // Daily risk
        private DateTime lastSessionDate = DateTime.MinValue;
        private double   sessionStartCashPnL;
        private int      dailyTradeCount;

        // -------------------------------------------------------------------
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name                       = "ESPullback2L";
                Description                = "2-legged pullback strategy with quality grading (mirrors v1 Pine strategy).";
                Calculate                  = Calculate.OnBarClose;
                EntriesPerDirection        = 1;
                EntryHandling              = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds  = 30;
                IsFillLimitOnTouch         = false;
                MaximumBarsLookBack        = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution        = OrderFillResolution.Standard;
                Slippage                   = 1;
                StartBehavior              = StartBehavior.WaitUntilFlat;
                TimeInForce                = TimeInForce.Gtc;
                TraceOrders                = false;
                RealtimeErrorHandling      = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling         = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade        = 50;
                IsInstantiatedOnEachOptimizationIteration = true;

                // Plot the EMA directly so it ALWAYS shows on chart - more reliable than AddChartIndicator
                AddPlot(new Stroke(Brushes.DodgerBlue, 2), PlotStyle.Line, "EMA21");
            }
            else if (State == State.DataLoaded)
            {
                emaInd = EMA(EmaLength);
                tick = TickSize;
            }
        }

        // -------------------------------------------------------------------
        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0) return;

            // Plot the EMA on the strategy's value series so it always shows
            if (DrawEma) Values[0][0] = emaInd[0];
            else        Values[0][0] = double.NaN;

            // ---- DIAGNOSTIC: faint background tint on every bar (toggle via input) ----
            if (DiagnosticTint)
                BackBrush = new SolidColorBrush(Color.FromArgb(15, 0, 255, 255));   // ~6% cyan
            else
                BackBrush = null;

            if (CurrentBar == BarsRequiredToTrade)
                Print(string.Format("[{0}] ESPullback2L FIRST BAR processed. CurrentBar={1}, EMA={2:F2}, Close={3:F2}",
                    Time[0], CurrentBar, emaInd[0], Close[0]));

            if (CurrentBar < BarsRequiredToTrade)
            {
                if (ShowStatusLabel) DrawStatusLabel("warmup", 0, 0);
                return;
            }

            // ---- Session day rollover ----
            DateTime today = Time[0].Date;
            if (today != lastSessionDate)
            {
                lastSessionDate     = today;
                sessionStartCashPnL = SystemPerformance != null ? SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit : 0;
                dailyTradeCount     = 0;
            }
            double dailyPnL = (SystemPerformance != null ? SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit : 0) - sessionStartCashPnL;

            // ---- Trend (EMA slope + price-side) ----
            bool emaUp    = emaInd[0] > emaInd[EmaSlopeLookback];
            bool emaDown  = emaInd[0] < emaInd[EmaSlopeLookback];
            bool bullTrnd = emaUp   && Close[0] > emaInd[0];
            bool bearTrnd = emaDown && Close[0] < emaInd[0];

            // ---- Run state machines ----
            bool fire2EL = UpdateBullStateMachine(bullTrnd);
            bool fire2ES = UpdateBearStateMachine(bearTrnd);

            // ---- Apply gates ----
            int bullScore = ComputeBullScore();
            int bearScore = ComputeBearScore();

            bool inSession = !RthOnly || IsRthBar();
            bool dailyOk   = dailyTradeCount < MaxDailyTrades && dailyPnL > -MaxDailyLoss;

            fire2EL = fire2EL && AllowLongs  && inSession && dailyOk
                      && bullScore >= MinQualityScore
                      && (!RequireSignalBar || IsValidLongSig());
            fire2ES = fire2ES && AllowShorts && inSession && dailyOk
                      && bearScore >= MinQualityScore
                      && (!RequireSignalBar || IsValidShortSig());

            // ---- Compute entry / stop / target ----
            double pLongEntry  = 0, pLongStop  = 0, pLongTarget  = 0;
            double pShortEntry = 0, pShortStop = 0, pShortTarget = 0;

            if (fire2EL)
            {
                pLongEntry  = High[1] + tick;
                pLongStop   = StopMode == StopType.FixedTicks
                              ? pLongEntry - StopTicks * tick
                              : (bullL2Bars > 0 ? bullL2Low : bullL1Low) - tick;
                pLongTarget = TargetMode == TargetType.Fixed1Pt ? pLongEntry + 4 * tick
                            : TargetMode == TargetType.Fixed2Pt ? pLongEntry + 8 * tick
                            : bullSwingHigh;
            }
            if (fire2ES)
            {
                pShortEntry  = Low[1] - tick;
                pShortStop   = StopMode == StopType.FixedTicks
                               ? pShortEntry + StopTicks * tick
                               : (bearL2Bars > 0 ? bearL2High : bearL1High) + tick;
                pShortTarget = TargetMode == TargetType.Fixed1Pt ? pShortEntry - 4 * tick
                             : TargetMode == TargetType.Fixed2Pt ? pShortEntry - 8 * tick
                             : bearSwingLow;
            }

            // ---- Apply min-RR filter (reject trades where target/risk < MinRrToEnter) ----
            if (fire2EL)
            {
                double risk = pLongEntry - pLongStop;
                double reward = pLongTarget - pLongEntry;
                if (risk <= 0 || reward / risk < MinRrToEnter) fire2EL = false;
            }
            if (fire2ES)
            {
                double risk = pShortStop - pShortEntry;
                double reward = pShortEntry - pShortTarget;
                if (risk <= 0 || reward / risk < MinRrToEnter) fire2ES = false;
            }

            // ---- Place orders if flat ----
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                if (fire2EL && pLongStop < pLongEntry && pLongTarget > pLongEntry)
                {
                    SetStopLoss("2EL",     CalculationMode.Price, pLongStop,   false);
                    SetProfitTarget("2EL", CalculationMode.Price, pLongTarget);
                    EnterLongStopMarket(0, true, Contracts, pLongEntry, "2EL");
                    pendingLongBar = CurrentBar;
                    dailyTradeCount++;
                    Print(string.Format("[{0}] 2EL grade {1}{2}  entry={3:F2}  stop={4:F2}  tgt={5:F2}",
                        Time[0], GradeFor(bullScore), bullScore, pLongEntry, pLongStop, pLongTarget));
                    DrawSignalGraphics(true, bullScore, pLongEntry, pLongStop, pLongTarget);
                }
                if (fire2ES && pShortStop > pShortEntry && pShortTarget < pShortEntry)
                {
                    SetStopLoss("2ES",     CalculationMode.Price, pShortStop,   false);
                    SetProfitTarget("2ES", CalculationMode.Price, pShortTarget);
                    EnterShortStopMarket(0, true, Contracts, pShortEntry, "2ES");
                    pendingShortBar = CurrentBar;
                    dailyTradeCount++;
                    Print(string.Format("[{0}] 2ES grade {1}{2}  entry={3:F2}  stop={4:F2}  tgt={5:F2}",
                        Time[0], GradeFor(bearScore), bearScore, pShortEntry, pShortStop, pShortTarget));
                    DrawSignalGraphics(false, bearScore, pShortEntry, pShortStop, pShortTarget);
                }
            }

            // ---- Cancel stale pending entries ----
            if (pendingLongBar  >= 0 && CurrentBar - pendingLongBar  > EntryExpiryBars && Position.MarketPosition == MarketPosition.Flat)
            {
                CancelWorkingOrder("2EL");
                pendingLongBar = -1;
            }
            if (pendingShortBar >= 0 && CurrentBar - pendingShortBar > EntryExpiryBars && Position.MarketPosition == MarketPosition.Flat)
            {
                CancelWorkingOrder("2ES");
                pendingShortBar = -1;
            }
            if (Position.MarketPosition != MarketPosition.Flat)
            {
                pendingLongBar  = -1;
                pendingShortBar = -1;
            }

            // ---- Leg markers (bigger so they're actually visible on /ES) ----
            if (DrawLegMarkers)
            {
                if (bullState == 1) Draw.TriangleUp(this,   "bullL1_" + CurrentBar, true, 0, Low[0]  - 4 * tick, Brushes.Cyan);
                if (bullState == 3) Draw.TriangleUp(this,   "bullL2_" + CurrentBar, true, 0, Low[0]  - 4 * tick, Brushes.Yellow);
                if (bearState == 1) Draw.TriangleDown(this, "bearL1_" + CurrentBar, true, 0, High[0] + 4 * tick, Brushes.Cyan);
                if (bearState == 3) Draw.TriangleDown(this, "bearL2_" + CurrentBar, true, 0, High[0] + 4 * tick, Brushes.Yellow);
            }

            // ---- Status label (top-right corner) ----
            if (ShowStatusLabel)
            {
                string trendStr = bullTrnd ? "BULL" : bearTrnd ? "BEAR" : "FLAT";
                DrawStatusLabel(trendStr, bullScore, bearScore);
            }

            // ---- Verbose debug print ----
            if (VerbosePrint && CurrentBar % 50 == 0)
            {
                Print(string.Format("[{0}] bar={1} bullTrnd={2} bearTrnd={3} bullState={4} bearState={5} bullScore={6} bearScore={7}",
                    Time[0], CurrentBar, bullTrnd, bearTrnd, bullState, bearState, bullScore, bearScore));
            }
        }

        // -------------------------------------------------------------------
        // Status label drawn in the top-right corner
        // -------------------------------------------------------------------
        private void DrawStatusLabel(string trendStr, int bullScore, int bearScore)
        {
            string text = string.Format(
                "ESPullback2L  |  bar={0}  |  {1}  |  bull state={2} score={3}  |  bear state={4} score={5}  |  trades today={6}",
                CurrentBar, trendStr, bullState, bullScore, bearState, bearScore, dailyTradeCount);
            Draw.TextFixed(this, "status", text, TextPosition.TopRight,
                Brushes.White, new SimpleFont("Arial", 11) { Bold = true },
                Brushes.Transparent, Brushes.Black, 30);
        }

        // -------------------------------------------------------------------
        // Bull state machine - returns true if trigger fires this bar
        // -------------------------------------------------------------------
        private bool UpdateBullStateMachine(bool bullTrnd)
        {
            bool fired = false;
            double prox = EmaProxTicks * tick;

            if (!bullTrnd)
            {
                bullState        = 0;
                bullSwingHigh    = double.NaN;
                bullEmaTouched   = false;
                bullL1Bars       = 0;  bullL1Ll = 0;  bullL1Low = double.NaN;
                bullL2Bars       = 0;  bullL2Ll = 0;  bullL2Low = double.NaN;
                return false;
            }

            bool pbContinues = High[0] <= High[1];
            bool pbBreaks    = High[0] >  High[1];

            if (bullState == 0)
            {
                if (pbContinues)
                {
                    bullState         = 1;
                    bullLegStart      = CurrentBar;
                    bullSwingHigh     = High[1];
                    bullL1Bars        = 1;
                    bullL1Ll          = Low[0] < Low[1] ? 1 : 0;
                    bullL1Low         = Low[0];
                    bullL2Bars        = 0;  bullL2Ll = 0;  bullL2Low = double.NaN;
                    bullEmaTouched    = Low[0] <= emaInd[0] + prox;
                }
            }
            else if (bullState == 1)
            {
                if (High[0] > bullSwingHigh)            bullState = 0;
                else if (CurrentBar - bullLegStart > MaxLegBars) bullState = 0;
                else if (pbBreaks)
                {
                    bullState    = 2;
                    bullLegStart = CurrentBar;
                }
                else
                {
                    bullL1Bars++;
                    if (Low[0] < Low[1]) bullL1Ll++;
                    bullL1Low = Math.Min(bullL1Low, Low[0]);
                    if (Low[0] <= emaInd[0] + prox) bullEmaTouched = true;
                }
            }
            else if (bullState == 2)
            {
                if (High[0] > bullSwingHigh) bullState = 0;
                else if (pbContinues)
                {
                    bullState    = 3;
                    bullLegStart = CurrentBar;
                    bullL2Bars   = 1;
                    bullL2Ll     = Low[0] < Low[1] ? 1 : 0;
                    bullL2Low    = Low[0];
                    if (Low[0] <= emaInd[0] + prox) bullEmaTouched = true;
                }
            }
            else if (bullState == 3)
            {
                if (High[0] > bullSwingHigh)              bullState = 0;
                else if (CurrentBar - bullLegStart > MaxLegBars) bullState = 0;
                else if (pbBreaks)
                {
                    fired      = true;
                    bullState  = 0;
                }
                else
                {
                    bullL2Bars++;
                    if (Low[0] < Low[1]) bullL2Ll++;
                    bullL2Low = Math.Min(bullL2Low, Low[0]);
                    if (Low[0] <= emaInd[0] + prox) bullEmaTouched = true;
                }
            }
            return fired;
        }

        // -------------------------------------------------------------------
        // Bear state machine (mirror)
        // -------------------------------------------------------------------
        private bool UpdateBearStateMachine(bool bearTrnd)
        {
            bool fired = false;
            double prox = EmaProxTicks * tick;

            if (!bearTrnd)
            {
                bearState        = 0;
                bearSwingLow     = double.NaN;
                bearEmaTouched   = false;
                bearL1Bars       = 0;  bearL1Hh = 0;  bearL1High = double.NaN;
                bearL2Bars       = 0;  bearL2Hh = 0;  bearL2High = double.NaN;
                return false;
            }

            bool pbContinues = Low[0] >= Low[1];
            bool pbBreaks    = Low[0] <  Low[1];

            if (bearState == 0)
            {
                if (pbContinues)
                {
                    bearState        = 1;
                    bearLegStart     = CurrentBar;
                    bearSwingLow     = Low[1];
                    bearL1Bars       = 1;
                    bearL1Hh         = High[0] > High[1] ? 1 : 0;
                    bearL1High       = High[0];
                    bearL2Bars       = 0;  bearL2Hh = 0;  bearL2High = double.NaN;
                    bearEmaTouched   = High[0] >= emaInd[0] - prox;
                }
            }
            else if (bearState == 1)
            {
                if (Low[0] < bearSwingLow)               bearState = 0;
                else if (CurrentBar - bearLegStart > MaxLegBars) bearState = 0;
                else if (pbBreaks)
                {
                    bearState    = 2;
                    bearLegStart = CurrentBar;
                }
                else
                {
                    bearL1Bars++;
                    if (High[0] > High[1]) bearL1Hh++;
                    bearL1High = Math.Max(bearL1High, High[0]);
                    if (High[0] >= emaInd[0] - prox) bearEmaTouched = true;
                }
            }
            else if (bearState == 2)
            {
                if (Low[0] < bearSwingLow) bearState = 0;
                else if (pbContinues)
                {
                    bearState    = 3;
                    bearLegStart = CurrentBar;
                    bearL2Bars   = 1;
                    bearL2Hh     = High[0] > High[1] ? 1 : 0;
                    bearL2High   = High[0];
                    if (High[0] >= emaInd[0] - prox) bearEmaTouched = true;
                }
            }
            else if (bearState == 3)
            {
                if (Low[0] < bearSwingLow)               bearState = 0;
                else if (CurrentBar - bearLegStart > MaxLegBars) bearState = 0;
                else if (pbBreaks)
                {
                    fired     = true;
                    bearState = 0;
                }
                else
                {
                    bearL2Bars++;
                    if (High[0] > High[1]) bearL2Hh++;
                    bearL2High = Math.Max(bearL2High, High[0]);
                    if (High[0] >= emaInd[0] - prox) bearEmaTouched = true;
                }
            }
            return fired;
        }

        // -------------------------------------------------------------------
        // Quality scoring
        // -------------------------------------------------------------------
        private int ComputeBullScore()
        {
            double l1Mom = bullL1Bars > 0 ? (double)bullL1Ll / bullL1Bars : 0;
            double l2Mom = bullL2Bars > 0 ? (double)bullL2Ll / bullL2Bars : 0;
            int sL1 = (int)Math.Round(l1Mom * 3);
            int sL2 = (int)Math.Round(l2Mom * 3);
            int sDepth = 0;
            if (!double.IsNaN(bullL2Low) && !double.IsNaN(bullL1Low))
            {
                if (bullL2Low > bullL1Low)                              sDepth = 3;
                else if (bullL2Low >= bullL1Low - 2 * tick)             sDepth = 1;
            }
            int sBars = (bullL1Bars >= 2 && bullL2Bars >= 2) ? 1 : 0;
            int score = sL1 + sL2 + sDepth + sBars;

            // Depth gate (combined with score)
            double pbLowOverall = bullL2Bars > 0
                ? Math.Min(bullL1Low, bullL2Low)
                : bullL1Low;
            if (double.IsNaN(bullSwingHigh) || double.IsNaN(pbLowOverall) ||
                (bullSwingHigh - pbLowOverall) < MinPullbackTicks * tick)
                return -1;  // not eligible
            if (!bullEmaTouched) return -1;
            return score;
        }

        private int ComputeBearScore()
        {
            double l1Mom = bearL1Bars > 0 ? (double)bearL1Hh / bearL1Bars : 0;
            double l2Mom = bearL2Bars > 0 ? (double)bearL2Hh / bearL2Bars : 0;
            int sL1 = (int)Math.Round(l1Mom * 3);
            int sL2 = (int)Math.Round(l2Mom * 3);
            int sDepth = 0;
            if (!double.IsNaN(bearL2High) && !double.IsNaN(bearL1High))
            {
                if (bearL2High < bearL1High)                            sDepth = 3;
                else if (bearL2High <= bearL1High + 2 * tick)           sDepth = 1;
            }
            int sBars = (bearL1Bars >= 2 && bearL2Bars >= 2) ? 1 : 0;
            int score = sL1 + sL2 + sDepth + sBars;

            double pbHighOverall = bearL2Bars > 0
                ? Math.Max(bearL1High, bearL2High)
                : bearL1High;
            if (double.IsNaN(bearSwingLow) || double.IsNaN(pbHighOverall) ||
                (pbHighOverall - bearSwingLow) < MinPullbackTicks * tick)
                return -1;
            if (!bearEmaTouched) return -1;
            return score;
        }

        // -------------------------------------------------------------------
        // Helpers
        // -------------------------------------------------------------------
        private bool IsRthBar()
        {
            DateTime t = Time[0];
            if (t.DayOfWeek == DayOfWeek.Saturday || t.DayOfWeek == DayOfWeek.Sunday) return false;
            int mins = t.Hour * 60 + t.Minute;
            return mins >= 9 * 60 + 30 && mins < 16 * 60;
        }

        private bool IsValidLongSig()
        {
            double r = High[0] - Low[0];
            if (r <= 0) return false;
            double body = Math.Abs(Close[0] - Open[0]);
            if (body < MinBodyFraction * r) return false;
            if (Close[0] <= Open[0]) return false;
            double cs = (Close[0] - Low[0]) / r;
            return cs >= MinCloseStrength;
        }

        private bool IsValidShortSig()
        {
            double r = High[0] - Low[0];
            if (r <= 0) return false;
            double body = Math.Abs(Close[0] - Open[0]);
            if (body < MinBodyFraction * r) return false;
            if (Close[0] >= Open[0]) return false;
            double cs = (High[0] - Close[0]) / r;
            return cs >= MinCloseStrength;
        }

        private string GradeFor(int score)
        {
            if (score >= 8) return "A";
            if (score >= 6) return "B";
            if (score >= 4) return "C";
            return "D";
        }

        private void CancelWorkingOrder(string signalName)
        {
            foreach (Order o in Orders)
            {
                if (o.Name == signalName && (o.OrderState == OrderState.Working || o.OrderState == OrderState.Accepted))
                {
                    CancelOrder(o);
                }
            }
        }

        // -------------------------------------------------------------------
        // Big, impossible-to-miss signal graphics:
        //   1. Bright arrow above/below the trigger bar
        //   2. Bar-background highlight (orange-tinted) on the trigger bar
        //   3. Grade label with R/R ratio
        //   4. Entry / stop / target lines (thick, bright)
        // -------------------------------------------------------------------
        private void DrawSignalGraphics(bool isLong, int score, double entry, double stop, double target)
        {
            string tag    = (isLong ? "2EL_" : "2ES_") + CurrentBar;
            string grade  = GradeFor(score);
            double risk   = Math.Abs(entry - stop);
            double reward = Math.Abs(target - entry);
            double rr     = risk > 0 ? reward / risk : 0;
            string labelText = string.Format("{0} {1}{2}  R={3:F1}t  T={4:F1}t  RR=1:{5:F2}",
                isLong ? "2EL" : "2ES", grade, score, risk / tick, reward / tick, rr);

            Brush mainBrush = isLong ? Brushes.Lime : Brushes.OrangeRed;

            // 1. BackBrushes paints the trigger bar's background
            BackBrushes[0] = new SolidColorBrush(isLong ? Color.FromArgb(80, 0, 255, 0) : Color.FromArgb(80, 255, 80, 0));

            // 2. BIG arrow
            if (isLong)
                Draw.ArrowUp(this,   tag + "_arrow", true, 0, Low[0]  - 6 * tick, mainBrush);
            else
                Draw.ArrowDown(this, tag + "_arrow", true, 0, High[0] + 6 * tick, mainBrush);

            // 3. Grade label
            Draw.Text(this, tag + "_label", labelText, 0,
                isLong ? Low[0] - 14 * tick : High[0] + 14 * tick,
                mainBrush);

            // 4. Entry / stop / target lines (thicker than before)
            if (DrawEntryLines)
            {
                int extendTo = -LineExtendBars;
                Draw.Line(this, tag + "_e", false, 0, entry,  extendTo, entry,  Brushes.White,  DashStyleHelper.Dash, 2);
                Draw.Line(this, tag + "_s", false, 0, stop,   extendTo, stop,   Brushes.Red,    DashStyleHelper.Solid, 2);
                Draw.Line(this, tag + "_t", false, 0, target, extendTo, target, Brushes.Lime,   DashStyleHelper.Solid, 2);

                // Price tag on right side of each line
                Draw.Text(this, tag + "_e_p", "ENTRY " + entry.ToString("F2"),  extendTo, entry,  Brushes.White);
                Draw.Text(this, tag + "_s_p", "STOP "  + stop.ToString("F2"),   extendTo, stop,   Brushes.Red);
                Draw.Text(this, tag + "_t_p", "TGT "   + target.ToString("F2"), extendTo, target, Brushes.Lime);
            }
        }
    }
}

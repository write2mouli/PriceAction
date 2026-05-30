# ES Price Action — thinkScript study
# 2EL / 2ES / F2EL / F2ES / Failed Breakout / HL-LH on /ES 2000 tick.
# Mirrors docs/strategy-spec.md. thinkScript can't trade or backtest custom logic,
# so this is a visual study with alert conditions. Use ../python/ or ../ninjatrader/
# for the actual backtest.

declare upper;

# =====================================================================
# Inputs
# =====================================================================
input emaLength            = 21;
input atrLength            = 14;
input swingStrength        = 3;
input emaProximityTicks    = 4;
input minCloseStrength     = 0.60;
input minBodyFraction      = 0.40;
input maxSignalRangeAtr    = 2.5;
input minPullbackTicks     = 8;
input maxPullbackBars      = 15;
input trendBarsAboveEma    = 14;
input tlBreakTicks         = 4;
input tlTestWindow         = 20;
input rangeLookback        = 30;
input rangeBandTicks       = 6;
input failBreakoutMaxTicks = 20;
input failBreakoutWindow   = 5;
input f2eReversalBars      = 2;

input show2EL          = yes;
input show2ES          = yes;
input showF2EL         = yes;
input showF2ES         = yes;
input showFailedBO     = yes;
input showHLLH         = yes;
input showEMA          = yes;
input showSignalLabels = yes;

input rthOnly          = yes;

# Regime gates (§7.5, §13.5)
input minPullbackDepthFrac = 0.30;
input newExtremeMinTicks   = 4;
input newExtremeCooldown   = 5;
input midRangeLowFrac      = 0.40;
input midRangeHighFrac     = 0.60;
input enableCongestion     = yes;
input congestionLookback   = 10;
input congestionMaxRange   = 16;
input congestionMaxDrift   = 8;

input fbRequirePbConfirm   = yes;
input fbPullbackWindow     = 8;
input fbLhHlMinTicks       = 4;

# =====================================================================
# Helpers
# =====================================================================
def tick = TickSize();
def ema  = ExpAverage(close, emaLength);
def atrv = WildersAverage(TrueRange(high, close, low), atrLength);

plot EMA21 = if showEMA then ema else Double.NaN;
EMA21.SetDefaultColor(GetColor(8));
EMA21.SetLineWeight(1);

# bar fundamentals
def isBull = close > open;
def isBear = close < open;
def body   = AbsValue(close - open);
def rng    = high - low;

# Close strength
def csBull = if rng > 0 then (close - low) / rng else 0;
def csBear = if rng > 0 then (high - close) / rng else 0;

# Signal bar validation
def signalLongOK  = isBull and rng > 0
                    and (atrv == 0 or rng <= maxSignalRangeAtr * atrv)
                    and (body >= minBodyFraction * rng)
                    and (csBull >= minCloseStrength);
def signalShortOK = isBear and rng > 0
                    and (atrv == 0 or rng <= maxSignalRangeAtr * atrv)
                    and (body >= minBodyFraction * rng)
                    and (csBear >= minCloseStrength);

# RTH gating (ET, 9:30–16:00)
def inSession = if !rthOnly then yes
                else (SecondsFromTime(0930) >= 0 and SecondsTillTime(1600) > 0);

# Congestion filter (§7.5)
def congRange = Highest(high, congestionLookback) - Lowest(low, congestionLookback);
def congDrift = AbsValue(close - close[congestionLookback]);
def inCongestion = enableCongestion
                   and congRange <= congestionMaxRange * tick
                   and congDrift <= congestionMaxDrift * tick;

# New-extreme cooldown (§13.5.4)
def newExtremeLong  = high == Highest(high, swingStrength + 1) and high > high[swingStrength + 1] + newExtremeMinTicks * tick;
def newExtremeShort = low  == Lowest(low,  swingStrength + 1) and low  < low[swingStrength + 1]  - newExtremeMinTicks * tick;
def barsSinceNewExtremeLong  = if newExtremeLong then 0
                               else if !IsNaN(barsSinceNewExtremeLong[1]) then barsSinceNewExtremeLong[1] + 1
                               else 9999;
def barsSinceNewExtremeShort = if newExtremeShort then 0
                               else if !IsNaN(barsSinceNewExtremeShort[1]) then barsSinceNewExtremeShort[1] + 1
                               else 9999;
def newExtremeBlockLong  = barsSinceNewExtremeLong  < newExtremeCooldown;
def newExtremeBlockShort = barsSinceNewExtremeShort < newExtremeCooldown;

# =====================================================================
# Swing pivots — confirmed `swingStrength` bars later
# =====================================================================
# A swing high at bar i is detected at bar i + swingStrength.
# We mark it on the current bar if high[swingStrength] is greater than its k neighbors.
def swingHighConfirmed =
    high[swingStrength] > Highest(high[swingStrength + 1], swingStrength) and
    high[swingStrength] > Highest(high, swingStrength);
def swingLowConfirmed =
    low[swingStrength] < Lowest(low[swingStrength + 1], swingStrength) and
    low[swingStrength] < Lowest(low, swingStrength);

# Cache last two confirmed swing high prices and bars-ago they occurred
def shPrice = if swingHighConfirmed then high[swingStrength] else shPrice[1];
def shBarsAgo = if swingHighConfirmed then swingStrength
                else if !IsNaN(shBarsAgo[1]) then shBarsAgo[1] + 1 else Double.NaN;
def shPrevPrice = if swingHighConfirmed then shPrice[1] else shPrevPrice[1];
def shPrevBarsAgo = if swingHighConfirmed then shBarsAgo[1]
                    else if !IsNaN(shPrevBarsAgo[1]) then shPrevBarsAgo[1] + 1 else Double.NaN;

def slPrice = if swingLowConfirmed then low[swingStrength] else slPrice[1];
def slBarsAgo = if swingLowConfirmed then swingStrength
                else if !IsNaN(slBarsAgo[1]) then slBarsAgo[1] + 1 else Double.NaN;
def slPrevPrice = if swingLowConfirmed then slPrice[1] else slPrevPrice[1];
def slPrevBarsAgo = if swingLowConfirmed then slBarsAgo[1]
                    else if !IsNaN(slPrevBarsAgo[1]) then slPrevBarsAgo[1] + 1 else Double.NaN;

# =====================================================================
# Trend classification
# =====================================================================
def aboveCnt = fold a = 0 to 20 with cnt = 0 do
    cnt + (if GetValue(close, a) > GetValue(ema, a) then 1 else 0);
def belowCnt = fold b = 0 to 20 with cnt2 = 0 do
    cnt2 + (if GetValue(close, b) < GetValue(ema, b) then 1 else 0);

def hh_hl = !IsNaN(shPrice) and !IsNaN(shPrevPrice) and !IsNaN(slPrice) and !IsNaN(slPrevPrice)
            and shPrice > shPrevPrice and slPrice > slPrevPrice;
def ll_lh = !IsNaN(shPrice) and !IsNaN(shPrevPrice) and !IsNaN(slPrice) and !IsNaN(slPrevPrice)
            and shPrice < shPrevPrice and slPrice < slPrevPrice;

# Trendline break approximation (line connecting last two confirmed swing lows / highs)
def bullSlope = if !IsNaN(slPrice) and !IsNaN(slPrevPrice) and (slPrevBarsAgo - slBarsAgo) > 0
                then (slPrice - slPrevPrice) / (slPrevBarsAgo - slBarsAgo)
                else 0;
def bullTLNow = if !IsNaN(slPrice) then slPrice + bullSlope * slBarsAgo else Double.NaN;
def bearSlope = if !IsNaN(shPrice) and !IsNaN(shPrevPrice) and (shPrevBarsAgo - shBarsAgo) > 0
                then (shPrice - shPrevPrice) / (shPrevBarsAgo - shBarsAgo)
                else 0;
def bearTLNow = if !IsNaN(shPrice) then shPrice + bearSlope * shBarsAgo else Double.NaN;

def bullTLBroken = !IsNaN(bullTLNow) and close < bullTLNow - tlBreakTicks * tick;
def bearTLBroken = !IsNaN(bearTLNow) and close > bearTLNow + tlBreakTicks * tick;

def barsSinceBullBreak = if bullTLBroken then 0
                         else if !IsNaN(barsSinceBullBreak[1]) then barsSinceBullBreak[1] + 1
                         else Double.NaN;
def barsSinceBearBreak = if bearTLBroken then 0
                         else if !IsNaN(barsSinceBearBreak[1]) then barsSinceBearBreak[1] + 1
                         else Double.NaN;

def bullTLActive = !IsNaN(bullTLNow) and !bullTLBroken
                   and (IsNaN(barsSinceBullBreak) or barsSinceBullBreak > tlTestWindow);
def bearTLActive = !IsNaN(bearTLNow) and !bearTLBroken
                   and (IsNaN(barsSinceBearBreak) or barsSinceBearBreak > tlTestWindow);

def bullTrend = aboveCnt >= trendBarsAboveEma and hh_hl and bullTLActive;
def bearTrend = belowCnt >= trendBarsAboveEma and ll_lh and bearTLActive;

# =====================================================================
# Trading range (simplified)
# =====================================================================
def hiN = Highest(high, rangeLookback);
def loN = Lowest(low, rangeLookback);
def band = rangeBandTicks * tick;
def rangeActive = !bullTrend and !bearTrend
                  and (hiN - loN) <= 30 * tick * 4
                  and !IsNaN(shPrice) and !IsNaN(slPrice)
                  and AbsValue(shPrice - hiN) <= band
                  and AbsValue(slPrice - loN) <= band;

# =====================================================================
# KEPs (EMA / trendline only — SR is implicit via range)
# =====================================================================
def atEmaLong  = low <= ema + emaProximityTicks * tick;
def atEmaShort = high >= ema - emaProximityTicks * tick;
def atTLLong   = bullTLActive and low <= bullTLNow + tlBreakTicks * tick
                                  and close >= bullTLNow - tlBreakTicks * tick;
def atTLShort  = bearTLActive and high >= bearTLNow - tlBreakTicks * tick
                                  and close <= bearTLNow + tlBreakTicks * tick;
def atKepLong  = atEmaLong or atTLLong;
def atKepShort = atEmaShort or atTLShort;

# =====================================================================
# Pullback tracking — simplified H1/H2 / L1/L2 using local extremes
# (Exact spec H2 chain requires causal state which thinkScript can't perfectly
#  model; we approximate: a "pullback to EMA + 2 lower-low-then-higher-high
#  pattern" qualifies as 2EL.)
# =====================================================================
def bullPullbackBar = bullTrend and low <= ema + emaProximityTicks * tick;
def bearPullbackBar = bearTrend and high >= ema - emaProximityTicks * tick;

# Track the lowest low / highest high of the recent pullback
def pbLowSinceTouch = if bullPullbackBar then low
                      else if !IsNaN(pbLowSinceTouch[1]) and bullTrend then Min(pbLowSinceTouch[1], low)
                      else Double.NaN;
def pbHighSinceTouch = if bearPullbackBar then high
                       else if !IsNaN(pbHighSinceTouch[1]) and bearTrend then Max(pbHighSinceTouch[1], high)
                       else Double.NaN;

# 2EL: bull trend + pullback touched EMA in last `maxPullbackBars` + current bar
# makes a higher high than previous bar (H2 approximation) + signal-bar valid
def emaTouchedRecently_L = !IsNaN(pbLowSinceTouch);
def h2_proxy_L = high > high[1] and high[1] < high[2] and high[2] < high[3];
def midRangeBlockLong  = rangeActive and (close - loN) / (hiN - loN) > midRangeHighFrac;
def midRangeBlockShort = rangeActive and (close - loN) / (hiN - loN) < midRangeLowFrac;

def setup_2EL = show2EL and bullTrend and signalLongOK and atKepLong
               and emaTouchedRecently_L and h2_proxy_L
               and (high - pbLowSinceTouch) >= minPullbackTicks * tick
               and inSession and !inCongestion
               and !newExtremeBlockLong and !midRangeBlockLong;

def emaTouchedRecently_S = !IsNaN(pbHighSinceTouch);
def l2_proxy_S = low < low[1] and low[1] > low[2] and low[2] > low[3];
def setup_2ES = show2ES and bearTrend and signalShortOK and atKepShort
               and emaTouchedRecently_S and l2_proxy_S
               and (pbHighSinceTouch - low) >= minPullbackTicks * tick
               and inSession and !inCongestion
               and !newExtremeBlockShort and !midRangeBlockShort;

# F2EL: in a bull trend, prior bar (within f2eReversalBars) was a valid bear signal bar
# that didn't follow through (low didn't break below it - 1 tick) and now we have
# a valid bull signal bar at a KEP.
def priorBearSig1 = signalShortOK[1];
def priorBearSig2 = signalShortOK[2];
def lowBrokeRecent_L = (Lowest(low, f2eReversalBars + 1) < low[f2eReversalBars + 1] - tick);
def setup_F2EL = showF2EL and bullTrend and bullTLActive and signalLongOK and atKepLong
                 and (priorBearSig1 or priorBearSig2)
                 and !lowBrokeRecent_L
                 and inSession and !inCongestion
                 and !newExtremeBlockLong and !midRangeBlockLong;

def priorBullSig1 = signalLongOK[1];
def priorBullSig2 = signalLongOK[2];
def highBrokeRecent_S = (Highest(high, f2eReversalBars + 1) > high[f2eReversalBars + 1] + tick);
def setup_F2ES = showF2ES and bearTrend and bearTLActive and signalShortOK and atKepShort
                 and (priorBullSig1 or priorBullSig2)
                 and !highBrokeRecent_S
                 and inSession and !inCongestion
                 and !newExtremeBlockShort and !midRangeBlockShort;

# Failed Breakout — immediate variant + pullback-confirmation variant
def loExWin = Lowest(low, failBreakoutWindow);
def hiExWin = Highest(high, failBreakoutWindow);

# Immediate FB
def setup_FB_LONG_imm  = !fbRequirePbConfirm and showFailedBO and rangeActive and signalLongOK
                        and loExWin < loN
                        and (loN - loExWin) <= failBreakoutMaxTicks * tick
                        and close > loN
                        and inSession and !inCongestion;
def setup_FB_SHORT_imm = !fbRequirePbConfirm and showFailedBO and rangeActive and signalShortOK
                        and hiExWin > hiN
                        and (hiExWin - hiN) <= failBreakoutMaxTicks * tick
                        and close < hiN
                        and inSession and !inCongestion;

# Armed-confirm FB (FB_PB): only fire if HL/LH structure forms after a failed BO excursion
def fbLongArmedBar = if rangeActive and loExWin < loN and (loN - loExWin) <= failBreakoutMaxTicks * tick and close > loN
                     then 0
                     else if !IsNaN(fbLongArmedBar[1]) and fbLongArmedBar[1] < fbPullbackWindow then fbLongArmedBar[1] + 1 else 9999;
def fbLongArmedExc = if fbLongArmedBar == 0 then loExWin else fbLongArmedExc[1];

def fbShortArmedBar = if rangeActive and hiExWin > hiN and (hiExWin - hiN) <= failBreakoutMaxTicks * tick and close < hiN
                      then 0
                      else if !IsNaN(fbShortArmedBar[1]) and fbShortArmedBar[1] < fbPullbackWindow then fbShortArmedBar[1] + 1 else 9999;
def fbShortArmedExc = if fbShortArmedBar == 0 then hiExWin else fbShortArmedExc[1];

def setup_FB_LONG_pb  = fbRequirePbConfirm and showFailedBO and rangeActive
                       and fbLongArmedBar > 0 and fbLongArmedBar <= fbPullbackWindow
                       and low > fbLongArmedExc + fbLhHlMinTicks * tick
                       and low > low[1]
                       and signalLongOK and inSession and !inCongestion;
def setup_FB_SHORT_pb = fbRequirePbConfirm and showFailedBO and rangeActive
                       and fbShortArmedBar > 0 and fbShortArmedBar <= fbPullbackWindow
                       and high < fbShortArmedExc - fbLhHlMinTicks * tick
                       and high < high[1]
                       and signalShortOK and inSession and !inCongestion;

def setup_FB_LONG  = setup_FB_LONG_imm  or setup_FB_LONG_pb;
def setup_FB_SHORT = setup_FB_SHORT_imm or setup_FB_SHORT_pb;

# HL/LH reversal: recent TL break + new HL or LH
def setup_HLLH_LONG = showHLLH and !IsNaN(barsSinceBearBreak) and barsSinceBearBreak <= tlTestWindow
                      and !IsNaN(slPrice) and !IsNaN(slPrevPrice) and slPrice > slPrevPrice
                      and signalLongOK and inSession and !inCongestion;
def setup_HLLH_SHORT = showHLLH and !IsNaN(barsSinceBullBreak) and barsSinceBullBreak <= tlTestWindow
                       and !IsNaN(shPrice) and !IsNaN(shPrevPrice) and shPrice < shPrevPrice
                       and signalShortOK and inSession and !inCongestion;

# =====================================================================
# Visual markers
# =====================================================================
plot sig_2EL  = if setup_2EL  then low  - 2 * tick else Double.NaN;
plot sig_2ES  = if setup_2ES  then high + 2 * tick else Double.NaN;
plot sig_F2EL = if setup_F2EL then low  - 2 * tick else Double.NaN;
plot sig_F2ES = if setup_F2ES then high + 2 * tick else Double.NaN;
plot sig_FBL  = if setup_FB_LONG  then low  - 2 * tick else Double.NaN;
plot sig_FBS  = if setup_FB_SHORT then high + 2 * tick else Double.NaN;
plot sig_HLL  = if setup_HLLH_LONG  then low  - 2 * tick else Double.NaN;
plot sig_HLS  = if setup_HLLH_SHORT then high + 2 * tick else Double.NaN;

sig_2EL.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
sig_2EL.SetDefaultColor(Color.GREEN);
sig_2EL.SetLineWeight(3);

sig_2ES.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
sig_2ES.SetDefaultColor(Color.RED);
sig_2ES.SetLineWeight(3);

sig_F2EL.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
sig_F2EL.SetDefaultColor(Color.YELLOW);
sig_F2EL.SetLineWeight(3);

sig_F2ES.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
sig_F2ES.SetDefaultColor(Color.ORANGE);
sig_F2ES.SetLineWeight(3);

sig_FBL.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
sig_FBL.SetDefaultColor(Color.CYAN);
sig_FBL.SetLineWeight(3);

sig_FBS.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
sig_FBS.SetDefaultColor(Color.MAGENTA);
sig_FBS.SetLineWeight(3);

sig_HLL.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
sig_HLL.SetDefaultColor(Color.LIGHT_GREEN);
sig_HLL.SetLineWeight(3);

sig_HLS.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
sig_HLS.SetDefaultColor(Color.PINK);
sig_HLS.SetLineWeight(3);

# Setup labels
AddChartBubble(showSignalLabels and setup_2EL,  low,  "2EL",  Color.GREEN,  no);
AddChartBubble(showSignalLabels and setup_2ES,  high, "2ES",  Color.RED,    yes);
AddChartBubble(showSignalLabels and setup_F2EL, low,  "F2EL", Color.YELLOW, no);
AddChartBubble(showSignalLabels and setup_F2ES, high, "F2ES", Color.ORANGE, yes);
AddChartBubble(showSignalLabels and setup_FB_LONG,  low,  "FB", Color.CYAN, no);
AddChartBubble(showSignalLabels and setup_FB_SHORT, high, "FB", Color.MAGENTA, yes);
AddChartBubble(showSignalLabels and setup_HLLH_LONG,  low,  "HL",  Color.LIGHT_GREEN, no);
AddChartBubble(showSignalLabels and setup_HLLH_SHORT, high, "LH",  Color.PINK, yes);

# Status labels
AddLabel(yes, if inCongestion then "CONGEST"
              else if bullTrend then "BULL"
              else if bearTrend then "BEAR"
              else if rangeActive then "RANGE"
              else "—",
         if inCongestion then Color.YELLOW
         else if bullTrend then Color.GREEN
         else if bearTrend then Color.RED
         else Color.GRAY);

AddLabel(rangeActive,
         "Range " + AsText(loN) + "–" + AsText(hiN),
         Color.CYAN);

# Alerts
Alert(setup_2EL,         "2EL signal",  Alert.BAR, Sound.Ring);
Alert(setup_2ES,         "2ES signal",  Alert.BAR, Sound.Ring);
Alert(setup_F2EL,        "F2EL signal", Alert.BAR, Sound.Bell);
Alert(setup_F2ES,        "F2ES signal", Alert.BAR, Sound.Bell);
Alert(setup_FB_LONG,     "Failed BO long",  Alert.BAR, Sound.Chimes);
Alert(setup_FB_SHORT,    "Failed BO short", Alert.BAR, Sound.Chimes);
Alert(setup_HLLH_LONG,   "HL reversal long",  Alert.BAR, Sound.Ding);
Alert(setup_HLLH_SHORT,  "LH reversal short", Alert.BAR, Sound.Ding);

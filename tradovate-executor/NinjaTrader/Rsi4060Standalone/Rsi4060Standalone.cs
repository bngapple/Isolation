/*
 * Rsi4060Standalone.cs
 *
 * Native NinjaTrader 8 strategy for the current strongest RSI-only variant found in this repo:
 * - RSI period: 4
 * - Oversold: 40
 * - Overbought: 60
 * - No VWAP filter
 * - No ATR regime filter
 * - No Python bridge
 *
 * Intended runtime shape:
 * - Attach to an MNQ 15-minute chart
 * - Use a regular-hours session template that includes the 16:30 bar and 16:45 flatten window
 * - One position at a time
 * - Next-bar execution from OnBarClose signals
 *
 * Backtest-aligned defaults from the repo's RSI search:
 * - Stop: 10.0 points
 * - Target: 100.0 points
 * - Max hold: 5 bars
 * - Default quantity: 1 contract
 */

#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class Rsi4060Standalone : Strategy
    {
        private const string EntrySignalName = "RSI40_60";

        private RSI rsi;

        [NinjaScriptProperty]
        [Range(2, 50)]
        [Display(Name = "Contracts", GroupName = "Parameters", Order = 1)]
        public int Contracts { get; set; }

        [NinjaScriptProperty]
        [Range(2, 20)]
        [Display(Name = "RSI Period", GroupName = "Parameters", Order = 2)]
        public int RsiPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(1, 99)]
        [Display(Name = "Oversold", GroupName = "Parameters", Order = 3)]
        public double Oversold { get; set; }

        [NinjaScriptProperty]
        [Range(1, 99)]
        [Display(Name = "Overbought", GroupName = "Parameters", Order = 4)]
        public double Overbought { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Stop Loss Ticks", GroupName = "Risk", Order = 5)]
        public int StopLossTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 2000)]
        [Display(Name = "Profit Target Ticks", GroupName = "Risk", Order = 6)]
        public int ProfitTargetTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Max Hold Bars", GroupName = "Risk", Order = 7)]
        public int MaxHoldBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Session Start HHMMSS", GroupName = "Session", Order = 8)]
        public int SessionStartTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Last Entry HHMMSS", GroupName = "Session", Order = 9)]
        public int LastEntryTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Flatten HHMMSS", GroupName = "Session", Order = 10)]
        public int FlattenTime { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                  = "Native NinjaTrader port of the repo's RSI(4) 40/60 standalone strategy.";
                Name                         = "Rsi4060Standalone";
                Calculate                    = Calculate.OnBarClose;
                EntriesPerDirection          = 1;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                IsOverlay                    = false;
                BarsRequiredToTrade          = 10;

                Contracts         = 1;
                RsiPeriod         = 4;
                Oversold          = 40;
                Overbought        = 60;
                StopLossTicks     = 40;   // 10.0 points on MNQ
                ProfitTargetTicks = 400;  // 100.0 points on MNQ
                MaxHoldBars       = 5;
                SessionStartTime  = 93000;
                LastEntryTime     = 163000;
                FlattenTime       = 164500;
            }
            else if (State == State.Configure)
            {
                SetStopLoss(EntrySignalName, CalculationMode.Ticks, StopLossTicks, false);
                SetProfitTarget(EntrySignalName, CalculationMode.Ticks, ProfitTargetTicks);
            }
            else if (State == State.DataLoaded)
            {
                // Smooth = 1 keeps the output close to the simple signal form used in the Python search.
                rsi = RSI(Close, RsiPeriod, 1);
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            if (CurrentBar < BarsRequiredToTrade || rsi == null)
                return;

            int now = ToTime(Time[0]);

            // Flatten before looking for any new action so the strategy cannot carry overnight.
            if (Position.MarketPosition != MarketPosition.Flat && now >= FlattenTime)
            {
                FlattenOpenPosition("EOD");
                return;
            }

            if (Position.MarketPosition != MarketPosition.Flat)
            {
                int barsSinceEntry = BarsSinceEntryExecution(0, EntrySignalName, 0);
                if (barsSinceEntry >= 0 && barsSinceEntry >= MaxHoldBars)
                {
                    FlattenOpenPosition("MaxHold");
                }
                return;
            }

            if (now < SessionStartTime || now >= LastEntryTime)
                return;

            double rsiValue = rsi[0];
            if (double.IsNaN(rsiValue))
                return;

            if (rsiValue < Oversold)
            {
                EnterLong(Contracts, EntrySignalName);
            }
            else if (rsiValue > Overbought)
            {
                EnterShort(Contracts, EntrySignalName);
            }
        }

        private void FlattenOpenPosition(string reason)
        {
            if (Position.MarketPosition == MarketPosition.Long)
            {
                ExitLong(reason, EntrySignalName);
            }
            else if (Position.MarketPosition == MarketPosition.Short)
            {
                ExitShort(reason, EntrySignalName);
            }
        }
    }
}
